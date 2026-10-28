import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegisterIn(BaseModel):
    email: EmailStr = Field(max_length=320)
    password: str = Field(min_length=8, max_length=128)
    # Cloudflare Turnstile token from the form; required only when the server has a Turnstile secret.
    captcha_token: str | None = Field(None, max_length=2048)

    @field_validator("email", mode="after")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.lower()


class LoginIn(RegisterIn):
    password: str = Field(min_length=1, max_length=128)
    # From an earlier sign-in on this browser (TokenOut.device_token).
    device_token: str | None = Field(None, max_length=2048)


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    # Keep it on this device and send it with later sign-ins (see LoginIn).
    device_token: str | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
