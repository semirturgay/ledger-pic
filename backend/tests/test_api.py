import asyncio
import base64
import datetime as dt
import json
import os
import sys
import time
import uuid
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import jwt
import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.config import settings
from app.core.errors import CaptchaFailed, UnsupportedImage
from app.core.rate_limit import RateLimiter
from app.db.session import SessionLocal, engine
from app.main import app
from app.models import Prompt, PromptType, ReceiptImage, User
from app.services import auth_service, images
from app.services.captcha import verify_captcha
from app.services.extraction import ExtractedLineItem, NoLongerWanted, ReceiptExtractor, _as_json
from app.services.images import MAX_JPEG_SCANS, jpeg_scan_count, normalize_image
from app.services.llm import ExtractionFailed
from app.services.llm.claude import ClaudeVision
from app.services.llm.gemini import GeminiVision, retry_delay_seconds
from app.services.prompts import prompt_store
from app.services.receipt_service import encode_cursor
from app.workers.extraction_job import prune_usage_log, purge_rejected_uploads, run_due_jobs, run_job
from app.workers.queue import Job, claim_job, lease_duration, retry_delay, utcnow
from app.workers.runner import Worker
from tests.conftest import FakeExtractor, auth_headers, get, make_image, new_receipt, upload

ANSWER = {
    "is_receipt": True,
    "merchant": "Corner Market",
    "date": "2026-10-03",
    "currency": "usd",
    "category": "groceries",
    "line_items": [
        {"description": "Milk", "quantity": 2, "unit_price": 1.99},
        {"description": "Bread", "quantity": 1, "unit_price": 3.5},
    ],
    "tax": 0.75,
    "total": 8.23,
}
GOOD = json.dumps(ANSWER)
CONFIRMED = {
    "merchant": "Corner Market",
    "purchased_at": "2026-10-03",
    "currency": "USD",
    "category": "groceries",
    "tax": "0.75",
    "total": "8.23",
    "line_items": [
        {"description": "Milk", "quantity": "2", "unit_price": "1.99"},
        {"description": "Bread", "quantity": "1", "unit_price": "3.50"},
    ],
}
BUSY = ExtractionFailed.busy()
LATER = dt.timedelta(hours=1)  # past any backoff delay


def answer(**overrides) -> str:
    return json.dumps({**ANSWER, **overrides})


async def put(client, headers, receipt_id, body=CONFIRMED):
    return await client.put(f"/receipts/{receipt_id}", headers=headers, json=body)




async def test_auth_required_login_and_rate_limit(client, monkeypatch):
    assert (await client.get("/receipts")).status_code == 401
    assert (await client.get("/health", headers={"X-Request-ID": "abc-123"})).headers["X-Request-ID"] == "abc-123"
    assert (await client.get("/receipts", headers={"Authorization": "Bearer garbage"})).status_code == 401
    await auth_headers(client)
    login = {"email": "ALICE@example.com", "password": "correct-horse"}  # emails are case-insensitive
    assert (await client.post("/auth/login", json=login)).status_code == 200
    assert (await client.post("/auth/login", json={**login, "password": "wrong"})).status_code == 401

    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 3)
    assert (await client.post("/auth/login", json=login)).status_code == 429  # register + 2 logins used all 3




async def test_upload_returns_immediately_and_worker_fills_draft(client, alice, run_jobs):
    created = (await upload(client, alice)).json()
    assert (created["status"], created["extraction_attempts"]) == ("processing", 0)  # queued, not started

    await run_jobs(GOOD)

    receipt = await get(client, alice, created["id"])
    assert receipt["status"] == "needs_review"
    assert (receipt["merchant"], receipt["currency"]) == ("Corner Market", "USD")
    assert (receipt["total"], receipt["line_items_total"]) == ("8.23", "7.48")  # money as strings
    assert receipt["totals_mismatch"] is False
    assert (receipt["extraction_error"], receipt["extraction_attempts"], receipt["prompt_version"]) == (None, 1, 1)


async def test_queue_processes_several_uploads(client, alice, run_jobs):
    ids = [await new_receipt(client, alice) for _ in range(3)]
    await run_jobs(GOOD, GOOD, GOOD)
    assert {(await get(client, alice, i))["status"] for i in ids} == {"needs_review"}


async def test_malformed_output_is_retried_with_errors(client, alice, run_jobs):
    receipt_id = await new_receipt(client, alice)
    prompts = (await run_jobs("this is not json", GOOD)).prompts
    assert len(prompts) == 2 and "did not pass validation" in prompts[1]
    receipt = await get(client, alice, receipt_id)
    assert (receipt["status"], receipt["merchant"], receipt["extraction_error"]) == ("needs_review", "Corner Market", None)


async def test_twice_invalid_output_degrades_to_partial_record(client, alice, run_jobs):
    partial = json.dumps(
        {
            "merchant": "Corner Market",
            "date": "03/10/2026",  # wrong format
            "currency": "$",  # not ISO 4217
            "total": 12.5,
            "line_items": [{"description": "Milk", "quantity": 1, "unit_price": 2}, {"description": ""}],
        }
    )
    receipt_id = await new_receipt(client, alice)
    await run_jobs("```json\n" + partial + "\n```", partial)

    receipt = await get(client, alice, receipt_id)
    assert receipt["status"] == "needs_review" and receipt["extraction_error"]
    assert (receipt["merchant"], receipt["total"]) == ("Corner Market", "12.50")
    assert (receipt["purchased_at"], receipt["currency"]) == (None, None)
    assert [li["description"] for li in receipt["line_items"]] == ["Milk"]




async def test_transient_failure_is_retried_later_and_silently(client, alice, run_jobs):
    receipt_id = await new_receipt(client, alice)
    await run_jobs(BUSY)
    receipt = await get(client, alice, receipt_id)
    assert (receipt["status"], receipt["extraction_error"]) == ("processing", None)  # nothing shown to the user

    assert (await run_jobs()).prompts == []  # not due yet: nothing runs
    await run_jobs(GOOD, later=LATER)
    receipt = await get(client, alice, receipt_id)
    assert (receipt["status"], receipt["extraction_attempts"], receipt["extraction_error"]) == ("needs_review", 2, None)


async def test_long_quota_wait_fails_now_instead_of_hiding_for_hours(client, alice, run_jobs):
    receipt_id = await new_receipt(client, alice)
    await run_jobs(ExtractionFailed("Daily quota reached.", retryable=True, retry_after=14 * 3600))
    assert (await get(client, alice, receipt_id))["failure_reason"] == "ai_unavailable"


async def test_gives_up_after_max_attempts_then_manual_retry_works(client, alice, run_jobs, monkeypatch):
    monkeypatch.setattr(settings, "extraction_max_attempts", 2)
    receipt_id = await new_receipt(client, alice)
    await run_jobs(BUSY)
    await run_jobs(BUSY, later=LATER)
    failed = await get(client, alice, receipt_id)
    assert (failed["status"], failed["failure_reason"]) == ("failed", "ai_unavailable")
    assert "Gave up after 2 attempts" in failed["extraction_error"]

    retried = (await client.post(f"/receipts/{receipt_id}/extract", headers=alice)).json()
    assert (retried["status"], retried["extraction_attempts"], retried["failure_reason"]) == ("processing", 0, None)
    await run_jobs(GOOD)
    assert (await get(client, alice, receipt_id))["status"] == "needs_review"


@pytest.mark.parametrize(
    ("reply", "reason", "message"),
    [
        (ExtractionFailed("Rejected (404).", retryable=False), "ai_unavailable", "Rejected"),
        (answer(is_receipt=False, merchant=None, total=None, line_items=[]), "not_a_receipt", "doesn't look like a receipt"),
        (answer(merchant=None, total=None, line_items=[]), "nothing_readable", "couldn't read anything"),
    ],
)
async def test_permanent_failures_are_not_retried(client, alice, run_jobs, reply, reason, message):
    receipt_id = await new_receipt(client, alice)
    await run_jobs(reply)
    receipt = await get(client, alice, receipt_id)
    assert (receipt["status"], receipt["failure_reason"], receipt["extraction_attempts"]) == ("failed", reason, 1)
    assert message in receipt["extraction_error"]


async def test_missing_api_key_or_prompt_fails_gracefully(client, alice):
    first = await new_receipt(client, alice)
    await run_due_jobs(ReceiptExtractor.from_settings(settings))  # the real extractor, no key configured
    assert "GEMINI_API_KEY" in (await get(client, alice, first))["extraction_error"]

    await activate_prompt(None)
    second = await new_receipt(client, alice)
    await run_due_jobs(fake := FakeExtractor([]))
    assert fake.prompts == []
    assert (await get(client, alice, second))["failure_reason"] == "ai_unavailable"


