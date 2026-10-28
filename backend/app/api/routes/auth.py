from fastapi import APIRouter, Depends, Request, status

from app.api.dependencies import Auth, CurrentUser, auth_rate_limit, client_ip, rate_limit_key
from app.core.config import settings
from app.core.errors import InvalidCredentials, RateLimited
from app.core.rate_limit import auth_limiter
from app.core.security import create_access_token, create_device_token, decode_device_token
from app.models import User
from app.schemas.auth import LoginIn, RegisterIn, TokenOut, UserOut
from app.services.captcha import verify_captcha

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=TokenOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(auth_rate_limit)],
)
async def register(body: RegisterIn, request: Request, auth: Auth) -> TokenOut:
    await verify_captcha(body.captcha_token, client_ip(request))
    key = f"register:{rate_limit_key(request)}"
    if not auth_limiter.allow(key, settings.registrations_per_ip_per_day, 24 * 60 * 60):
        raise RateLimited("Too many new accounts from this network today. Try again tomorrow.")
    try:
        user = await auth.register(body.email, body.password)
    except BaseException:
        auth_limiter.forgive(key)  # only accounts actually created count
        raise
    return TokenOut(access_token=create_access_token(user.id), device_token=create_device_token(user.id))


@router.post("/login", response_model=TokenOut, dependencies=[Depends(auth_rate_limit)])
async def login(body: LoginIn, request: Request, auth: Auth) -> TokenOut:
    # Human check first, and only wrong passwords count against the account: requests that never
    # pass the check (or never reach the password) can't be used to lock someone else out.
    await verify_captcha(body.captcha_token, client_ip(request))
    # Slots are reserved before the (slow) password check, so simultaneous guesses can't all slip past
    # the limits; they are given back unless the password turns out to be wrong. A browser that signed in
    # to this account before presents its device token and is limited on its own, so strangers' guesses
    # (from anywhere, even the owner's own shared address) never lock the owner out of their devices.
    # Everyone else is limited per account and address, and per account across all addresses.
    device = decode_device_token(body.device_token) if body.device_token else None
    if device is not None and device.user_id == await auth.user_id_for(body.email):
        limits = [(f"login:device:{device.device_id}", settings.login_attempts_per_device_per_15_minutes)]
    else:
        device = None
        limits = [
            (f"login:{body.email}:{rate_limit_key(request)}", settings.login_attempts_per_account_and_ip_per_15_minutes),
            (f"login:{body.email}", settings.login_attempts_per_account_per_15_minutes),
        ]
    reserved: list[str] = []
    for key, limit in limits:
        if not auth_limiter.allow(key, limit, 15 * 60):
            for held in reserved:
                auth_limiter.forgive(held)
            raise RateLimited("Too many sign-in attempts for this account. Try again in 15 minutes.")
        reserved.append(key)
    try:
        user = await auth.login(body.email, body.password)
    except InvalidCredentials:
        raise  # a wrong password keeps its slots
    except BaseException:
        for held in reserved:
            auth_limiter.forgive(held)
        raise
    for held in reserved:
        auth_limiter.forgive(held)
    # A recognised device keeps its token; anyone else gets one for next time.
    device_token = body.device_token if device is not None else create_device_token(user.id)
    return TokenOut(access_token=create_access_token(user.id), device_token=device_token)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user
