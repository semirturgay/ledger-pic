# Configuration

All settings are environment variables. Copy `.env.example` to `.env`: docker compose reads it for every service, the web app included. (For `npm run dev` outside Docker, the frontend reads `frontend/.env.local` instead.) Anything not in your `.env` uses the default shown here.

"Used by" says which process reads the setting: the **API**, the **worker**, docker **compose**, or the **frontend**.

## Required

| Variable | Used by | Purpose |
|---|---|---|
| `JWT_SECRET` | API | Token signing key: at least 32 characters, not guessable. Generate with `openssl rand -hex 32`. |
| `POSTGRES_PASSWORD` | compose | Password the compose database is created with. |
| `NEXT_PUBLIC_API_URL` | frontend | Backend base URL, default `http://localhost:8000`. Built into the app, so rebuild (`docker compose up -d --build frontend`) or redeploy after changing it. |

## Vision model

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `EXTRACTION_PROVIDER` | worker | `gemini` | `gemini` or `claude` |
| `GEMINI_API_KEY` | worker | | Google AI Studio key. Without it, extraction fails and users fill fields in by hand. |
| `GEMINI_MODEL` | worker | `gemini-3.6-flash` | |
| `CLAUDE_API_KEY` | worker | | Anthropic key, when the provider is `claude` |
| `CLAUDE_MODEL` | worker | `claude-haiku-5-5` | |
| `AI_TIMEOUT_SECONDS` | worker | `120` | Time limit per model call |
| `AI_MAX_CONCURRENCY` | worker | `4` | Model calls at the same time, per worker process |

## Worker

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `WORKER_CONCURRENCY` | worker | `4` | Jobs processed at the same time, per worker process |
| `WORKER_POLL_SECONDS` | worker | `60` | How often an idle worker checks for jobs. New uploads wake it at once; this catches scheduled retries and crashed jobs (minimum 0.2). |
| `EXTRACTION_MAX_ATTEMPTS` | worker | `5` | Tries per receipt when the model is temporarily unavailable |
| `IMAGE_DECODE_TIMEOUT_SECONDS` | worker | `30` | Time limit for decoding one photo |
| `MAX_IMAGE_EDGE` | worker | `1600` | Photos are shrunk to this many pixels on the long side |
| `DECODER_REQUIRE_LANDLOCK` | worker | `true` | Refuse to decode photos on kernels without Landlock (Linux 5.13+). See [security](security.md). |

## Web, network and sign-in

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | API, worker | | Postgres URL. `postgres://` URLs from hosting providers work too. Compose sets it for you. |
| `CORS_ORIGINS` | API | `http://localhost:3000` | Allowed frontend origins, comma-separated |
| `CORS_ORIGIN_REGEX` | API | | Optional pattern, e.g. for Vercel preview URLs. Anchor it to your team. |
| `FORWARDED_ALLOW_IPS` | API | loopback (compose: the tunnel) | Proxies allowed to set `X-Forwarded-For`. Set to your host's proxy range; never `*`. |
| `API_BIND` | compose | `127.0.0.1` | Interface the API and web app are published on. `0.0.0.0` to test from a phone on your Wi-Fi. |
| `API_DOCS` | API | `false` | Serve `/docs` and `/openapi.json`. Local development only. |
| `TURNSTILE_SECRET_KEY` | API | | Cloudflare Turnstile on sign-up and sign-in. Empty turns it off. |
| `TURNSTILE_HOSTNAMES` | API | | Your frontend's hostname(s). Required whenever a real Turnstile secret is set. |
| `NEXT_PUBLIC_TURNSTILE_SITE_KEY` | frontend | | The matching Turnstile site key. Needed whenever `TURNSTILE_SECRET_KEY` is set, or sign-in is refused. |

## Limits

These have safe defaults. Change them only if you need to.

| Variable | Default | Purpose |
|---|---|---|
| `MAX_UPLOAD_BYTES` | 10 MB | Largest photo accepted |
| `UPLOADS_PER_MINUTE` | `20` | Per user |
| `UPLOADS_PER_DAY`, `REEXTRACTS_PER_DAY` | `100`, `30` | Per user |
| `IMAGE_STORAGE_LIMIT_MB` | `300` | Stored photos per user |
| `NEW_ACCOUNT_HOURS` | `24` | How long an account counts as new |
| `NEW_ACCOUNT_UPLOADS_PER_DAY`, `NEW_ACCOUNT_REEXTRACTS_PER_DAY` | `20`, `5` | Smaller allowances for new accounts |
| `REGISTRATIONS_PER_IP_PER_DAY` | `5` | New accounts per address |
| `AI_JOBS_PER_DAY_TOTAL` | `2000` | Model jobs per day across all users: a ceiling on AI spend |
| `TRUSTED_ACCOUNT_DAYS`, `UNTRUSTED_ACCOUNTS_JOBS_PER_DAY_TOTAL` | `14`, `1000` | Accounts younger than this share one smaller daily pool |
| `GUARANTEED_JOBS_PER_DAY`, `AI_JOBS_PER_DAY_HARD_TOTAL` | `10`, `3000` | What each established account still gets once the shared pool is used up, and the hard ceiling above it |
| `LOGIN_ATTEMPTS_PER_ACCOUNT_AND_IP_PER_15_MINUTES` | `10` | Wrong passwords per account, from one address |
| `LOGIN_ATTEMPTS_PER_ACCOUNT_PER_15_MINUTES` | `100` | Wrong passwords per account, from all addresses |
| `LOGIN_ATTEMPTS_PER_DEVICE_PER_15_MINUTES` | `10` | Wrong passwords from a browser that signed in before |
| `DEVICE_TOKEN_DAYS` | `180` | How long a browser is remembered for sign-in limits |
