# Recoverable audits — implementation plan

Date: 2026-10-12. Scope: plan only, based on repository inspection by the lead and three read-only reviewers. This document does not claim a live deployment or completed BOQ quality check.

## Goal and operating policy

An uploaded, fully stored job remains queued across an API or worker restart. A running audit that is interrupted keeps its partial artifacts and can start a fresh attempt. Only the current attempt may set the job result. Admin review still controls customer publication.

Use the existing single-VM, single-SQLite deployment. `Job.status == SUBMITTED` is the durable queue; no message broker or new queue table is required. Run exactly one audit worker process on the VM. Use a process lock on the shared data volume to reject a second worker. Keep the lock file at a stable path and never unlink or replace it while workers can run; `flock` locks the inode, not its name. SQLite `BEGIN IMMEDIATE` serializes a claim and creates the `AuditRun` row in the same transaction.

Recovery policy: after an unclean worker exit, mark the unfinished run `INTERRUPTED` with unknown duration. Requeue the job automatically once across its history; after a second interrupted attempt, set `FAILED` with an admin action to retry. A normal audit timeout or missing required output remains `FAILED` and requires a deliberate retry. Do not claim that a Codex generation resumes mid-run: recovery starts a new attempt.

## Current behavior that must change

- Upload and retry schedule `run_audit_job` through FastAPI `BackgroundTasks` (`backend/app/api/jobs.py`). The worker is tied to the API process.
- API startup marks every `SUBMITTED` and `PROCESSING` job `FAILED` (`backend/app/core/migrations.py`). Queued work therefore does not survive restart.
- Retry removes the existing output directory and clears output records (`backend/app/api/jobs.py`). Runner logs use fixed filenames, and output detection scans the whole job tree (`backend/app/services/audit_runner.py`). Previous artifacts can be lost or mistaken for a new result.
- An upload first commits a row with `input_file_path="pending"`, then commits the real path after saving files. A polling worker must never claim the pending row.
- Admin status edits, output edits, completion and job deletion can race a running audit (`backend/app/api/admin.py`). Customer deletion can do the same (`backend/app/api/jobs.py`).
- The deploy guard blocks both queued and running jobs, and Compose has no worker service (`scripts/deploy-gce.sh`, `docker-compose.yml`).

## Execution contract

1. **Queue readiness.** Upload commits the complete input metadata and real path before a worker may claim it. The worker selects the oldest `SUBMITTED` job with a non-pending, existing input. API startup marks orphaned pending uploads `FAILED` after the previous API process has stopped; it leaves ready `SUBMITTED` jobs alone. A submitted job whose saved input path is missing is marked `FAILED` with an actionable admin note, releasing customer active-job capacity.
2. **Claim.** The worker holds the exclusive process lock for its lifetime. In one short `BEGIN IMMEDIATE` transaction it checks the selected job, creates the next uniquely numbered `AuditRun`, changes the job to `PROCESSING`, and commits. No transaction is held while Codex runs. The lock is the singleton guarantee for this VM; `AuditRun.id` is the attempt identity used at finalization.
3. **Attempt files.** New attempts write under `jobs/<code>/attempts/<attempt>/` with their own `output/`, stdout and stderr logs. Stream subprocess logs to files as they arrive and parse only the bounded final message needed for the summary. Compute required output types solely from this attempt's files; existing `JobOutput` rows must not count. Keep legacy root `output/` and fixed logs untouched; do not invent missing history for old runs.
4. **Finish.** Close output/log files and check required outputs before the final database transaction. That transaction accepts results only if this `AuditRun.id` is the job's newest run, its status is `RUNNING`, and its job is still `PROCESSING`; it replaces current `JobOutput` references, summary, job status and run end together. A stale attempt cannot publish. A crash between file writes and this commit leaves inspectable partial files and an interrupted run, not a published report.
5. **Shutdown and recovery.** Start Codex in its own process group. On worker stop or timeout, terminate the group, wait briefly, then kill it if necessary. Configure the worker as the container's main process with `init: true` and a stop grace period. Prove in an integration check that killing the worker container leaves no Codex descendant; the lock alone does not prove this. Only after the old container/process group is gone and the process lock is acquired, reconcile unfinished runs once. Preserve their files, mark their duration unknown, and apply the one-restart policy only to a `PROCESSING` job with a matching `RUNNING` run. Leave other job states unchanged; a legacy `PROCESSING` job without a matching run becomes `FAILED` for admin review. The next attempt gets a new directory. Recovery must be idempotent.
6. **Retry and conflicting actions.** Retry changes `FAILED` to `SUBMITTED` under the same SQLite serialization used for the two-active-job quota; it clears only current result references and summary, not historical files. Reject manual assignment of `SUBMITTED` or `PROCESSING`. Serialize queued-job deletion/state changes against claim. Return 409 for deletion or status changes while `PROCESSING`, and for admin output edits while `SUBMITTED` or `PROCESSING`; allow existing manual output handling in other states. Admin completion cannot publish a queued or running job. Customer access to draft and historical artifacts remains closed.
7. **History access.** Add an admin-only attempt report/ledger list and download using the existing path containment and streaming helpers. Show run status and artifact links in admin job detail. Keep raw worker logs server-side for restricted troubleshooting; expose only redacted diagnostics through the API. List legacy root files separately when their run attribution is uncertain; missing legacy files return 404. The customer sees only the current published outputs.
8. **Worker health.** Refresh a worker heartbeat from the actual claim/poll loop, including its wait on Codex. Compose health checks require a fresh heartbeat from the current worker instance, not a stale file from a former process. This proves loop liveness, while the audit timeout bounds a hung Codex run; the process lock remains the ownership mechanism.

