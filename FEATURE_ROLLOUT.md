# Feature rollout

Implemented on 2026-10-05. Backend and frontend were updated on this machine after adding admin account management; a SQLite backup was taken and there were no queued/running audits or pending chat messages before restart.

## Admin account management

`/admin/users` supports customer creation with an admin-specified password (12–200 characters), edits, password resets, activation/deactivation and deletion. Admin accounts are excluded and cannot be changed through these endpoints. Password/username changes and deactivation revoke sessions. Deletion is allowed only without jobs; accounts with jobs can be deactivated without removing their files or chat history. No schema migration or new dependency is needed.

Verification: all 57 backend checks pass, frontend typecheck/build pass, and the deployed authenticated users API, admin page and backend health were checked successfully. The temporary verification session was removed; existing customer accounts were not changed.

## What changes

- Customers have one usable login session. A new browser login replaces the previous session; tabs sharing one cookie work together. Admins retain multiple sessions. Account and IP login limits are enforced independently.
- Each worker attempt has durable timing records. Customer/admin pages show total turnaround, cumulative measured AI time, and the current attempt. Failed attempts remain in totals after retry. Interrupted and historical measurements are labeled incomplete/unavailable. Time-based simulated audit stages have been removed.
- Project pages show Vietnamese SBTech AI chat in the left sidebar (opened with **Chat** on mobile). Messages survive refresh, with a 2000-character question limit and a default 20 sends/hour/account. One active chat turn is allowed globally for this single-backend deployment.
- Only approved customer summaries and bounded text excerpts from the first eight annotated PDF pages enter chat context. Excel cells, drawings, visual details, internal notes and audit logs are not made available. The assistant must acknowledge these limits.
- Customer usage requests use `/api/jobs/ai-capacity`, exposing availability only. `/api/jobs/codex-usage` is admin-only. Both endpoints query Codex usage only while a job is `PROCESSING`.
- The proxy caps incoming customer chat requests at 16 KB; the private gateway authenticates before parsing and caps streamed context at 200 KB.
- Project publication/status changes reset the provider context; obsolete transcripts are hidden. Late answers are discarded if the project changes or the session is replaced while answering.

## Isolation

`chat` is a separate non-root container. It has no portal database, `/data`, source workspace, Docker socket, or audit-home mount. It has its own credential volume and session volume, and no published port. Backend/gateway communication uses `CHAT_GATEWAY_TOKEN`.

Codex is pinned to 0.159.3 in the chat image. Every thread/turn requires the `sbtech-chat` named permissions profile: filesystem root denied, minimal runtime paths readable, only that conversation's empty workspace readable, auth/home/application/proc denied, network disabled for tools, and no writes or approval escalation. Shell, execution, plugins, apps, image and multi-agent tools are disabled. Unexpected action requests or tool events fail the reply. The provider process receives neither the gateway token nor the application's environment.

The auth initializer copies only `auth.json` from the existing audit login into the dedicated chat credential volume. It does not expose the audit home to chat. Each conversation has a private runtime home; it cannot read another conversation's runtime home through the sandbox. The gateway may contact the inference/auth service; disabling tool networking does not disable model inference.

## Prepare deployment

1. Confirm the intended account's customer-facing deployment arrangement before public launch. Technical integration tests do not establish subscription/resale entitlement.
2. Generate a secret using `openssl rand -hex 32` and put it in the existing `.env` as `CHAT_GATEWAY_TOKEN`. Do not commit it. Set `CHAT_GATEWAY_URL=http://chat:8010`; optional timeout/rate settings are documented in the example env files.
3. Back up the database and job files. Wait for existing audits to finish before restarting: the worker is in-process, and the startup recovery deliberately marks interrupted/submitted work failed for explicit retry.
4. Build the images and initialize chat credentials, then start the services:

```bash
docker compose build backend frontend chat
docker compose --profile chat-setup run --rm chat-auth-init
docker compose up -d backend frontend chat
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
docker compose ps
docker compose exec chat python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8010/health').status)"
```

The initializer assumes the existing backend uses file-based ChatGPT login at `/root/.codex/auth.json`, as verified on this machine. If it expires or needs reauthentication, refresh the existing backend login, rerun the initializer and restart chat. If it is unavailable, chat returns a generic temporary-unavailability error and preserves the question.

If chat reports that it cannot answer, check `docker compose ps`: an unhealthy chat service can mean missing credentials or an empty gateway token. Editing `.env` and running `restart` does not update an existing container's environment. After setting the same token for backend and chat, run the credential initializer above and `docker compose up -d --no-deps --force-recreate chat`. Recreate backend too if its token changed, after active audits finish. Never print the token or `auth.json` while diagnosing.

On this machine Docker bridge DNS failed during package downloads. For a local build only, the successfully tested workaround for chat was `docker build --network=host -t boqaudit-chat chat`. No global Docker network settings were changed.

## Migration and restart behavior

Startup adds `customer_active_sessions`, `audit_runs`, `chat_conversations`, and `chat_messages`. Existing tables/columns and job files are unchanged; no existing-column ALTER migration is needed. Migration is idempotent. Existing customer sessions are reduced to the newest usable session pointer; admin sessions remain valid.

Recovery marks running audit records `INTERRUPTED` without inventing duration, changes in-process queued/processing jobs to `FAILED` for explicit retry, and marks pending chat questions failed. Do not use multiple backend replicas: recovery and the global chat lock assume the existing single-instance SQLite architecture.

## Verification

- Backend pytest suite (54 checks) passes in an ephemeral Python 3.12 container, matching production, with synthetic users/files and no production data/credentials. Host Python 3.14 stalled in AnyIO; container verification avoids that runtime mismatch.
- Frontend TypeScript check and production build pass.
- The new chat image builds; Compose configuration validates.
- Live isolated smoke test with synthetic `SBTECH-SMOKE` metadata passed question/answer and thread resumption. Credential and cross-project read attempts were blocked. Sandbox command execution fails closed on this container host; chat remains functional with commands disabled.
- Existing portal containers remain untouched. Authentication was copied only to a private temporary file for the live smoke test; that copy was deleted afterward.

## Practical limits

Single-session login discourages simultaneous account sharing; it cannot prevent taking turns or sharing a cookie. SBTech AI is a customized assistant, not a claim of independently trained model weights. Backend branding cannot guarantee that users never infer the provider.

Chat/report excerpts are deliberately bounded. Requests for detailed spreadsheet quantities require evidence that this initial chat version does not read. User-facing messages state this limitation instead of inventing an answer.

Chat messages and private provider session records persist locally. The API exposes the latest 40 messages for the current project context and passes at most 12000 characters of recent history when rotating/rebuilding a provider thread. Job deletion cascades portal chat records; private provider session directories require operational retention/cleanup separately.

Official references: [Codex App Server](https://learn.chatgpt.com/docs/app-server), [permissions](https://learn.chatgpt.com/docs/permissions), and [OWASP session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).
