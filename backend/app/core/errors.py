"""Domain exceptions raised by services; api/errors.py maps them to HTTP. Messages are user-safe."""


class DomainError(Exception):
    def __init__(self, message: str, **extra: object) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra  # extra JSON fields for the client, e.g. the id of an existing receipt


class NotFound(DomainError):
    pass


class InvalidStateTransition(DomainError):
    """E.g. confirming a receipt while extraction is still running."""


class EmailAlreadyRegistered(DomainError):
    pass


class InvalidCredentials(DomainError):
    pass


class UnsupportedImage(DomainError):
    pass


class ImageTooLarge(DomainError):
    pass


class EmptyUpload(DomainError):
    pass


class IdempotencyKeyReused(DomainError):
    """The same Idempotency-Key was sent with a different file."""


class RateLimited(DomainError):
    pass


class DuplicateUpload(DomainError):
    """This user already uploaded this exact file; `receipt_id` is the existing receipt."""


class QuotaExceeded(DomainError):
    pass


class CaptchaFailed(DomainError):
    pass


class ServiceBusy(DomainError):
    """Too much is already waiting (e.g. password hashing); the client should retry shortly."""


class InvalidCursor(DomainError):
    """A pagination cursor that wasn't issued by this API (or was altered)."""
