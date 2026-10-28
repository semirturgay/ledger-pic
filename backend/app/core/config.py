import re
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Cloudflare's published always-pass/always-fail Turnstile test secrets (local development only).
CLOUDFLARE_TEST_SECRETS = {f"{n}x0000000000000000000000000000000AA" for n in (1, 2, 3)}


class Settings(BaseSettings):
    # .env lives at the repo root; the second entry covers running from inside backend/.
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = "postgresql+psycopg://ledgerpic:ledgerpic@localhost:5432/ledgerpic"
    log_level: str = "INFO"
    api_docs: bool = False  # interactive /docs and /openapi.json; API_DOCS=true for local development

    jwt_secret: str = Field(min_length=32)
    jwt_expire_minutes: int = 60 * 24
    # Per client IP, per process. See core/rate_limit.py for why that is enough here.
    auth_rate_limit_per_minute: int = 10
    # Wrong passwords per account: 10 from one address and 100 from all addresses together, so spreading
    # guesses over many IPs doesn't help. A browser that signed in to the account before sends its device
    # token instead and gets its own 10, so strangers' guesses never lock the owner out of their devices.
    login_attempts_per_account_and_ip_per_15_minutes: int = 10
    login_attempts_per_account_per_15_minutes: int = 100
    login_attempts_per_device_per_15_minutes: int = 10
    device_token_days: int = 180
    # Cloudflare Turnstile on register/login. Empty secret = CAPTCHA off (local dev, tests).
    turnstile_secret_key: str = ""
    # Comma-separated hostnames a token must have been solved on (e.g. ledgerpic.vercel.app), so a token
    # from another site using the same site key is refused. Empty: not checked (local development).
    turnstile_hostnames: str = ""

    # Anti-abuse: LedgerPic stores receipts, not photo albums.
    uploads_per_minute: int = 20  # per user, per process (burst protection)
    uploads_per_day: int = 100  # per user, from the append-only usage log (holds across instances and deletes)
    reextracts_per_day: int = 30  # per user, "Try again" on a draft or a failed receipt
    # All users together (uploads + re-runs): a ceiling on model spend even if many accounts are created.
    ai_jobs_per_day_total: int = 2000
    # During an account's first day, smaller allowances, so a batch of throwaway accounts can't drain the
    # global ceiling; and at most this many sign-ups per address per day.
    new_account_hours: int = 24
    new_account_uploads_per_day: int = 20
    new_account_reextracts_per_day: int = 5
    registrations_per_ip_per_day: int = 5
    # All accounts younger than trusted_account_days together: throwaway accounts (even ones left to age
    # a day or two) hit this, not the global ceiling, so the rest of it stays for established users.
    trusted_account_days: int = 14
    untrusted_accounts_jobs_per_day_total: int = 1000
    # Once the shared pool is used up, each established account still gets this many jobs a day, up to
    # the hard ceiling: throwaway accounts left to age can pause the pool, not stop real users.
    guaranteed_jobs_per_day: int = 10
    ai_jobs_per_day_hard_total: int = 3000
    image_storage_limit_mb: int = 300  # per user, total stored image bytes
    rejected_upload_retention_days: int = 7  # not-a-receipt / unreadable uploads are then deleted

    # Which vision model reads receipts.
    extraction_provider: Literal["gemini", "claude"] = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    claude_api_key: str = ""  # env CLAUDE_API_KEY
    claude_model: str = "claude-haiku-5-5"  # cheapest current Claude with vision and structured outputs
    ai_timeout_seconds: int = 120  # per model call
    # How long a worker keeps the active prompt in memory; a newly activated version is live within this.
    prompt_cache_seconds: int = 60
    # Simultaneous model calls per worker process.
    ai_max_concurrency: int = 4

    # Extraction queue (app/workers/). Transient model errors are retried with backoff up to this many tries.
    extraction_max_attempts: int = 5
    worker_concurrency: int = 4
    # Fallback poll interval. Uploads wake idle workers through Postgres NOTIFY; polling catches what NOTIFY
    # doesn't announce: scheduled retries, jobs whose lease ran out, and wake-ups missed during a reconnect.
    worker_poll_seconds: float = Field(default=60.0, ge=0.2)
    # Decode images only where the kernel can confine the decoder's file access (Landlock, Linux 5.13+).
    # Turning this off on an older kernel means a hijacked decoder could read any same-user process's
    # environment in the worker's container; only do it where the worker is the only such process.
    decoder_require_landlock: bool = True

    max_upload_bytes: int = 10 * 1024 * 1024
    # Longest image edge sent to the model and stored. Keeps tokens, cost and storage bounded.
    max_image_edge: int = 1600
    # Wall-clock limit for decoding one upload in the worker (a 12 MP photo takes well under a second).
    image_decode_timeout_seconds: float = 30

    # Comma-separated exact origins, plus an optional regex (e.g. for Vercel preview URLs).
    cors_origins: str = "http://localhost:3000"
    cors_origin_regex: str = ""

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str) -> str:
        # Hosted Postgres providers hand out postgres:// URLs; SQLAlchemy needs the driver named.
        return re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", value)

    @field_validator("jwt_secret")
    @classmethod
    def _secret_is_not_trivial(cls, value: str) -> str:
        # Length alone accepts "aaaa…": anything that guessable lets anyone forge tokens offline.
        if len(set(value)) < 10:
            raise ValueError("JWT_SECRET is too predictable; generate one with: openssl rand -hex 32")
        return value

    @model_validator(mode="after")
    def _turnstile_is_bound_to_our_site(self) -> "Settings":
        # A real secret without hostnames would accept tokens solved on any site using the same key.
        if self.turnstile_secret_key and not self.turnstile_hostname_set and self.turnstile_secret_key not in CLOUDFLARE_TEST_SECRETS:
            raise ValueError("TURNSTILE_HOSTNAMES must list your frontend's hostname(s) when TURNSTILE_SECRET_KEY is set")
        return self

    @property
    def turnstile_hostname_set(self) -> set[str]:
        return {h.strip().lower() for h in self.turnstile_hostnames.split(",") if h.strip()}

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
