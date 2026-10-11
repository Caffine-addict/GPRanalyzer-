# Hosting GPR Studio

The Studio runs on one machine and listens only on 127.0.0.1. Everyone signs in with their own
account, and every attempt is logged in Postgres. Remote access (an HTTPS address for phones and
teammates) is being chosen; Tailscale was tried on 2026-10-08 and removed.

## Once

1. **Postgres** (Docker Desktop running, set to start at login):
   `docker compose up -d`. It reads `POSTGRES_PASSWORD`, `DATABASE_URL` and `TEST_DATABASE_URL`
   from `.env` (see `.env.example`). It listens on 127.0.0.1 only.
2. **Accounts**, one per person, never shared:
   `.venv/bin/python -m studio.users add <name>`. Passwords are typed at a hidden prompt, 12+ characters.
3. **Run the Studio as a service** (starts at login, restarts if it stops):
   `deploy/macos/install_studio_service.sh`, then open http://127.0.0.1:8500.
   Log: `~/Library/Logs/GPRStudio/studio.log`.

## Day to day

| Task | Command |
|---|---|
| Who signed in, when, from where | `python -m studio.users log [--user NAME] [--limit 100]` |
| Accounts and last sign-in | `python -m studio.users list` |
| New password (signs them out everywhere) | `python -m studio.users passwd NAME` |
| Block someone now | `python -m studio.users disable NAME` |
| Back up now | `deploy/backup.sh`: database + annotations into `backups/` (gitignored); `BACKUP_DIR=/Volumes/<drive>/GPR` for another disk |
| Nightly backups (02:30, newest 14 kept) | `deploy/macos/install_backup_service.sh`; log `~/Library/Logs/GPRStudio/backup.log` |
| Restart the Studio after an update | `launchctl kickstart -k gui/$(id -u)/com.gprstudio.studio` |

## What protects what

- Passwords: Argon2id hashes only. Sessions: the cookie is HttpOnly, SameSite=Lax, Secure over
  HTTPS, 12 hours; the database keeps only its SHA-256.
- Five failed attempts for a name (or twenty from one address) in 15 minutes lock sign-in.
- Writes from other websites are refused (Origin / Sec-Fetch-Site checks).
- Backups cover the database and `annotations/` nightly. `Dataset/` (the radar files) is source data:
  keep the original deliveries safe separately. A backup on the same disk does not survive the disk.
- The live-pipeline API (`api/server.py`) has no sign-in: keep it on 127.0.0.1, never expose it.
