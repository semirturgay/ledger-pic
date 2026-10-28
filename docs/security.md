# Security

## What protects users

- **Your data is private.** Every database query is limited to the signed-in user. Asking for someone else's receipt gives "not found".
- **Passwords** are stored as strong one-way hashes (Argon2id), never as plain text.
- **Password guessing is limited.** There are limits per address and per account. A browser you've signed in on before keeps its own limit, so someone guessing your password can't lock you out of it.
- **Bots** are stopped at sign-up and login with Cloudflare Turnstile.
- **The server refuses to start** with a weak secret key, or with bot protection set up wrong.

## What protects the service

- **Limits per user:** 20 uploads a minute, 100 uploads and 30 retries a day, and 300 MB of photos.
- **New accounts** get smaller limits, and each address can create only 5 accounts a day.
- **AI cost:** there's a daily cap on AI jobs across all users. New accounts share a smaller part of it, so they can't use it all up.
- **Uploads** must come with a valid login before the server reads them, and are cut off at 10 MB.
- **Fair queue:** one user's big batch can't hold up everyone else.

## Opening uploaded images safely

A photo could be built to attack the image library, so it's never opened by the main worker. A separate, short-lived process opens it, and that process:

- has no passwords or keys
- has limits on CPU, memory and time, and is killed if it goes over
- on Linux, can't use the network, start programs, read other files, or touch other processes
- refuses to run if it can't lock itself down
- runs one at a time, so it never runs next to another user's photo

Photos that are too big, or built to be slow to open, are rejected at upload.

## The AI's answers

The AI's answer is treated as untrusted. It's checked against a strict format, cleaned, only written to your own draft, and shown as plain text.

## The rest of the setup

- Containers don't run as root, and have no extra system permissions.
- The database is only reachable from the machine itself.
- Only the worker has the AI keys.
- The website blocks scripts from other sites, and can't be shown inside other sites.
- Offline copies are kept per account, and deleted on sign-in and sign-out.
- Dependencies, base images and CI actions are pinned to exact versions.

## Audit

We checked the code nine times with Cloudflare's [security-audit tool](https://github.com/cloudflare/security-audit-skill). We fixed every finding and added a test for each, except one risk we chose to accept (below). The tool reads the code but couldn't run it, so we tested the image sandbox by hand in the worker container. A clean check doesn't prove there's nothing left.

## Trade-offs we chose

These are fine for a small app. Each says what to change if it grows.

- **The job queue lives in the database.** Workers are woken up with Postgres `NOTIFY`, and also check every 60 seconds. A wake-up can be missed, but the check catches it, up to a minute later. For heavy traffic, move to a real queue that confirms every delivery (like SQS or RabbitMQ).
- **Fake accounts can still use up a day's AI budget,** if someone creates around a hundred and waits two weeks. Fixing that needs email or phone checks, or payment.
- **New browser during an attack:** while someone is guessing your password, you can't log in from a browser you've never used, for up to 15 minutes. Browsers you've used still work.
- **Logins can't be cancelled early.** A login lasts 24 hours.
- **Sign-up shows if an email is taken.** Rate limits and bot protection slow down anyone abusing this.
- **Some limits are kept in memory,** so each server instance has its own, and a restart resets them. Daily limits are in the database and always hold.
- **The website allows its own inline scripts,** because Next.js needs them.
- **Image sandbox needs Linux 5.13+.** On older systems the worker won't open images, unless you set `DECODER_REQUIRE_LANDLOCK=false`.