async def test_rejected_receipt_can_be_filled_in_by_hand_or_retried(client, alice, run_jobs):
    first, second = await new_receipt(client, alice), await new_receipt(client, alice)
    not_a_receipt = answer(is_receipt=False)
    await run_jobs(not_a_receipt, not_a_receipt)
    assert {r["failure_reason"] for r in (await client.get("/receipts", headers=alice)).json()["items"]} == {"not_a_receipt"}

    confirmed = (await put(client, alice, first)).json()
    assert (confirmed["status"], confirmed["failure_reason"]) == ("confirmed", None)
    await client.post(f"/receipts/{second}/extract", headers=alice)
    await run_jobs(GOOD)
    assert (await get(client, alice, second))["status"] == "needs_review"


async def test_job_from_a_dead_worker_is_reclaimed(client, alice):
    receipt_id = await new_receipt(client, alice)
    abandoned = await claim_job()  # a worker leases the job, then dies
    assert abandoned is not None and await claim_job() is None  # leased: nobody else can take it

    after_expiry = lambda: utcnow() + lease_duration() + dt.timedelta(seconds=1)  # noqa: E731
    await run_due_jobs(FakeExtractor([GOOD]), now=after_expiry)
    assert (await get(client, alice, receipt_id))["status"] == "needs_review"

    # The original worker coming back late must not overwrite the result.
    await run_job(abandoned, FakeExtractor([answer(merchant="Stale")]))
    assert (await get(client, alice, receipt_id))["merchant"] == "Corner Market"


async def test_delete_mid_job_is_harmless_and_confirm_mid_job_is_refused(client, alice):
    receipt_id = await new_receipt(client, alice)
    assert (await put(client, alice, receipt_id)).status_code == 409  # extraction still running
    job = await claim_job()
    assert (await client.delete(f"/receipts/{receipt_id}", headers=alice)).status_code == 204
    await run_job(job, FakeExtractor([GOOD]))  # finds nothing to write; no error




async def test_confirm_edit_delete_keep_totals_consistent(client, alice, run_jobs):
    first, second = await new_receipt(client, alice), await new_receipt(client, alice)
    await run_jobs(GOOD, GOOD)

    async def october():
        return (await client.get("/receipts/summary?month=2026-10", headers=alice)).json()

    summary = await october()
    assert (summary["month_totals"], summary["pending_review_count"]) == ([], 2)  # drafts never count

    assert (await put(client, alice, first)).json()["status"] == "confirmed"
    await put(client, alice, second)
    assert (await october())["month_totals"] == [{"currency": "USD", "total": "16.46", "count": 2}]

    # The user's correction wins over what the model said.
    corrected = await put(client, alice, second, {**CONFIRMED, "total": "10.00", "category": "dining"})
    assert corrected.json()["totals_mismatch"] is True
    summary = await october()
    assert summary["month_totals"] == [{"currency": "USD", "total": "18.23", "count": 2}]
    assert {c["category"]: c["total"] for c in summary["month_by_category"]} == {"dining": "10.00", "groceries": "8.23"}

    assert (await client.delete(f"/receipts/{second}", headers=alice)).status_code == 204
    assert (await october())["month_totals"] == [{"currency": "USD", "total": "8.23", "count": 1}]
    assert (await client.get(f"/receipts/{second}/image", headers=alice)).status_code == 404


async def test_currencies_are_never_mixed_and_money_precision_is_enforced(client, alice, run_jobs):
    ids = [await new_receipt(client, alice) for _ in range(2)]
    await run_jobs(GOOD, GOOD)
    assert (await put(client, alice, ids[0], {**CONFIRMED, "total": "8.234"})).status_code == 422
    for receipt_id, currency in zip(ids, ("USD", "EUR"), strict=True):
        await put(client, alice, receipt_id, {**CONFIRMED, "currency": currency})

    totals = (await client.get("/receipts/summary?month=2026-10", headers=alice)).json()["month_totals"]
    assert {t["currency"] for t in totals} == {"EUR", "USD"}

    # A receipt from another month counts in the all-time breakdown, not in this month's.
    await put(client, alice, ids[1], {**CONFIRMED, "purchased_at": "2020-02-24", "category": "utilities"})
    summary = (await client.get("/receipts/summary?month=2026-10", headers=alice)).json()
    assert {c["category"] for c in summary["month_by_category"]} == {"groceries"}
    assert {c["category"] for c in summary["all_time_by_category"]} == {"groceries", "utilities"}




async def test_users_cannot_see_each_others_receipts(client, alice, run_jobs):
    bob = await auth_headers(client, "bob@example.com")
    receipt_id = await new_receipt(client, alice)
    await run_jobs(GOOD)
    await put(client, alice, receipt_id)

    assert (await client.get("/receipts", headers=bob)).json() == {"items": [], "next_cursor": None}
    assert (await client.get("/receipts/summary?month=2026-10", headers=bob)).json()["all_time_totals"] == []
    url = f"/receipts/{receipt_id}"
    for method, path in [("GET", url), ("GET", f"{url}/image"), ("PUT", url), ("POST", f"{url}/extract"), ("DELETE", url)]:
        body = CONFIRMED if method == "PUT" else None
        assert (await client.request(method, path, headers=bob, json=body)).status_code == 404, (method, path)
    assert (await client.get(url, headers=alice)).status_code == 200


async def test_upload_validation(client, alice, monkeypatch):
    assert (await upload(client, alice, b"%PDF-1.4 not an image", "r.pdf")).status_code == 415
    assert (await upload(client, alice, idempotency_key="no spaces allowed!")).status_code == 422
    monkeypatch.setattr(settings, "max_upload_bytes", 1000)
    assert (await upload(client, alice, make_image(size=(2000, 2000)))).status_code == 413


async def test_retried_upload_with_same_key_returns_original_receipt(client, alice):
    image = make_image()
    first = await upload(client, alice, image, idempotency_key="key-0001-abcdef")
    retry = await upload(client, alice, image, idempotency_key="key-0001-abcdef")
    assert (first.status_code, retry.status_code, retry.headers["Idempotent-Replayed"]) == (202, 200, "true")
    assert retry.json()["id"] == first.json()["id"]
    assert len((await client.get("/receipts", headers=alice)).json()["items"]) == 1

    other_file = await upload(client, alice, make_image(size=(500, 700)), idempotency_key="key-0001-abcdef")
    assert other_file.status_code == 422


async def test_idempotency_keys_are_per_user(client, alice):
    bob = await auth_headers(client, "bob@example.com")
    a = await upload(client, alice, make_image(), idempotency_key="shared-key-123")
    b = await upload(client, bob, make_image(), idempotency_key="shared-key-123")
    assert a.status_code == b.status_code == 202 and a.json()["id"] != b.json()["id"]




async def test_api_stores_original_and_worker_downscales_it_once(client, alice, run_jobs):
    receipt_id = await new_receipt(client, alice, make_image("PNG", (3000, 2000)), "big.png")
    before = await client.get(f"/receipts/{receipt_id}/image", headers=alice)
    assert before.headers["content-type"] == "image/png"  # untouched original until the worker runs

    first = await run_jobs(BUSY)
    second = await run_jobs(GOOD, later=LATER)
    sent, mime_type = second.images[0]
    assert first.images[0][0] == sent  # resized once, reused by the retry
    assert mime_type == "image/jpeg" and max(Image.open(BytesIO(sent)).size) == settings.max_image_edge
    after = await client.get(f"/receipts/{receipt_id}/image", headers=alice)
    assert (after.headers["content-type"], after.content) == ("image/jpeg", sent)


async def test_image_that_cannot_be_decoded_fails_without_calling_the_model(client, alice, run_jobs):
    truncated = make_image(size=(800, 1200))[:-2000]  # valid header, broken image data
    receipt_id = await new_receipt(client, alice, truncated)
    fake = await run_jobs()
    receipt = await get(client, alice, receipt_id)
    assert (receipt["status"], receipt["failure_reason"], fake.prompts) == ("failed", "image_unreadable", [])
    assert "could not be decoded" in receipt["extraction_error"]