## Team execution and integration order

| Owner | First implementation slice | Files and contract to coordinate |
| --- | --- | --- |
| Lead (orchestrator) | Freeze the state/attempt contract above; review migrations, claim/finalization and API races; integrate and verify the end-to-end cases. | `backend/app/models/entities.py`, `backend/app/core/migrations.py`, shared `AuditRun`/job semantics. |
| Agent 1 — worker | Add dedicated worker loop, process lock, atomic claim, per-attempt execution/logging, signal cleanup and recovery. | `backend/app/services/audit_runner.py`, new worker entry point, `backend/app/core/migrations.py`. |
| Agent 2 — API and history | Remove audit background scheduling, make retry non-destructive, guard concurrent admin/customer actions, expose admin attempt artifacts and status. | `backend/app/api/jobs.py`, `backend/app/api/admin.py`, schemas, admin/customer UI and types. |
| Agent 3 — release and checks | Add the worker to Compose/deploy and backup procedure; update deployment guard/rollback checks, recovery tests and docs. | `docker-compose.yml`, `scripts/deploy-gce.sh`, `scripts/tests/test_deploy_gce.sh`, `backend/tests`, `README.md`, `docs/ci-cd-gce.md`. |

Before parallel edits, the lead and agents agree on the attempt path, claim/finalization function signatures, interruption limit, and admin API shape. Merge worker and API changes before enabling the worker service. Do not deploy an intermediate API that queues jobs without a working worker. Keep the current guard against restarting an active audit during initial rollout; permit queued jobs through deploy only after the new worker path is proven.

## Required verification

| Scenario | Pass condition |
| --- | --- |
| Upload versus claim | Pending upload is never claimed; a fully stored job is claimed once. |
| Missing queued input | A non-pending queued job with a missing input is marked `FAILED` with an admin note and releases active-job capacity. |
| Queued restart | A ready `SUBMITTED` job survives API/worker restart and runs without customer resubmission. |
| Duplicate worker | Two worker starts result in one lock holder and one `RUNNING` attempt. |
| Interrupted run | Kill the worker container during Codex; no child survives, old run and files remain, new attempt begins once, old files cannot satisfy required outputs. |
| Crash before DB finish | Complete-looking files from an uncommitted attempt stay private and are not presented as `REVIEW` or `COMPLETED`. |
| Repeated interruption | One automatic restart occurs; another interruption becomes `FAILED` and can be retried deliberately. |
| Retry and admin race | Two retries do not create two attempts; delete/status/output/complete operations cannot race a live run. |
| Access and legacy data | Customers cannot fetch draft/history files; admin can inspect allowed attempt files; existing root-level outputs remain readable. |
| Timing and rollout | Interrupted duration stays unknown; worker health detects a stopped loop; deploy rollback and paired DB/files backup/restore work with the worker present. |

Update `backend/tests/test_timing_chat.py` (which currently expects restart to fail jobs), runner/job API tests and their fixtures, and `scripts/tests/test_deploy_gce.sh`. Use a fake Codex process for kill/restart checks; do not spend provider quota in CI. Run the existing backend suite, frontend typecheck/build and deploy script checks after integration.

## Release gate and limits

First cutover needs a maintenance window: stop uploads/ingress, wait for existing audits to finish, stop the backend, and take a paired SQLite and jobs backup. The deploy script now requires the preserved prior Compose file and paired backup before first cutover; rollback restores those before starting the prior backend. The repo includes scripted guard and rollback checks, but the real VM backup, restore, and rollback are still external release gates. Deploy the integrated API and one worker together, prove both healthy, then reopen ingress. Verify one queued and one interrupted synthetic job on a non-production copy before production cutover. After cutover, permit queued jobs through deploy only after the new worker path and rollback are proven; keep the active-run drain guard initially. Backup instructions stop both backend and worker for a consistent snapshot; never restore data while either can hold the lock file.

This recovery work does not establish per-job filesystem isolation. The direct BOQ quality pilot remains gated by the separate isolation proof in `docs/quality-pilot-runbook.md`.
