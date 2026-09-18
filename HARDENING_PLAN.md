# BOQ Audit Portal V1 hardening plan

| Issue | Files | Change | Verification |
|---|---|---|---|
| Customer can see draft outputs | `backend/app/api/jobs.py` | Hide output metadata and reject output file access unless the owned job is `COMPLETED`; retain admin access | Processing/review/completed and cross-customer IDOR tests |
| Upload holds a SQLite write lock and reads the whole file unsafely | `backend/app/api/jobs.py`, `backend/app/storage/files.py` | Commit the pending job before storage, read asynchronously in bounded chunks, then update metadata in a short transaction; remove incomplete jobs/files on failure | Upload validation, size, filename and status tests |
| Weak production configuration and automatic demo seed | `backend/app/core/config.py`, `backend/app/main.py`, env examples | Fail startup/config creation for weak secrets/passwords/insecure cookies; seed demo only when explicitly configured | Production settings tests and startup behavior |
| SQLite connection resilience | `backend/app/core/database.py` | Enable foreign keys, busy timeout and WAL per database connection | PRAGMA test and full backend suite |
| Login brute force | `backend/app/api/auth.py` | Add bounded in-memory failed-login limiter per client IP | 429 regression test |
| Weak CLI password path | `backend/app/cli.py` | Enforce a 12-character minimum on create/reset | Focused unit test/manual CLI validation |
| Shallow health endpoint | `backend/app/main.py` | Check a lightweight DB query and writable data directory; return 503 on failure | Health endpoint test |
| Proxy/container operational hardening | `Caddyfile`, `docker-compose.yml`, Dockerfiles (review) | Limit upload request bodies, rotate logs, retain persistent bind mount, document the existing bind-mount constraint on backend non-root conversion | `docker compose config`, builds |
| Unsafe live SQLite backup | `scripts/backup.sh` | Snapshot SQLite with its backup API before archiving database and jobs | Shell syntax and backup smoke test |
| Environment/deployment documentation | `.gitignore`, `.env.example`, `.env.production.example`, `README.md` | Safe placeholders and Compute Engine/Caddy HTTPS instructions | Git tracking check and documentation review |

The customer workflow remains `upload -> PROCESSING -> COMPLETED`; no admin start step or automated processing is introduced.