async def activate_prompt(version: int | None) -> None:
    async with SessionLocal() as db:
        await db.execute(update(Prompt).values(is_active=False))
        if version is not None:
            db.add(Prompt(type=PromptType.RECEIPT_EXTRACTION, version=version, template=f"v{version} ({{categories}})", is_active=True))
        await db.commit()
    prompt_store.clear()  # what the 60 s cache expiry does in production


async def test_new_prompt_versions_take_over_and_are_recorded(client, alice, run_jobs):
    await activate_prompt(2)
    receipt_id = await new_receipt(client, alice)
    fake = await run_jobs(GOOD)
    assert fake.prompts[0].startswith("v2 (groceries, dining")
    assert (await get(client, alice, receipt_id))["prompt_version"] == 2


def test_backoff_grows_gradually_and_honours_the_services_wait():
    delays = [retry_delay(attempt).total_seconds() for attempt in range(1, 6)]
    assert 24 <= delays[0] <= 36 and delays[3] > 1500 and delays[4] <= 3600 * 1.2  # ~30s ... ~32m, capped
    assert retry_delay(1, requested=600).total_seconds() == 600  # Gemini asked for 10 minutes
    quota = {"error": {"details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"}]}}
    assert (retry_delay_seconds(quota), retry_delay_seconds({})) == (37.0, None)


async def test_prompt_type_is_constrained_to_known_types():
    async with SessionLocal() as db:
        db.add(Prompt(type="receipt_extration", version=9, template="typo", is_active=False))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_gemini_provider_sends_image_prompt_and_schema_and_maps_rate_limits():
    from google.genai import errors as genai_errors

    sent = {}

    async def generate_content(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(text=GOOD)

    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    extractor = ReceiptExtractor(GeminiVision(client, "gemini-test"), 1)
    result = await extractor.extract(b"img", "image/jpeg", "Read it")
    assert (result.data.merchant, sent["model"]) == ("Corner Market", "gemini-test")
    image, prompt = sent["contents"].parts
    assert (image.inline_data.mime_type, image.inline_data.data, prompt.text) == ("image/jpeg", b"img", "Read it")
    assert sent["config"].response_json_schema["required"][0] == "is_receipt"

    retry_info = {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"}

    async def rate_limited(**_kwargs):
        raise genai_errors.APIError(429, {"error": {"code": 429, "message": "quota", "details": [retry_info]}})

    client.aio.models.generate_content = rate_limited
    with pytest.raises(ExtractionFailed) as failed:
        await extractor.extract(b"img", "image/jpeg", "Read it")
    assert (failed.value.retryable, failed.value.retry_after) == (True, 37.0)


async def test_claude_provider_sends_image_and_strict_schema_and_handles_refusal():
    class FakeMessages:
        def __init__(self, response):
            self.response, self.sent = response, {}

        async def create(self, **kwargs):
            self.sent = kwargs
            return self.response

    messages = FakeMessages(SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=GOOD)]))
    extractor = ReceiptExtractor(ClaudeVision(SimpleNamespace(messages=messages), "claude-haiku-5-5"), 1)
    result = await extractor.extract(b"img", "image/jpeg", "Read it")
    assert (result.data.merchant, messages.sent["model"]) == ("Corner Market", "claude-haiku-5-5")
    image, text = messages.sent["messages"][0]["content"]
    assert (image["source"]["media_type"], text["text"]) == ("image/jpeg", "Read it")
    assert messages.sent["output_config"]["effort"] == "low"
    assert messages.sent["output_config"]["format"]["schema"]["additionalProperties"] is False

    messages.response = SimpleNamespace(stop_reason="refusal", content=[])
    with pytest.raises(ExtractionFailed, match="declined"):
        await extractor.extract(b"img", "image/jpeg", "Read it")


def test_line_items_are_rounded_per_line_like_a_till():
    from app.core.money import line_items_total

    weighed = [(Decimal("1.670"), Decimal("129.95")), (Decimal("0.758"), Decimal("1349.95"))]
    assert line_items_total(weighed) == Decimal("1240.28")  # 217.02 + 1023.26, as printed
    assert line_items_total([(Decimal("1.5"), Decimal("0.03"))] * 2) == Decimal("0.10")  # not 0.09


async def test_sub_cent_unit_prices_are_kept_so_utility_bills_add_up(client, alice, run_jobs):
    bill = answer(
        merchant="PG&E", currency="USD", category="utilities", tax=None, total=2578.62,
        line_items=[
            {"description": "Customer Charge", "quantity": 30, "unit_price": 4.59959},  # printed 137.99
            {"description": "Energy Charges", "quantity": 17384.64, "unit_price": 0.14039},  # printed 2,440.63
        ],
    )
    receipt_id = await new_receipt(client, alice)
    await run_jobs(bill)

    receipt = await get(client, alice, receipt_id)
    assert [li["unit_price"] for li in receipt["line_items"]] == ["4.599590", "0.140390"]  # not 4.60 / 0.14
    assert (receipt["line_items_total"], receipt["totals_mismatch"]) == ("2578.62", False)

    edited = {**CONFIRMED, "line_items": [{"description": "Energy", "quantity": "1", "unit_price": "0.140390"}]}
    assert (await put(client, alice, receipt_id, edited)).status_code == 200
    too_precise = {**edited, "line_items": [{"description": "Energy", "quantity": "1", "unit_price": "0.1403901"}]}
    assert (await put(client, alice, receipt_id, too_precise)).status_code == 422


async def test_same_photo_twice_is_rejected_with_a_link_to_the_first(client, alice):
    image = make_image()
    first = await new_receipt(client, alice, image)
    again = await upload(client, alice, image)
    assert again.status_code == 409 and again.json()["receipt_id"] == first
    bob = await auth_headers(client, "bob@example.com")
    assert (await upload(client, bob, image)).status_code == 202  # per user, never across users


async def test_rephotographed_receipt_is_flagged_as_a_possible_duplicate(client, alice, run_jobs):
    first = await new_receipt(client, alice)
    await run_jobs(GOOD)
    second = await new_receipt(client, alice)
    await run_jobs(answer(merchant="CORNER MARKET"))
    assert (await get(client, alice, second))["possible_duplicate_of"] == first
    assert (await get(client, alice, first))["possible_duplicate_of"] is None
    assert (await get(client, alice, second))["extraction_model"] == "fake"


async def test_uploads_are_capped_so_the_app_is_not_a_photo_store(client, alice, monkeypatch):
    monkeypatch.setattr(settings, "uploads_per_day", 2)
    await new_receipt(client, alice)
    await new_receipt(client, alice)
    third = await upload(client, alice)
    assert third.status_code == 429 and "Daily limit" in third.json()["detail"]

    monkeypatch.setattr(settings, "uploads_per_day", 100)
    monkeypatch.setattr(settings, "image_storage_limit_mb", 0)
    assert "Storage limit" in (await upload(client, alice)).json()["detail"]


async def test_rejected_uploads_are_purged_after_the_retention_period(client, alice, run_jobs):
    rejected = await new_receipt(client, alice)
    await run_jobs(answer(is_receipt=False))
    kept = await new_receipt(client, alice)
    await run_jobs(GOOD)
    assert await purge_rejected_uploads() == 0  # still within the retention window
    assert await purge_rejected_uploads(now=lambda: utcnow() + dt.timedelta(days=8)) == 1
    assert (await client.get(f"/receipts/{rejected}", headers=alice)).status_code == 404
    assert (await get(client, alice, kept))["status"] == "needs_review"


async def test_login_is_limited_per_account_across_ips(client, alice, monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1000)
    monkeypatch.setattr(settings, "login_attempts_per_account_and_ip_per_15_minutes", 3)
    body = {"email": "alice@example.com", "password": "wrong-password"}
    codes = [(await client.post("/auth/login", json=body)).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]


async def test_captcha_is_required_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "turnstile_secret_key", "secret")
    body = {"email": "carol@example.com", "password": "correct-horse"}
    missing = await client.post("/auth/register", json=body)
    assert missing.status_code == 400 and "human check" in missing.json()["detail"]

    def siteverify(token_ok: bool):
        return httpx.MockTransport(lambda _request: httpx.Response(200, json={"success": token_ok}))

    await verify_captcha("good-token", "1.2.3.4", siteverify(True))
    with pytest.raises(CaptchaFailed):
        await verify_captcha("bad-token", "1.2.3.4", siteverify(False))


