# Hosting GPR Studio for your crew (one PC + Tailscale)

The Studio runs on one machine and listens only on 127.0.0.1. Tailscale gives it a private HTTPS
address that only devices signed in to your tailnet can reach; phones need HTTPS for GPS in Field
mode. Everyone signs in with their own account, and every attempt is logged in Postgres.

## Once

1. **Postgres** (Docker Desktop running):
   `docker compose up -d`. It reads `POSTGRES_PASSWORD`, `DATABASE_URL` and `TEST_DATABASE_URL`
   from `.env` (see `.env.example`). It listens on 127.0.0.1 only.
2. **Accounts**, one per person, never shared:
   `.venv/bin/python -m studio.users add <name>`. Passwords are typed at a hidden prompt, 12+ characters.
3. **Start the Studio**: `.venv/bin/python -m studio` (port 8500). It refuses to start without the
   database, and refuses every page without a sign-in.
4. **Tailscale**: `brew install --cask tailscale-app` (asks for your Mac password), open
   Tailscale, sign in. In the admin console (login.tailscale.com) under **DNS**, turn on
   **MagicDNS** and **HTTPS Certificates**.
5. **Publish to the tailnet**: `scripts/serve_tailnet.sh`. It prints the address,
   `https://<this-machine>.<tailnet>.ts.net`.
6. **Phones and laptops**: install Tailscale, sign in to the same tailnet (or share this machine
   with them from the admin console), open the address, sign in to the Studio.

## Day to day

| Task | Command |
|---|---|
| Who signed in, when, from where | `python -m studio.users log [--user NAME] [--limit 100]` |
| Accounts and last sign-in | `python -m studio.users list` |
| New password (signs them out everywhere) | `python -m studio.users passwd NAME` |
| Block someone now | `python -m studio.users disable NAME` |
| Back up the database | `deploy/postgres/backup.sh` (writes `backups/`, gitignored) |
| Stop sharing | `tailscale serve reset` |

## What protects what

- Passwords: Argon2id hashes only. Sessions: the cookie is HttpOnly, SameSite=Lax, Secure over
  HTTPS, 12 hours; the database keeps only its SHA-256.
- Five failed attempts for a name (or twenty from one address) in 15 minutes lock sign-in.
- Writes from other websites are refused (Origin / Sec-Fetch-Site checks).
- Not covered: `Dataset/` and `annotations/` are files on this PC; back them up with the PC.
- The live-pipeline API (`api/server.py`) has no sign-in: keep it on 127.0.0.1, never serve it.
