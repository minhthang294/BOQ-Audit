# BOQ Portal feature plan

Date: 2026-10-05. Scope: repository inspection and planning only; no application changes or deployment.

## Findings from the existing code

- FastAPI/SQLAlchemy/SQLite backend; Next.js/React frontend. Reuse this stack.
- `backend/app/api/auth.py` creates database-backed sessions but allows multiple active sessions per account. Logout and password changes already revoke sessions.
- `backend/app/api/jobs.py` sets `started_at` before input storage finishes. Retry overwrites `started_at` and clears `completed_at`.
- `backend/app/services/audit_runner.py` preserves that early start time; it also sets `completed_at` when work needs admin review. Admin completion overwrites this timestamp. These fields therefore cannot distinguish AI processing, waiting, and delivery reliably.
- Customer progress stages in `frontend/app/jobs/[job_code]/page.tsx` are estimated from fixed elapsed-time thresholds, not worker events.
- `backend/app/services/codex_usage.py` already talks to Codex App Server. It only queries account limits; it is not a conversation implementation.
- Customers request `/api/jobs/codex-usage`, which exposes the provider name in browser network tools and returns the shared backend account's plan/usage information.
- Audits run as in-process background tasks. Compose mounts the shared application data and Codex credentials into the backend container; a project working directory alone is not a filesystem security boundary.
- No existing customer chat UI, chat endpoints, or conversation ownership records were found.

## 1. Reduce account sharing

Recommended first release: one active customer session per account.

- After successful password verification, atomically replace that customer's previous session. Enforce one session per customer under concurrent login requests, not only sequential requests. Preserve existing admin behavior initially.
- Reuse database session validation so the replaced session loses API and file access. Show a clear login-page explanation when a session has been replaced.
- Multiple tabs in the same browser continue sharing one session. A second browser/device requires login and replaces the first.
- Keep password-change revocation and existing login rate limiting. Add an account-keyed limit alongside the existing IP limit to reduce distributed login attempts.
- Do not hard-bind sessions to IP addresses: mobile networks and shared offices make this unreliable.
- This discourages simultaneous sharing; it cannot prevent users taking turns or copying an active cookie. If abuse persists, add verified device enrollment/passkeys or MFA and account activity review. Browser fingerprinting is not the first release.

Likely files: auth API, auth dependency/login error handling, rate limiter use, login page, auth tests. Add a database constraint/migration if needed for concurrent enforcement.

Acceptance: browser B replaces A; A cannot list/download projects; failed login leaves A valid; same-browser tabs work; concurrent logins leave only one usable customer session; password changes still revoke it.