async def test_prompts_are_immutable_in_the_database(client, alice, run_jobs):
    await new_receipt(client, alice)
    await run_jobs(GOOD)  # drafted with v1, so v1 can't be deleted either
    for statement in ("UPDATE prompts SET template = 'edited'", "DELETE FROM prompts"):
        async with SessionLocal() as db:
            with pytest.raises(DBAPIError, match="immutable|cannot be deleted"):
                await db.execute(text(statement))
    await activate_prompt(2)  # switching the active version is the one allowed change


async def test_concurrent_workers_process_each_job_exactly_once(client, alice):
    ids = [await new_receipt(client, alice) for _ in range(12)]
    fake = FakeExtractor([GOOD] * 12)
    counts = await asyncio.gather(*(run_due_jobs(fake) for _ in range(6)))
    assert sum(counts) == 12 and len(fake.images) == 12  # no job claimed twice, none missed
    assert {(await get(client, alice, i))["status"] for i in ids} == {"needs_review"}


# Security audit run 1 (~/security-audit-skill/ledger-pic/run-1): one regression test per lead.


async def test_client_ip_cannot_be_chosen_through_forwarded_headers(monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 2)
    # As the Dockerfile runs it: X-Forwarded-For trusted only from FORWARDED_ALLOW_IPS (default loopback).
    proxied = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")
    transport = httpx.ASGITransport(app=proxied, client=("203.0.113.5", 40000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as direct:
        codes = [
            (await direct.post(
                "/auth/register",
                json={"email": f"spoof{i}@example.com", "password": "correct-horse"},
                headers={"X-Forwarded-For": f"198.51.100.{i}"},  # a fresh fake IP every time
            )).status_code
            for i in range(3)
        ]
    assert codes == [201, 201, 429]
    command = (Path(__file__).parents[1] / "Dockerfile").read_text().split("\nCMD", 1)[1]
    assert "forwarded-allow-ips" not in command  # never '*'; configured through FORWARDED_ALLOW_IPS


def test_rate_limiter_forgets_idle_keys():
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    for i in range(500):
        limiter.allow(f"auth:198.51.100.{i}", 10, 60)
    now[0] = 600  # every one of those windows has passed
    limiter.allow("auth:203.0.113.1", 10, 60)
    assert len(limiter) == 1


async def test_failed_human_checks_cannot_lock_someone_out(client, alice, monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1000)
    monkeypatch.setattr(settings, "turnstile_secret_key", "secret")
    attack = {"email": "Alice@Example.com", "password": "wrong-password"}  # no captcha token
    codes = {(await client.post("/auth/login", json=attack)).status_code for _ in range(15)}
    assert codes == {400}

    async def solved(token, ip):
        return None

    monkeypatch.setattr("app.api.routes.auth.verify_captcha", solved)
    owner = {"email": "alice@example.com", "password": "correct-horse", "captcha_token": "ok"}
    assert (await client.post("/auth/login", json=owner)).status_code == 200


async def test_job_that_keeps_killing_the_worker_is_failed_not_retried_forever(client, alice):
    receipt_id = await new_receipt(client, alice)
    start, step = utcnow(), lease_duration() + dt.timedelta(seconds=1)
    for n in range(settings.extraction_max_attempts):
        # Claimed, then the worker dies before recording anything; the lease runs out.
        assert await claim_job(now=lambda n=n: start + n * step) is not None
    assert await claim_job(now=lambda: start + 10 * step) is None
    receipt = await get(client, alice, receipt_id)
    assert (receipt["status"], receipt["failure_reason"]) == ("failed", "ai_unavailable")


async def test_decoding_runs_in_a_child_process_that_fails_safely():
    out = await normalize_image(make_image(size=(3000, 2000)), 1600, timeout=30)
    assert Image.open(BytesIO(out)).size == (1600, 1067)
    for hostile in (b"\xff\xd8 not really a jpeg", make_image()[:-500]):
        with pytest.raises(UnsupportedImage, match="could not be decoded"):
            await normalize_image(hostile, 1600, timeout=30)
    # A decode that overruns is killed, not waited for (a thread could never be stopped).
    with pytest.raises(UnsupportedImage, match="too long"):
        await normalize_image(make_image(size=(3000, 2000)), 1600, timeout=0.001)


async def test_one_bad_job_does_not_stop_the_worker(monkeypatch):
    worker = Worker(FakeExtractor([]))
    crashed = []

    async def claim():
        if len(crashed) == 2:
            worker.stop()
            return None
        return Job(uuid.uuid4(), uuid.uuid4())

    async def crash(job, extractor):
        crashed.append(job)
        raise RuntimeError("decoder blew up")

    monkeypatch.setattr("app.workers.runner.claim_job", claim)
    monkeypatch.setattr("app.workers.runner.run_job", crash)
    await asyncio.wait_for(worker.work_loop(), timeout=5)
    assert len(crashed) == 2  # the loop kept going after the first crash


async def test_huge_images_are_rejected_at_upload(client, alice, monkeypatch):
    monkeypatch.setattr("app.services.images.MAX_IMAGE_PIXELS", 1000)
    response = await upload(client, alice, make_image(size=(400, 600)))
    assert response.status_code == 415 and "too large" in response.json()["detail"]


async def test_deleting_receipts_does_not_give_back_the_daily_upload_quota(client, alice, monkeypatch):
    monkeypatch.setattr(settings, "uploads_per_day", 2)
    first = await new_receipt(client, alice)
    await new_receipt(client, alice)
    assert (await client.delete(f"/receipts/{first}", headers=alice)).status_code == 204
    again = await upload(client, alice)
    assert again.status_code == 429 and "Daily limit" in again.json()["detail"]


async def test_reextract_is_metered_and_waits_its_turn(client, alice, run_jobs, monkeypatch):
    monkeypatch.setattr(settings, "reextracts_per_day", 1)
    mine = await new_receipt(client, alice)
    await run_jobs(GOOD)
    bob = await auth_headers(client, "bob@example.com")
    theirs = await new_receipt(client, bob)
    assert (await client.post(f"/receipts/{mine}/extract", headers=alice)).status_code == 202
    job = await claim_job()
    assert str(job.receipt_id) == theirs  # queued earlier, so served first
    await run_job(job, FakeExtractor([GOOD]))
    await run_jobs(GOOD)
    second = await client.post(f"/receipts/{mine}/extract", headers=alice)
    assert second.status_code == 429 and "re-runs" in second.json()["detail"]


def test_compose_keeps_postgres_off_the_network():
    compose = (Path(__file__).parents[2] / "docker-compose.yml").read_text()
    assert '"127.0.0.1:5432:5432"' in compose and '- "5432:5432"' not in compose
    assert "POSTGRES_PASSWORD: ledgerpic" not in compose  # comes from .env


async def test_responses_are_not_sniffed_and_cors_allows_only_what_the_frontend_sends(client, alice):
    receipt_id = await new_receipt(client, alice)
    image = await client.get(f"/receipts/{receipt_id}/image", headers=alice)
    assert image.headers["X-Content-Type-Options"] == "nosniff"
    preflight = {"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"}
    allowed = await client.options("/receipts", headers={**preflight, "Access-Control-Request-Headers": "authorization,idempotency-key"})
    assert allowed.status_code == 200
    odd = await client.options("/receipts", headers={**preflight, "Access-Control-Request-Headers": "x-anything"})
    assert odd.status_code == 400


# Security audit run 2 (~/security-audit-skill/ledger-pic/run-2): one regression test per lead.


async def test_simultaneous_wrong_passwords_cannot_overrun_the_account_limit(client, alice, monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1000)
    monkeypatch.setattr(settings, "login_attempts_per_account_and_ip_per_15_minutes", 3)
    guess = {"email": "alice@example.com", "password": "wrong-password"}
    codes = await asyncio.gather(*(client.post("/auth/login", json=guess) for _ in range(20)))
    assert sorted(c.status_code for c in codes) == [401] * 3 + [429] * 17


async def test_successful_logins_do_not_use_up_the_account_limit(client, alice, monkeypatch):
    monkeypatch.setattr(settings, "login_attempts_per_account_and_ip_per_15_minutes", 2)
    owner = {"email": "alice@example.com", "password": "correct-horse"}
    assert {(await client.post("/auth/login", json=owner)).status_code for _ in range(5)} == {200}


def _chunked(*parts: bytes):
    """A body with no Content-Length, sent as chunks (what a streaming client does)."""

    async def stream():
        for part in parts:
            yield part

    return stream()


async def test_chunked_upload_over_the_limit_is_cut_off_before_auth_or_disk(client, alice, monkeypatch):
    monkeypatch.setattr("app.api.body_limit.MULTIPART_OVERHEAD", 0)
    limit = settings.max_upload_bytes
    boundary = "ledgerpicboundary"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"r.jpg\"\r\n"
            "Content-Type: image/jpeg\r\n\r\n").encode()
    chunk = b"\0" * (1024 * 1024)
    body = [head] + [chunk] * (limit // len(chunk) + 2) + [f"\r\n--{boundary}--\r\n".encode()]
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    response = await client.post("/receipts", headers={**headers, **alice}, content=_chunked(*body))
    assert response.status_code == 413  # signed in: cut off while streaming, before the handler
    for auth in ({}, {"Authorization": "Bearer not-a-real-token"}):  # no token, or a forged one
        refused = await client.post("/receipts", headers={**headers, **auth}, content=_chunked(*body))
        assert refused.status_code == 401  # refused before a single byte of the body is read


async def test_chunked_json_bodies_are_capped_too(client):
    huge = b'{"email": "a@example.com", "password": "' + b"x" * (2 * 1024 * 1024) + b'"}'
    response = await client.post(
        "/auth/login", headers={"Content-Type": "application/json"}, content=_chunked(huge[:1000], huge[1000:])
    )
    assert response.status_code == 413


async def test_compose_tunnel_gives_each_visitor_their_own_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 2)
    compose = (Path(__file__).parents[2] / "docker-compose.yml").read_text()
    tunnel_ip = "172.28.0.10"
    assert f"ipv4_address: {tunnel_ip}" in compose and f"FORWARDED_ALLOW_IPS:-{tunnel_ip}" in compose
    proxied = ProxyHeadersMiddleware(app, trusted_hosts=tunnel_ip)

    async def register_three(peer: str) -> list[int]:
        transport = httpx.ASGITransport(app=proxied, client=(peer, 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return [
                (await c.post(
                    "/auth/register",
                    json={"email": f"{peer}-{i}@example.com", "password": "correct-horse"},
                    # The tunnel appends the visitor's real address; a client-sent value stays on the left.
                    headers={"X-Forwarded-For": f"203.0.113.9, 198.51.100.{i}"},
                )).status_code
                for i in range(3)
            ]

    assert await register_three(tunnel_ip) == [201, 201, 201]  # three visitors, three buckets
    # Straight to the published port (Docker's gateway): forwarded headers are ignored, one bucket.
    assert await register_three("192.168.65.1") == [201, 201, 429]


# Hardening after audit run 2.


@pytest.mark.filterwarnings("ignore:The HMAC key")  # the forged HS512 token uses a short key on purpose
async def test_tokens_signed_with_another_algorithm_are_refused(client, alice):
    me = (await client.get("/auth/me", headers=alice)).json()
    claims = {"sub": me["id"], "exp": utcnow() + dt.timedelta(hours=1)}
    for forged in (jwt.encode(claims, settings.jwt_secret, algorithm="HS512"), jwt.encode(claims, key=None, algorithm="none")):
        assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})).status_code == 401


