# Pinned by digest so a rebuild gets exactly these bytes; bump tag and digest together.
FROM python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f

COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dependencies first (cached until pyproject.toml or uv.lock change), exactly as locked.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY . .

RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8000

# PORT is set by hosts like Render/Railway; defaults to 8000 locally.
# X-Forwarded-For is trusted only from FORWARDED_ALLOW_IPS (uvicorn reads it; default loopback only).
# Set it to your proxy's address range, never '*': the client IP keys the login rate limit.
# The worker service runs the same image with: python -m app.workers
# exec: uvicorn replaces the shell as PID 1, so it receives SIGTERM and drains requests on shutdown.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