Reference: [OWASP session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

## 2. Measure time per project

Show two explicitly named measurements:

- **Total turnaround:** first durable project creation to final delivery; includes upload storage, waiting, review, and retries. While unfinished, show elapsed time. This does not include time spent transferring the request before the server creates the job.
- **AI processing time:** sum of actual worker execution time across all attempts, with current-attempt time shown separately while running.

Implementation:

- Add a small `AuditRun` table linked to the job, recording attempt number, actual start/end, status, and measured execution seconds. Reuse SQLite and SQLAlchemy; no metrics service needed.
- Start execution timing at worker entry, record completion/failure in every exit path, and preserve prior runs on retry. Use a monotonic clock for execution duration and UTC timestamps for display.
- Keep customer delivery timestamps separate from run completion. Audit completion and admin release must not overwrite run records. Handle reopened jobs explicitly so an old delivery time is not presented as a current completion.
- Expose durations through the job response and show them on customer/admin details and lists. A terminal job's timer stops; historical projects without run records show execution time as unavailable rather than fabricated.
- Detect interrupted runs on backend startup, mark their duration incomplete, and show retry/recovery status. Do not count server downtime as measured execution or silently restart an uncertain audit.
- Label current progress stages as estimated, or replace them with honest status-only progress until real worker milestones exist. Do not present elapsed time as a predicted finish time.

Likely files: entities, runner, job/admin APIs, schemas, frontend types/date utilities, customer/admin pages, job tests. Provide an explicit, idempotent schema migration for any changed existing columns; `create_all` does not alter them.

Acceptance: successful/failed attempts stop timing; retries retain prior execution totals; admin review does not extend AI time; restart does not leave a falsely running timer; UTC handling and old records remain correct.

## 3. Small branded project chat

First release: a collapsible **SBTech AI** chat on each project detail page, for BOQ questions, status explanations, and completed-report discussion. Use Vietnamese by default and persist one conversation per customer/project.

- Frontend calls only authenticated portal endpoints: `GET /api/jobs/{job_code}/chat` and `POST /api/jobs/{job_code}/chat/messages`. Reuse current cookie auth and ownership checks.
- Store conversation ownership, private backend thread ID, messages, and message status in SQLite. Backend chooses all provider settings; clients cannot supply thread IDs, commands, paths, prompts, or tool permissions.
- Use Codex App Server `thread/start`, `thread/resume`, and `turn/start`. Start with a simple send/wait response and loading state; add streaming only if observed latency makes it necessary. Persist pending/error states so refreshes and failures do not lose the conversation.
- Run chat separately from the audit runner with OS/container-enforced access to an authorized project snapshot only. No application database, other customer projects, internal notes, audit logs, or unrestricted credential/config directory access. Read-only sandbox alone must not be assumed to restrict every readable host path. Disable unnecessary commands, MCP integrations, network access, writes, and approval escalation.
- Build context server-side from permitted metadata and customer-visible results. Before completion, explain status without exposing draft findings. After completion, provide approved report evidence and cite page/sheet/row locations when available. If evidence extraction is unavailable, say so rather than claiming full document access.
- Treat user messages and document text as untrusted input. Keep output text escaped, enforce message/history limits, rate limits, timeouts, and one active turn per conversation; bound overall chat concurrency so audits retain capacity.
- Rename the customer usage route to a neutral AI-capacity route and return product-level availability only. Keep shared account plan/usage details admin-only. Return generic customer errors; raw provider logs and backend thread identifiers remain private.
- Brand the assistant as “SBTech AI — customized for BOQ review.” Your skill and instructions customize behavior; call it trained only if actual training has been performed. Do not program false provider or training claims. Branding and private infrastructure cannot guarantee customers will never infer the underlying provider.
- Verify the installed CLI/protocol and the deployment account's permitted customer-facing usage before launch. The reviewed technical documentation establishes integration mechanics, not permission to resell a shared subscription.

Likely additions: one chat service, one chat API module, conversation/message models, one React chat component; small changes to routing/config/types and the existing usage endpoint. No chatbot UI library, vector database, or new agent framework initially.

Acceptance: chat survives refresh; unrelated users cannot read or send messages; guessed thread IDs are rejected/ignored; requests to read another project or secrets are blocked by runtime permissions; chat cannot edit outputs or trigger audits; failure/quota/timeout handling preserves messages; customer responses do not expose raw provider metadata.

Reference: [official OpenAI documentation: Codex App Server](https://learn.chatgpt.com/docs/app-server).

## Delivery order and verification

1. Account session policy and concurrent-login checks.
2. Durable run timing and customer/admin displays.
3. Isolated chat backend, then branded project chat UI and customer metadata cleanup.
4. Run relevant backend tests, frontend typecheck/build, and a two-account integration check. Use a controlled live backend smoke test before launch; no customer files or production credentials in tests.

Use existing pytest tests for auth/timing/chat isolation. Planning estimates: account controls 0.5–1 day, timing 1–2 days, chat and isolation 2–4 days, integration checks 0.5–1 day. Total 4–8 developer days, depending on the installed Codex protocol and hosting isolation. These are implementation estimates, not project audit runtimes.

No reliable audit-duration forecast can be derived from source code alone. Collect actual completed run measurements first; any future ETA should use those measurements and visibly remain an estimate.