async def test_captcha_tokens_from_another_site_or_action_are_refused(monkeypatch):
    monkeypatch.setattr(settings, "turnstile_secret_key", "secret")
    monkeypatch.setattr(settings, "turnstile_hostnames", "ledgerpic.example")

    def siteverify(**outcome):
        return httpx.MockTransport(lambda _request: httpx.Response(200, json={"success": True, **outcome}))

    await verify_captcha("t", None, siteverify(hostname="ledgerpic.example", action="auth"))
    for outcome in ({"hostname": "evil.example", "action": "auth"}, {"hostname": "ledgerpic.example", "action": "checkout"}):
        with pytest.raises(CaptchaFailed):
            await verify_captcha("t", None, siteverify(**outcome))


async def test_total_daily_ai_jobs_are_capped_across_all_accounts(client, alice, monkeypatch):
    monkeypatch.setattr(settings, "ai_jobs_per_day_total", 1)
    await new_receipt(client, alice)
    bob = await auth_headers(client, "bob@example.com")
    blocked = await upload(client, bob)
    assert blocked.status_code == 429 and "processing limit" in blocked.json()["detail"]


async def test_simultaneous_uploads_cannot_overshoot_the_daily_limit(client, alice, monkeypatch):
    monkeypatch.setattr(settings, "uploads_per_day", 2)
    responses = await asyncio.gather(*(upload(client, alice) for _ in range(6)))
    assert sorted(r.status_code for r in responses) == [202, 202, 429, 429, 429, 429]


def test_lease_covers_waiting_for_a_model_slot(monkeypatch):
    monkeypatch.setattr(settings, "worker_concurrency", 4)
    monkeypatch.setattr(settings, "ai_max_concurrency", 4)
    single = lease_duration()
    monkeypatch.setattr(settings, "ai_max_concurrency", 1)  # fewer model slots than worker loops
    # Behind the three other loops' model calls (each up to two calls with one SDK retry).
    assert lease_duration() - single == dt.timedelta(seconds=(settings.ai_timeout_seconds * 4 + 60) * 3)


async def test_receipt_deleted_while_preparing_is_never_sent_to_the_model(client, alice, monkeypatch):
    receipt_id = await new_receipt(client, alice)

    async def normalize_then_user_deletes(raw, max_edge, timeout, require_landlock=True):
        assert (await client.delete(f"/receipts/{receipt_id}", headers=alice)).status_code == 204
        return raw

    monkeypatch.setattr("app.workers.extraction_job.normalize_image", normalize_then_user_deletes)
    fake = FakeExtractor([GOOD])
    await run_due_jobs(fake)
    assert fake.prompts == []



# Security audit run 3 (~/security-audit-skill/ledger-pic/run-3): one regression test per lead.


