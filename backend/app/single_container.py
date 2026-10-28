"""Runs migrations, then the API and the worker side by side in one container: for hosts with no free
background workers (Render's free tier). docker-compose.yml runs them as separate services instead.

Each process gets only the secrets it needs, as in docker-compose.yml: the API never sees the model keys,
the worker never sees the token-signing key. If either process exits, the other is stopped too and the
container exits, so the host restarts both.

Run with: python -m app.single_container
"""

import os
import signal
import subprocess
import sys

from app.core.process import make_undumpable

MODEL_KEYS = ("GEMINI_API_KEY", "CLAUDE_API_KEY")
# Neither migrations nor the worker sign tokens or check CAPTCHAs. Settings still need a well-formed secret.
NO_TOKEN_SECRETS = {"JWT_SECRET": "this-process-does-not-sign-tokens-0123456789", "TURNSTILE_SECRET_KEY": ""}


def environments(env: dict[str, str]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """(migrations, API, worker): each with only the secrets it needs."""
    api = {key: value for key, value in env.items() if key not in MODEL_KEYS}
    return api | NO_TOKEN_SECRETS, api, env | NO_TOKEN_SECRETS


def main() -> int:
    make_undumpable()  # this process holds every secret; the API and worker run as the same user
    env = dict(os.environ)
    migrate_env, api_env, worker_env = environments(env)

    subprocess.run(["alembic", "upgrade", "head"], env=migrate_env, check=True)

    port = env.get("PORT", "8000")
    api = subprocess.Popen(
        ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", port, "--proxy-headers"], env=api_env
    )
    worker = subprocess.Popen([sys.executable, "-m", "app.workers"], env=worker_env)
    children = {api.pid: api, worker.pid: worker}
    stopping = False

    def stop_all(*_: object) -> None:
        nonlocal stopping
        stopping = True
        for child in children.values():
            if child.poll() is None:
                child.send_signal(signal.SIGTERM)  # the worker finishes its running jobs first

    signal.signal(signal.SIGTERM, stop_all)
    signal.signal(signal.SIGINT, stop_all)

    # As PID 1 this process also reaps orphans it never started; only its own children end the wait.
    while True:
        pid, status = os.wait()
        if pid in children:
            break
    del children[pid]
    asked_to_stop = stopping
    stop_all()
    (other,) = children.values()
    other.wait()
    # A process stopping on its own is a failure: exit non-zero so the host restarts the container.
    return 0 if asked_to_stop else os.waitstatus_to_exitcode(status) or 1


if __name__ == "__main__":
    sys.exit(main())
