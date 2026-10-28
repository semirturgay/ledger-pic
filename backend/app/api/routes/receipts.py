import uuid

from fastapi import APIRouter, File, Header, Query, Response, UploadFile, status

from app.api.dependencies import CurrentUser, Receipts
from app.core.config import settings
from app.schemas.receipt import ReceiptListItem, ReceiptOut, ReceiptPage, ReceiptUpdate, SummaryOut

router = APIRouter(prefix="/receipts", tags=["receipts"])


@router.post("", response_model=ReceiptOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_receipt(
    user: CurrentUser,
    service: Receipts,
    response: Response,
    file: UploadFile = File(...),
    # Client-generated key per file: retrying with the same key returns the original receipt.
    idempotency_key: str | None = Header(None, pattern=r"^[A-Za-z0-9_-]{8,64}$"),
) -> ReceiptOut:
    """Store the image and queue extraction. Returns at once; poll until status leaves `processing`."""
    # Read one byte past the limit so the service can tell "at the limit" from "over it".
    raw = await file.read(settings.max_upload_bytes + 1)
    receipt, replayed = await service.create_from_upload(user, raw, idempotency_key)
    if replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    return ReceiptOut.from_receipt(receipt)


@router.get("", response_model=ReceiptPage)
async def list_receipts(
    user: CurrentUser,
    service: Receipts,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, max_length=200, description="next_cursor from the previous page"),
) -> ReceiptPage:
    items, next_cursor = await service.list(user, limit, cursor)
    return ReceiptPage(items=[ReceiptListItem.model_validate(r) for r in items], next_cursor=next_cursor)


@router.get("/summary", response_model=SummaryOut)
async def summary(
    user: CurrentUser,
    service: Receipts,
    month: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM, defaults to now"),
) -> SummaryOut:
    return await service.summary(user, month)


@router.get("/{receipt_id}", response_model=ReceiptOut)
async def get_receipt(
    user: CurrentUser,
    service: Receipts,
    receipt_id: uuid.UUID,
) -> ReceiptOut:
    return ReceiptOut.from_receipt(await service.get(user, receipt_id))


@router.get("/{receipt_id}/image")
async def get_receipt_image(
    user: CurrentUser,
    service: Receipts,
    receipt_id: uuid.UUID,
) -> Response:
    image = await service.get_image(user, receipt_id)
    # Not cached for long: the worker replaces the original upload with the downscaled version.
    return Response(image.data, media_type=image.content_type, headers={"Cache-Control": "private, max-age=60"})


@router.put("/{receipt_id}", response_model=ReceiptOut)
async def confirm_receipt(
    user: CurrentUser,
    service: Receipts,
    receipt_id: uuid.UUID,
    body: ReceiptUpdate,
) -> ReceiptOut:
    """Save the corrected record and mark it confirmed. Idempotent: repeating it stores the same state."""
    return ReceiptOut.from_receipt(await service.confirm(user, receipt_id, body))


@router.post("/{receipt_id}/extract", response_model=ReceiptOut, status_code=status.HTTP_202_ACCEPTED)
async def retry_extraction(
    user: CurrentUser,
    service: Receipts,
    receipt_id: uuid.UUID,
) -> ReceiptOut:
    """Put an unconfirmed receipt back on the extraction queue with a fresh set of attempts."""
    return ReceiptOut.from_receipt(await service.request_extraction(user, receipt_id))


@router.delete("/{receipt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_receipt(
    user: CurrentUser,
    service: Receipts,
    receipt_id: uuid.UUID,
) -> Response:
    await service.delete(user, receipt_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