async def test_a_strangers_wrong_guesses_do_not_lock_the_owner_out(alice, monkeypatch):
    monkeypatch.setattr(settings, "login_attempts_per_account_and_ip_per_15_minutes", 3)

    async def login_from(ip: str, password: str) -> int:
        transport = httpx.ASGITransport(app=app, client=(ip, 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return (await c.post("/auth/login", json={"email": "alice@example.com", "password": password})).status_code

    attacker = [await login_from("198.51.100.66", "wrong-password") for _ in range(5)]
    assert attacker == [401, 401, 401, 429, 429]
    assert await login_from("203.0.113.10", "correct-horse") == 200  # the owner, from their own address


async def test_guesses_spread_over_many_addresses_still_hit_the_account_ceiling(alice, monkeypatch):
    monkeypatch.setattr(settings, "login_attempts_per_account_per_15_minutes", 4)
    codes = []
    for i in range(6):
        transport = httpx.ASGITransport(app=app, client=(f"198.51.100.{i}", 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            body = {"email": "alice@example.com", "password": "wrong-password"}
            codes.append((await c.post("/auth/login", json=body)).status_code)
    assert codes == [401, 401, 401, 401, 429, 429]


async def test_jpegs_packed_with_progressive_scans_are_rejected(client, alice):
    baseline = make_image()
    assert jpeg_scan_count(baseline) == 1
    buf = BytesIO()
    Image.new("RGB", (400, 600), "white").save(buf, "JPEG", progressive=True)
    assert 1 < jpeg_scan_count(buf.getvalue()) <= MAX_JPEG_SCANS  # an ordinary progressive photo is fine
    packed = baseline[:-2] + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" * (MAX_JPEG_SCANS + 1) + b"\xff\xd9"
    response = await upload(client, alice, packed)
    assert response.status_code == 415 and "progressive scans" in response.json()["detail"]
    with pytest.raises(UnsupportedImage):  # and the worker refuses one stored before the check existed
        await normalize_image(packed, 1600, timeout=30)


# Hardening after audit run 3.


async def test_new_accounts_get_a_smaller_daily_allowance(client, alice, monkeypatch):
    monkeypatch.setattr(settings, "new_account_hours", 24)
    monkeypatch.setattr(settings, "new_account_uploads_per_day", 1)
    await new_receipt(client, alice)
    assert (await upload(client, alice)).status_code == 429  # day one: 1 upload
    async with SessionLocal() as db:
        await db.execute(update(User).values(created_at=utcnow() - dt.timedelta(days=2)))
        await db.commit()
    assert (await upload(client, alice)).status_code == 202  # an established account: the full 100


async def test_each_address_can_create_only_a_few_accounts_a_day(client, monkeypatch):
    monkeypatch.setattr(settings, "registrations_per_ip_per_day", 2)
    register = lambda email: client.post("/auth/register", json={"email": email, "password": "correct-horse"})
    assert (await register("one@example.com")).status_code == 201
    assert (await register("one@example.com")).status_code == 409  # failed sign-ups don't count
    assert (await register("two@example.com")).status_code == 201
    assert (await register("three@example.com")).status_code == 429


async def test_ipv6_clients_are_limited_per_64_block(monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 2)
    codes = []
    for i in range(3):  # a fresh address from the same /64 each time
        transport = httpx.ASGITransport(app=app, client=(f"2001:db8:1:2::{i + 1:x}", 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            body = {"email": f"v6-{i}@example.com", "password": "correct-horse"}
            codes.append((await c.post("/auth/register", json=body)).status_code)
    assert codes == [201, 201, 429]


async def test_receipt_deleted_while_waiting_for_a_model_slot_is_never_sent():
    fake = FakeExtractor([GOOD])

    async def deleted() -> bool:
        return False

    with pytest.raises(NoLongerWanted):
        await fake.extract(b"img", "image/jpeg", "Read it", deleted)
    assert fake.prompts == []


async def test_in_production_captcha_tokens_must_be_for_the_login_action(monkeypatch):
    monkeypatch.setattr(settings, "turnstile_secret_key", "secret")
    monkeypatch.setattr(settings, "turnstile_hostnames", "ledgerpic.example")
    no_action = httpx.MockTransport(lambda _r: httpx.Response(200, json={"success": True, "hostname": "ledgerpic.example"}))
    with pytest.raises(CaptchaFailed):
        await verify_captcha("t", None, no_action)


def test_compose_never_hands_the_tunnel_address_to_another_container():
    compose = (Path(__file__).parents[2] / "docker-compose.yml").read_text()
    assert "ip_range: 172.28.0.128/25" in compose and "ipv4_address: 172.28.0.10" in compose


async def test_the_decoder_child_gets_no_environment(monkeypatch):
    monkeypatch.setenv("CLAUDE_API_KEY", "sk-must-not-leak")
    seen = {}
    real = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        seen.update(kwargs)
        return await real(*args, **kwargs)

    monkeypatch.setattr("app.services.images.asyncio.create_subprocess_exec", spy)
    await normalize_image(make_image(), 1600, timeout=30)
    assert seen["env"] == {}


def test_oversized_model_output_is_not_stored_whole():
    huge = json.dumps({"merchant": "x" * 300_000})
    kept = _as_json(huge)
    assert kept["truncated"] is True and len(kept["unparsed_text"]) == 5000


async def test_a_malformed_siteverify_response_is_a_failed_check_not_a_crash(monkeypatch):
    monkeypatch.setattr(settings, "turnstile_secret_key", "secret")
    not_an_object = httpx.MockTransport(lambda _r: httpx.Response(200, json=["success"]))
    with pytest.raises(CaptchaFailed):
        await verify_captcha("t", None, not_an_object)


# Security audit run 4 (~/security-audit-skill/ledger-pic/run-4): one regression test per lead and deferred area.


def test_limiter_keys_are_forgotten_after_their_own_window():
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    limiter.allow("register:1.2.3.4", 5, 86400)  # a 24 h key ...
    for i in range(1000):  # ... must not keep 15-minute keys alive
        limiter.allow(f"login:{i}@example.com:1.2.3.4", 10, 900)
    now[0] = 3600
    limiter.allow("auth:9.9.9.9", 10, 60)
    assert len(limiter) == 2  # the register key (still in its window) and the new one


async def test_throwaway_accounts_cannot_use_up_the_processing_limit_for_everyone(client, alice, monkeypatch):
    monkeypatch.setattr(settings, "untrusted_accounts_jobs_per_day_total", 1)
    await new_receipt(client, alice)  # alice is new: uses the new-account cohort's one job
    bob = await auth_headers(client, "bob@example.com")
    blocked = await upload(client, bob)
    assert blocked.status_code == 429 and "New accounts" in blocked.json()["detail"]
    async with SessionLocal() as db:  # an established user is not affected
        await db.execute(update(User).where(User.email == "bob@example.com").values(created_at=utcnow() - dt.timedelta(days=30)))
        await db.commit()
    assert (await upload(client, bob)).status_code == 202


def test_lease_covers_decoding_and_both_model_calls(monkeypatch):
    monkeypatch.setattr(settings, "worker_concurrency", 4)
    monkeypatch.setattr(settings, "ai_max_concurrency", 4)
    worst_case = settings.image_decode_timeout_seconds + settings.ai_timeout_seconds * 4
    assert lease_duration().total_seconds() > worst_case


def test_deeply_nested_model_output_is_just_malformed():
    assert "unparsed_text" in _as_json("[" * 150_000)


# Security audit run 5 (~/security-audit-skill/ledger-pic/run-5): one regression test per lead.


async def _sign_in(ip: str, password: str, device_token: str | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=(ip, 40000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        body = {"email": "alice@example.com", "password": password, "device_token": device_token}
        return await c.post("/auth/login", json=body)


async def test_strangers_cannot_lock_the_owner_out_of_a_device_they_signed_in_on(alice, monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1000)
    monkeypatch.setattr(settings, "login_attempts_per_account_per_15_minutes", 20)
    device = (await _sign_in("203.0.113.10", "correct-horse")).json()["device_token"]
    for _ in range(10):  # someone behind the owner's own (shared) address ...
        await _sign_in("203.0.113.10", "wrong-password")
    for i in range(3):  # ... and others everywhere else
        for _ in range(10):
            await _sign_in(f"198.51.100.{i}", "wrong-password")
    assert (await _sign_in("203.0.113.10", "correct-horse")).status_code == 429  # no device token: locked
    signed_in = await _sign_in("198.51.100.200", "correct-horse", device)  # any network, after any restart
    assert signed_in.status_code == 200 and signed_in.json()["device_token"] == device


async def test_a_device_token_only_counts_for_its_own_account_and_guesses_on_it_are_limited(client, alice, monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1000)
    monkeypatch.setattr(settings, "login_attempts_per_device_per_15_minutes", 2)
    monkeypatch.setattr(settings, "login_attempts_per_account_and_ip_per_15_minutes", 2)
    bobs = (await client.post("/auth/register", json={"email": "bob@example.com", "password": "correct-horse"})).json()
    for _ in range(2):
        await _sign_in("198.51.100.1", "wrong-password")
    assert (await _sign_in("198.51.100.1", "correct-horse", bobs["device_token"])).status_code == 429  # bob's: no help
    device = (await _sign_in("198.51.100.2", "correct-horse")).json()["device_token"]
    codes = [(await _sign_in("198.51.100.3", "wrong-password", device)).status_code for _ in range(3)]
    assert codes == [401, 401, 429]  # a stolen device token gives no more guesses than an address
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {device}"})
    assert me.status_code == 401  # and it is never an access token


async def test_a_flood_of_sign_ins_gets_busy_instead_of_queueing_forever(client, alice, monkeypatch):
    monkeypatch.setattr(auth_service, "HASH_WAIT_SECONDS", 0.05)
    held = [await asyncio.wait_for(auth_service._hashing().acquire(), 1) for _ in range(4)]
    try:
        response = await client.post("/auth/login", json={"email": "alice@example.com", "password": "correct-horse"})
    finally:
        for _ in held:
            auth_service._hashing().release()
    assert response.status_code == 503
    assert (await client.post("/auth/login", json={"email": "alice@example.com", "password": "correct-horse"})).status_code == 200


async def test_accounts_left_to_age_a_few_days_still_share_the_new_account_ceiling(client, alice, monkeypatch):
    monkeypatch.setattr(settings, "untrusted_accounts_jobs_per_day_total", 1)
    async with SessionLocal() as db:  # throwaway accounts registered days ago, past their first day
        await db.execute(update(User).values(created_at=utcnow() - dt.timedelta(days=3)))
        await db.commit()
    await new_receipt(client, alice)
    blocked = await upload(client, alice)
    assert blocked.status_code == 429 and "New accounts" in blocked.json()["detail"]


async def test_a_nul_in_the_model_answer_is_dropped_not_a_save_that_fails_forever(client, alice, run_jobs):
    receipt_id = await new_receipt(client, alice)
    fake = await run_jobs(answer(merchant="Corner\u0000 Market", line_items=[{"description": "Mi\u0000lk", "quantity": 1, "unit_price": 8.23}]))
    assert len(fake.prompts) == 1
    receipt = await get(client, alice, receipt_id)
    assert receipt["status"] == "needs_review" and receipt["merchant"] == "Corner Market"
    assert receipt["line_items"][0]["description"] == "Milk"
    line = {"description": "Mi\u0000lk", "quantity": "1", "unit_price": "8.23"}
    edited = await put(client, alice, receipt_id, {**CONFIRMED, "merchant": "Corner\u0000 Market", "line_items": [line]})
    assert edited.status_code == 200 and edited.json()["merchant"] == "Corner Market"
    assert edited.json()["line_items"][0]["description"] == "Milk"


async def test_an_answer_postgres_refuses_fails_the_receipt_instead_of_retrying(client, alice, run_jobs, monkeypatch):
    monkeypatch.setattr("app.services.extraction._storable", lambda value: value)  # let the NUL through
    receipt_id = await new_receipt(client, alice)
    fake = await run_jobs(answer(is_receipt=False, merchant="A\u0000B"))
    receipt = await get(client, alice, receipt_id)
    assert receipt["status"] == "failed" and "could not be saved" in receipt["extraction_error"]
    assert (await run_jobs(GOOD, later=LATER)).prompts == []  # not retried
    assert len(fake.prompts) == 1


def _fake_decoder(monkeypatch, script: str) -> None:
    monkeypatch.setattr(images, "_CHILD", script)


async def test_the_decoder_and_anything_it_started_are_killed_on_timeout(monkeypatch, tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    _fake_decoder(monkeypatch, f"""
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open({str(pid_file)!r}, "w").write(str(child.pid))
time.sleep(60)
""")
    with pytest.raises(UnsupportedImage):
        await normalize_image(make_image(), 1600, timeout=1)
    grandchild = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("the decoder's own child survived the kill")


@pytest.mark.parametrize("output", ["b'not a jpeg'", "b'\\xff\\xd8\\xff' + b'0' * (17 * 1024 * 1024) + b'\\xff\\xd9'"])
async def test_only_a_bounded_jpeg_from_the_decoder_is_kept(monkeypatch, output):
    _fake_decoder(monkeypatch, f"import sys\nsys.stdin.buffer.read()\nsys.stdout.buffer.write({output})")
    with pytest.raises(UnsupportedImage):
        await normalize_image(make_image(), 1600, timeout=30)


@pytest.mark.skipif(sys.platform != "linux", reason="seccomp is Linux-only; on other systems this runs in CI and the container")
def test_the_confined_decoder_cannot_open_connections_or_start_processes():
    import subprocess

    probe = """
import ctypes, os, platform, socket, sys
sys.path.insert(0, sys.argv[1])
from app.services.images import confine_decoder
assert confine_decoder(ctypes.CDLL(None, use_errno=True), platform.machine())
import resource, signal
libc = ctypes.CDLL(None, use_errno=True)
SYS_RT_SIGQUEUEINFO = {"x86_64": 129, "aarch64": 138}[platform.machine()]
siginfo = (ctypes.c_int * 32)(signal.SIGUSR1, 0, -1)  # si_signo, si_errno, si_code = SI_QUEUE

def check(result):
    if result == -1:
        raise PermissionError(ctypes.get_errno(), "")

attempts = (
    lambda: socket.socket(), os.fork, lambda: os.execv("/bin/true", ["true"]), lambda: os.kill(os.getppid(), 0),
    lambda: check(libc.syscall(SYS_RT_SIGQUEUEINFO, os.getppid(), signal.SIGUSR1, siginfo)),
    lambda: resource.prlimit(os.getppid(), resource.RLIMIT_NOFILE, (32, 32)),
    lambda: open(f"/proc/{os.getppid()}/environ", "rb").read() if libc.syscall(444, None, 0, 1) >= 1 else check(-1),  # Landlock
)
for attempt in attempts:
    try:
        attempt()
        print("allowed")
    except PermissionError:
        print("denied")
resource.prlimit(0, resource.RLIMIT_NOFILE)  # its own limits stay readable
print("self-ok")
"""
    root = str(Path(images.__file__).resolve().parents[2])
    result = subprocess.run([sys.executable, "-I", "-B", "-c", probe, root], capture_output=True, text=True, timeout=30)
    assert result.stdout.split() == ["denied"] * 7 + ["self-ok"], result.stderr


async def test_the_real_decoder_still_decodes_under_its_confinement():
    out = await normalize_image(make_image("PNG"), 1600, timeout=30)
    assert out.startswith(b"\xff\xd8\xff")


# Security audit run 6 (~/security-audit-skill/ledger-pic/run-6): one regression test per lead and deferred area.


async def test_one_accounts_batch_does_not_hold_up_everyone_elses_uploads(client, alice):
    batch = [await new_receipt(client, alice, make_image(size=(400 + i, 600))) for i in range(3)]
    bob = await auth_headers(client, "bob@example.com")
    bobs = await new_receipt(client, bob)
    order = []
    while job := await claim_job():
        order.append(str(job.receipt_id))
    assert order == [batch[0], bobs, batch[1], batch[2]]


async def test_only_one_decoder_runs_at_a_time(monkeypatch):
    running, most = 0, 0
    real = images._decode_in_child

    async def counted(*args):
        nonlocal running, most
        running += 1
        most = max(most, running)
        try:
            return await real(*args)
        finally:
            running -= 1

    monkeypatch.setattr(images, "_decode_in_child", counted)
    await asyncio.gather(*(normalize_image(make_image(), 1600, timeout=30) for _ in range(3)))
    assert most == 1


async def test_sign_ins_waiting_for_a_hashing_slot_hold_no_database_connection(client, alice, monkeypatch):
    held = [await asyncio.wait_for(auth_service._hashing().acquire(), 1) for _ in range(4)]
    try:
        waiting = [asyncio.create_task(client.post("/auth/login", json={"email": "alice@example.com", "password": "x"})) for _ in range(3)]
        await asyncio.sleep(0.5)  # all three are queued for a hashing slot now
        assert engine.pool.checkedout() == 0
    finally:
        for _ in held:
            auth_service._hashing().release()
    assert {(await w).status_code for w in waiting} == {401}


async def test_established_users_keep_a_small_allowance_when_aged_accounts_use_up_the_pool(client, alice, monkeypatch):
    monkeypatch.setattr(settings, "ai_jobs_per_day_total", 2)
    monkeypatch.setattr(settings, "ai_jobs_per_day_hard_total", 4)
    monkeypatch.setattr(settings, "guaranteed_jobs_per_day", 1)
    mallory = await auth_headers(client, "mallory@example.com")
    async with SessionLocal() as db:  # everyone here is older than trusted_account_days
        await db.execute(update(User).values(created_at=utcnow() - dt.timedelta(days=30)))
        await db.commit()
    for i in range(2):  # an aged attacker account fills the shared pool ...
        await new_receipt(client, mallory, make_image(size=(400 + i, 600)))
    assert (await upload(client, mallory, make_image(size=(450, 600)))).status_code == 429  # ... and can't go on
    assert (await upload(client, alice)).status_code == 202  # alice still gets her guaranteed job
    blocked = await upload(client, alice, make_image(size=(460, 600)))
    assert blocked.status_code == 429 and "processing limit" in blocked.json()["detail"]


# Security audit run 7 (~/security-audit-skill/ledger-pic/run-7): one regression test per lead.


async def test_decoders_of_different_worker_processes_never_overlap():
    import subprocess

    holder = subprocess.Popen(  # another worker process, mid-decode
        [sys.executable, "-c", "import fcntl, sys, time; f = open(sys.argv[1]); fcntl.flock(f, fcntl.LOCK_EX); print('locked', flush=True); time.sleep(1.5)", images.__file__],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        started = time.monotonic()
        await normalize_image(make_image(), 1600, timeout=30)
        assert time.monotonic() - started > 1.0  # waited for the other process's decode
    finally:
        holder.wait(timeout=10)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"jwt_secret": "a" * 40}, "JWT_SECRET is too predictable"),
        ({"turnstile_secret_key": "0x4AAAAAAAreal-secret"}, "TURNSTILE_HOSTNAMES"),
    ],
)
def test_settings_refuse_a_guessable_secret_or_an_unbound_captcha(overrides, message):
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match=message):
        Settings(**{"jwt_secret": "test-secret-not-for-production-0123456789", "turnstile_hostnames": "", **overrides})
    Settings(jwt_secret="test-secret-not-for-production-0123456789", turnstile_secret_key="1x0000000000000000000000000000000AA")


# Security audit run 8 (~/security-audit-skill/ledger-pic/run-8).


def test_the_worker_container_has_no_init_process_holding_its_secrets():
    compose = (Path(__file__).parents[2] / "docker-compose.yml").read_text()
    worker = compose.split("\n  worker:\n", 1)[1].split("\n  # ", 1)[0]
    settings_lines = [line.strip() for line in worker.splitlines() if not line.strip().startswith("#")]
    assert "init: true" not in settings_lines  # PID 1 must be the non-dumpable worker itself


async def test_anonymous_uploads_are_refused_before_the_body_is_read(client):
    response = await client.post("/receipts", files={"file": ("r.jpg", make_image(), "image/jpeg")})
    assert response.status_code == 401



async def test_a_model_timeout_is_retried_once_not_five_times_and_waits_behind_newer_uploads(client, alice, run_jobs):
    slow = ExtractionFailed.took_too_long()
    receipt_id = await new_receipt(client, alice)
    first = await run_jobs(slow)
    assert len(first.prompts) == 1
    bob = await auth_headers(client, "bob@example.com")
    bobs = await new_receipt(client, bob)
    order = []
    later = utcnow() + LATER
    while job := await claim_job(now=lambda: later):
        order.append(str(job.receipt_id))
        await run_job(job, FakeExtractor([slow] if str(job.receipt_id) == receipt_id else [GOOD]), now=lambda: later)
    assert order == [bobs, receipt_id]  # the retry went behind bob's newer upload
    receipt = await get(client, alice, receipt_id)
    assert receipt["status"] == "failed" and "too long" in receipt["extraction_error"]  # no third try
    assert (await run_jobs(GOOD, later=LATER * 10)).prompts == []


async def test_an_undecodable_upload_is_not_kept_or_rerun(client, alice, run_jobs, monkeypatch):
    async def undecodable(raw, max_edge, timeout, require_landlock=True):
        raise UnsupportedImage("The image could not be decoded")

    monkeypatch.setattr("app.workers.extraction_job.normalize_image", undecodable)
    receipt_id = await new_receipt(client, alice)
    assert (await run_jobs()).prompts == []
    assert (await get(client, alice, receipt_id))["failure_reason"] == "image_unreadable"
    async with SessionLocal() as db:
        stored = await db.get(ReceiptImage, uuid.UUID(receipt_id))
        assert stored.data == b"" and stored.size_bytes == 0  # the up-to-10 MB original is gone at once
    assert (await client.get(f"/receipts/{receipt_id}/image", headers=alice)).status_code == 404
    assert (await client.post(f"/receipts/{receipt_id}/extract", headers=alice)).status_code == 409



def test_the_decoder_refuses_to_run_without_landlock_unless_told_to(monkeypatch):
    from app.services.images import _landlock

    class NoLandlock:  # a kernel without it: landlock_create_ruleset reports no ABI
        @staticmethod
        def syscall(*_args):
            return -1

    assert _landlock(NoLandlock(), required=True) is False
    assert _landlock(NoLandlock(), required=False) is True


def test_the_poll_interval_cannot_be_set_to_a_busy_loop():
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(jwt_secret="test-secret-not-for-production-0123456789", worker_poll_seconds=0)



async def test_model_values_at_the_edges_still_save(client, alice, run_jobs):
    receipt_id = await new_receipt(client, alice)
    huge = {"description": "Bulk", "quantity": 9999999.9996, "unit_price": 1}
    reply = answer(line_items=[{"description": "Milk", "quantity": 1, "unit_price": 8.23}]).replace("}", ', "note": NaN}', 1)
    await run_jobs(reply)
    assert (await get(client, alice, receipt_id))["status"] == "needs_review"  # NaN in an extra key is dropped
    with pytest.raises(ValidationError):
        ExtractedLineItem.model_validate(huge)  # would round past Numeric(10,3): refused, not a failed save



# Cursor pagination and query indexes.


async def _all_pages(client, headers, limit):
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": limit, **({"cursor": cursor} if cursor else {})}
        page = (await client.get("/receipts", headers=headers, params=params)).json()
        seen += [r["id"] for r in page["items"]]
        pages += 1
        if not (cursor := page["next_cursor"]):
            return seen, pages


async def test_the_receipt_list_pages_newest_first_without_gaps_or_repeats(client, alice):
    ids = [await new_receipt(client, alice, make_image(size=(400 + i, 600))) for i in range(5)]
    seen, pages = await _all_pages(client, alice, limit=2)
    assert seen == ids[::-1] and pages == 3


async def test_uploads_arriving_mid_scroll_do_not_shift_later_pages(client, alice):
    ids = [await new_receipt(client, alice, make_image(size=(400 + i, 600))) for i in range(4)]
    first = (await client.get("/receipts", headers=alice, params={"limit": 2})).json()
    await new_receipt(client, alice, make_image(size=(500, 600)))  # arrives while the user scrolls
    rest = (await client.get("/receipts", headers=alice, params={"limit": 2, "cursor": first["next_cursor"]})).json()
    assert [r["id"] for r in first["items"] + rest["items"]] == ids[::-1]  # nothing repeated or skipped


async def test_a_cursor_is_validated_and_only_ever_reads_your_own_receipts(client, alice):
    await new_receipt(client, alice)
    bob = await auth_headers(client, "bob@example.com")
    for bad in ("not-a-cursor", "AAAA", base64.urlsafe_b64encode(b"2026-01-01|nope").decode()):
        assert (await client.get("/receipts", headers=alice, params={"cursor": bad})).status_code == 400
    far_future = encode_cursor(utcnow() + dt.timedelta(days=1), uuid.uuid4())  # past every alice receipt
    assert (await client.get("/receipts", headers=bob, params={"cursor": far_future})).json()["items"] == []
    assert (await client.get("/receipts", headers=alice, params={"limit": 101})).status_code == 422


async def test_every_query_the_app_runs_uses_an_index(client, alice, run_jobs, monkeypatch):
    """Drive every flow, record each SQL statement, and EXPLAIN it with sequential scans disabled:
    a 'Seq Scan' left in a plan means no index can serve that query."""
    from sqlalchemy import event

    statements: list[tuple[str, object]] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if not executemany and statement.lstrip().upper().startswith(("SELECT", "UPDATE", "DELETE")):
            statements.append((statement, parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        bob = await auth_headers(client, "bob@example.com")
        first = await new_receipt(client, alice)
        await new_receipt(client, alice, make_image(size=(420, 600)), idempotency_key="key-for-index-test")
        await upload(client, alice)  # duplicate photo
        await new_receipt(client, bob)
        await run_jobs(GOOD, GOOD, GOOD)
        await client.get("/auth/me", headers=alice)
        page = (await client.get("/receipts", headers=alice, params={"limit": 1})).json()
        await client.get("/receipts", headers=alice, params={"limit": 1, "cursor": page["next_cursor"]})
        await client.get("/receipts/summary", headers=alice)
        await get(client, alice, first)
        await client.get(f"/receipts/{first}/image", headers=alice)
        await put(client, alice, first)
        await client.post(f"/receipts/{page['items'][0]['id']}/extract", headers=alice)
        await client.delete(f"/receipts/{first}", headers=alice)
        await purge_rejected_uploads()
        await prune_usage_log()
        await client.post("/auth/login", json={"email": "alice@example.com", "password": "correct-horse"})
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    app_tables = ("receipts", "receipt_images", "line_items", "usage_events", "users", "prompts")
    checked, unindexed = 0, []
    async with engine.connect() as conn:
        await conn.exec_driver_sql("SET enable_seqscan = off")
        for statement, parameters in statements:
            if not any(f" {t}" in statement for t in app_tables):
                continue  # now(), advisory locks, version checks
            plan = "\n".join(row[0] for row in (await conn.exec_driver_sql("EXPLAIN " + statement, parameters)).all())
            checked += 1
            if "Seq Scan" in plan:
                unindexed.append(f"{statement}\n{plan}")
    assert checked > 20
    assert not unindexed, "\n\n".join(unindexed)


async def test_every_foreign_key_column_is_indexed():
    """Postgres runs the ON DELETE CASCADE / SET NULL lookups itself: each referencing column needs an index."""
    query = text("""
        SELECT c.conrelid::regclass::text, a.attname
        FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
        WHERE c.contype = 'f' AND NOT EXISTS (
            SELECT 1 FROM pg_index i WHERE i.indrelid = c.conrelid AND i.indkey[0] = c.conkey[1]
        )
    """)
    async with engine.connect() as conn:
        assert (await conn.execute(query)).all() == []
