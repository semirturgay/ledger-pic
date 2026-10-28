"""Cloudflare Turnstile check for register and login. Off when TURNSTILE_SECRET_KEY is empty."""

import logging

import httpx

from app.core.config import settings
from app.core.errors import CaptchaFailed

log = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
EXPECTED_ACTION = "auth"  # set by the widget in frontend/src/components/Turnstile.tsx


async def verify_captcha(token: str | None, client_ip: str | None, transport: httpx.AsyncBaseTransport | None = None) -> None:
    if not settings.turnstile_secret_key:
        return
    if not token:
        raise CaptchaFailed("Please complete the human check.")
    form = {"secret": settings.turnstile_secret_key, "response": token}
    if client_ip:
        form["remoteip"] = client_ip
    try:
        async with httpx.AsyncClient(timeout=10, transport=transport) as client:
            response = await client.post(VERIFY_URL, data=form)
            outcome = response.json()
        if not isinstance(outcome, dict):
            raise ValueError("unexpected siteverify response")
    except (httpx.HTTPError, ValueError) as exc:
        # Fail closed: an unverifiable token is not a pass.
        log.warning("Turnstile verification unavailable: %r", exc)
        raise CaptchaFailed("Couldn't verify the human check. Try again.") from exc
    if not outcome.get("success"):
        log.info("Turnstile rejected a token: %s", outcome.get("error-codes"))
        raise CaptchaFailed("The human check failed or expired. Try again.")
    # A valid token solved on some other page with our site key, or for another action, is not a pass.
    hostnames = settings.turnstile_hostname_set
    if hostnames and str(outcome.get("hostname", "")).lower() not in hostnames:
        log.info("Turnstile token from unexpected hostname %r", outcome.get("hostname"))
        raise CaptchaFailed("The human check failed or expired. Try again.")
    allowed_actions = {EXPECTED_ACTION} if hostnames else {None, "", EXPECTED_ACTION}  # test keys send no action
    if outcome.get("action") not in allowed_actions:
        log.info("Turnstile token for unexpected action %r", outcome.get("action"))
        raise CaptchaFailed("The human check failed or expired. Try again.")
