<!-- /autoplan restore point: "/home/thangpham/.gstack/projects/minhthang294-BOQ-Audit/main-autoplan-restore-20261006-150334.md" -->
## Implementation plan
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

## Approved current scope — 2026-10-07

The admin approved quality validation first for an internal GCP pilot tested with one user. The original feature requirements below remain acceptance obligations for existing session, timing, and chat functionality; their implementation status must be checked against the current code. This quality-pilot scope is additional and is the next work. Source design: docs/designs/boq-audit-quality-pilot.md (APPROVED).

Architects without technical experience need to find errors in BOQs and verify the findings against drawings and estimates. The tool uses Codex to run the BOQ audit workflow and generate Vietnamese reports. A successful process exit and a set of output files do not establish that the findings are correct, complete, or understandable.

The immediate work is a small, repeatable quality pilot using the existing reports. Measure supported findings, missed errors, false alarms, and the architect's ability to verify a finding before expanding internal use.

- Primary user: an internal architect who can review quantities and drawings but should not need to operate Codex or a CLI. The admin controls accounts, deployment, and rollout.
- Reuse FastAPI, SQLite, Next.js, the current upload/download flow, and the BOQ audit skill. Initial evaluation uses files and a worksheet, with no new application screen or database service.
- Existing Google Cloud deployment remains the distribution channel. This design authorizes planning, not a production run, deployment change, or access to private project files.
- Reference findings require a competent human reviewer. Neither the source package, its confidentiality status, its discipline, nor the result of the first user's test is known yet.
- An evaluation run must use approved synthetic or anonymized inputs in a dedicated test environment. The test environment must not mount production data or production configuration. Codex runtime credentials, if needed, must be outside tool-readable paths, with an enforced per-job filesystem boundary verified before execution. A prompt instruction alone is insufficient. If the deployed runner cannot meet this condition, record the execution as blocked and evaluate already available, approved artifacts offline.

1. This is an internal tool, not a public SaaS launch.
2. Success means useful findings linked to verifiable source evidence, with a person making the final engineering judgment.
3. One user test establishes an early pilot, not accuracy or readiness for wider use.
4. Before wider rollout, inspect audit access to other jobs and credentials. The inspected Compose configuration mounts /data and Codex home into the backend that launches the audit subprocess; live permissions remain unverified.

### 1. Assemble and independently label a small corpus

Start with at least three approved document packages representative of the internal work: one with confirmed errors, one independently checked within a stated scope with no known errors, and one with missing or ambiguous evidence. This is an initial pilot, not a statistical certification. Keep reference labels outside the agent's inputs. Record source hashes, revision, discipline, checkable scope, and exclusions before the runs.

For each expected finding record a stable ID, item/object and source page or sheet/row, error class, checked calculation or geometric basis, severity with the reviewer’s reason, and a case-specific numerical tolerance where applicable. Units and source assumptions must be explicit. For ambiguous packages, record before execution which required checks cannot be resolved and which limitations the report must communicate. Seeded errors may extend coverage, but label them as synthetic and report them separately from real cases. Cases not independently checked are unavailable references, not clean examples. Resolve reference disagreements before scoring; log unresolved disagreements separately and exclude them from scored denominators with a reason.

Reuse the BOQ skill's issue and calculation ledgers, templates, and validate_run.py for structural validation; reuse its existing regression patterns where applicable to the discipline. Those patterns do not replace independent expert labels and are not all required for every type of building project.

### 2. Freeze inputs and preserve every attempt

Record repository/deployed revision as observed, CLI version, model identifier if reported, skill bundle digest, prompt hash, source hashes, configuration relevant to audit behavior, and run/attempt ID. Never put credentials or raw credential-bearing URLs in the run record. Unknown versions stay unknown.

Perform one baseline audit per package after the test-environment prerequisites pass. Save the complete output set, BOQ ledgers, manifest, structural validator result, status, and measured runtime. Timeouts, failed runs, missing ledgers, and partial results remain in the run inventory. They cannot be counted as successful audits or disappear through retries. Repeats and changed prompts are separate attempts, never replacements for the baseline. Diagnostic tuning uses separate development examples; freeze a later holdout before using it to assess improvements.

### 3. Adjudicate findings with a simple worksheet

Match each reported issue to at most one reference issue using the same physical object, error class, and compatible source/evidence. A human reviewer makes ambiguous matches. Duplicate or split reports share a root issue and do not inflate true positives. A numerical mismatch is supported only when the independently chosen method, units, assumptions, and tolerance support it.

Classify each unique reported issue as supported, false alarm, unresolved, or outside the declared evaluation scope. Classify every reference error as found or missed. A vague warning without evidence is not a confirmed detection. A valid novel error may become a true positive only after independent adjudication; version the reference change, explain it, and preserve the original comparison so the baseline is not silently improved.

Report raw counts per package, error class, and severity. Precision = supported unique findings / (supported unique findings + false-alarm unique findings) in scored scope. Unresolved and out-of-scope findings are excluded from that denominator and reported separately. Recall = matched reference errors / all adjudicated reference errors in scored scope. A zero denominator is N/A, not 100%. Report unresolved/out-of-scope counts and failed/partial runs next to these metrics. Failed/partial attempts can show provisional findings but cannot earn a rollout pass; assess all expected issues and unavailable coverage explicitly. Also adjudicate the report narrative against the predeclared required checks and limitations: an unsupported all-clear or completed-verification claim, or an omitted required limitation, fails package readiness even if there are no issue rows. Never pool seed-only success into claims about real packages.

### 4. Observe one architect using the existing reports

Ask the pilot architect to verify five findings, or all findings if fewer, spanning supported findings and available uncertainty/false-alarm examples. Do not reveal the reference answer first. Record whether they locate the source, understand the issue and recommended next action, and reach the adjudicated interpretation, plus time and admin assistance. With a tiny sample, report the individual outcomes and denominator; do not claim general usability or speed gains.

Use Vietnamese reports and existing Excel/PDF views. Set this usability rule before the observation: successful independent verification means the architect locates the cited source and explains the issue, uncertainty, and next action consistently with the adjudicated interpretation, without admin help. Every observed task must meet it before this pilot can justify wider use; an empty exercise is inconclusive. Any failure keeps the pilot limited, becomes a concrete follow-up task, and must be reassessed after repair. A new review screen is deferred until the exercise demonstrates a need.

## Success Criteria and Decision

Proposed pilot bar for later plan review: every adjudicated critical reference error is detected with supporting evidence; no unsupported claim is presented as a critical confirmed error; every scored confirmed finding has inspectable source evidence; ambiguous evidence remains explicitly unresolved. Every baseline run and exclusion is reported. No failed, structurally blocked, or partial package receives a readiness pass. These criteria apply only to the evaluated scope, not to all BOQs.

Also require a nonempty human usability exercise that meets the independent-verification rule above, plus an enforced isolation check before wider use. Any unsupported all-clear, omitted required limitation, or unresolved usability failure keeps this pilot from earning readiness for wider use. The admin reviews the report and chooses continue limited pilot, repair and rerun, or widen rollout. No automatic deployment follows a quality score. Numerical performance targets beyond this small pilot require a representative labeled set and must be set before the next assessment.

## Existing Flow and Implementation Boundary

backend/app/services/audit_runner.py already invokes Codex, generates output records, extracts counters from final text, and chooses a status. Its current zero-exit completion decision checks output categories, not independently adjudicated accuracy; the pilot must not read COMPLETED as a quality certification. docker-compose.yml supplies the backend data and Codex-home mounts. Existing reports, issue/calculation ledgers, validator, and backend tests are reusable; mocked worker tests are workflow checks, not engineering accuracy evidence.

Keep application changes out of this initial design. During autoplan, reconcile stale feature assumptions, map gaps in the existing audit path, and plan minimal fixes if evidence warrants them. Any runtime redesign is a separate scoped task. This design does not discard already implemented features.

## Open Questions and Dependencies

- Which approved package and independent reviewer can the admin provide first? None was supplied here.
- What did the first architect find difficult or incorrect? No observation record exists in this session.
- What are the deployed revision, model/skill configuration, and actual isolation controls? Repository inspection is not a live GCP verification.
- Which disciplines, measurement rules, and tolerances should the pilot cover? The reviewer must state them case by case.

## The Assignment

Choose one representative, approved BOQ plus source drawings, and ask a qualified reviewer to list known errors and evidence before seeing Codex's output. Then observe the pilot architect verifying five findings from the existing report. Return the labeled reference and an observation record; do not send production credentials.


<!-- autoplan-accepted:ceo -->
- Preserve every original session, timing and project-chat requirement and acceptance criterion; annotate October 5 findings as historical and verify current implementation before proposing a rebuild. No original requirement is waived by this pilot.
- Implement the approved quality-pilot procedure using a file worksheet and runbook, reusing existing upload/download, Vietnamese reports, BOQ issue/calculation ledgers, validate_run.py and applicable regression cases. No new application UI, endpoint, service, queue or database is required for this pilot.
- The worksheet shall include a Vietnamese cover with package/revision/scope, attempt and evidence status, required limitations and human-review instruction; source-location checklist, approved precision/recall formulas with N/A for zero denominators, failed/partial attempt inventory, and a no-coaching observation script. Verify each field against the approved design and demonstrate on synthetic examples without treating those examples as measured pilot results.
- Preserve the approved corpus, independent hidden labels, numerical tolerances, deduplication, novel-error adjudication/version history, separate synthetic/real results, baseline attempt preservation, holdout separation, narrative/unsupported-all-clear checks and nonempty all-observed-tasks-pass usability rule exactly. Failed, partial, structurally blocked, unsupported-all-clear or omitted-limitation packages cannot earn readiness. All critical reference errors require evidenced detection and no false critical confirmed claim is allowed.
- Require enforced per-job filesystem and credential isolation plus approved synthetic/anonymized inputs before any live evaluation. If proof is unavailable, record live execution blocked and assess approved existing artifacts offline with limitations; repository Compose is not proof of deployed safety. The admin owns package/reviewer access and any later rollout decision; this review authorizes no production action.
- Plan verification of existing auth/timing/chat acceptance criteria and reconcile README's stale manual-audit description when pilot documentation is implemented; retain interrupted-run, concurrent-session, ownership, chat isolation, generic customer errors and timing semantics. Do not claim these checks were executed during planning.
- Record represented and unrepresented disciplines, error classes and severities; a zero critical-reference denominator is critical detection unassessed, never passed. The admin must restrict any wider-use decision to demonstrated coverage; synthetic critical cases demonstrate synthetic coverage only. Verify a zero-critical example cannot yield a critical-detection pass.
- Treat the first approved package as a rehearsal/dependency check; the complete three-package corpus is required before this pilot readiness decision. The admin owns source approval, independent reviewer availability and architect scheduling. Missing inputs block measured results; documentation and synthetic demonstrations may proceed without claiming pilot results.
- Record descriptive reviewer-preparation, adjudication, admin-assistance and architect-verification time, plus the actionable correction, clarification or justified unresolved decision for each verified finding. Do not infer general savings or introduce another numerical rollout threshold.
<!-- /autoplan-accepted:ceo -->

<!-- autoplan-accepted:design -->
- Order the Vietnamese worksheet cover as package/revision/scope, prominent human-review instruction, separately labeled execution/evidence/pilot-assessment states, required limitations and assessed/unassessed coverage, then findings/source/actions. Include “Chạy hoàn tất — chưa xác nhận chất lượng” and zero-critical “Chưa đánh giá”; never convey these distinctions through color alone.
- Each finding row shall include stable ID, exact source filename/revision, PDF page and printed drawing/sheet identifier when different, Excel worksheet and cell/row, object/location, calculation/assumption and corrective/clarifying/justifiably unresolved next action. Explicitly mark unavailable locators. Verify one usable and one incomplete synthetic citation.
- Demonstrate visible worksheet outcomes for awaiting inputs, zero findings, no reference denominator, unresolved-only findings, failed/partial/structurally blocked attempts, offline artifacts with missing provenance, zero critical coverage and supported scoped findings. Each shows denominator/N/A, missing evidence, next action and owner; zero findings still requires narrative/required-check adjudication.
- Keep participant task material separate from the observer answer key and recording sheet. Give the architect the ordinary Vietnamese report and neutral finding ID/source task only; record source location, interpretation, uncertainty, next action, time and assistance before revealing adjudication. Preserve five-or-all fewer selection, nonempty exercise and all-observed-tasks-pass rule.
- During retained UI verification inventory existing Vietnamese labels/actions for session replacement, total turnaround and AI execution/history/interruption, saved pending/error chat, refresh recovery, quota/capacity and concurrent-turn conflicts. Preserve primary project/report/source navigation and secondary collapsible chat; propose changes only for concrete acceptance gaps.
- Verify retained UI at 375/768/1440 CSS px, 200% zoom, keyboard-only and screen-reader use, including embedded report/download alternatives. Require readable 16px-equivalent body instructions/findings, 4.5:1 body contrast, 44px important controls, visible labels/focus and word-based status/limitations. Use existing components/palette/font, no redesign; failed criteria remain failed until the smallest separately scoped repair is verified. No rendered accessibility or quality pass is claimed by source review.
<!-- /autoplan-accepted:design -->

<!-- autoplan-accepted:dx -->
- Add an exact three-step documentation rehearsal from README to the planned runbook and self-contained SYNTHETIC/not-pilot-result worksheet/source example. Prerequisites are a checked-out repository and ordinary viewers. Start when the maintainer opens README; finish only when they correctly explain a supported source match and an unresolved example with limitations. Target <=5 minutes includes reading/navigation; current time unknown. Keep full installation, real audit and engineering adjudication clocks separate. Record elapsed time, completion, assistance and confusion in the existing worksheet using a fresh reader; no telemetry, automatic release gate or recurring process is selected.
- Document the existing validator with resolved installed script/Python prerequisites, approved copied run-directory path and --out coverage_result.json, expected output/report and exit meaning. It writes only to the approved copy: exit 2 means BLOCKED; exit 0 can still mean PARTIAL_WITH_OPEN_ITEMS_REVIEW_REQUIRED. READY_FOR_ENGINEER_REVIEW is METADATA_ONLY, requires human signoff and never proves engineering accuracy or publication permission. Preserve original and failed artifacts; no production execution authorized.
- Add a compact safe troubleshooting table with actual problem, likely cause, corrective action, responsible role and runbook section link for replaced session, concurrent/saved pending chat, quota/capacity, stale project, generic gateway failure, interrupted audit, missing CLI/skill/evidence and structurally blocked validation. Customer errors remain generic; internal paths/logs/diagnostics are admin-only and redacted. Inventory actual UI behavior and scope the smallest separately reviewed repair for any observed acceptance gap.
- Explain existing artifact/package/revision/scope/tolerance/runtime-version and configuration choices, owners, permitted alternatives and their effects; do not invent a new configuration system, default engineering tolerances, configurable readiness gates or new API. Preserve corpus/adjudication/attempt/runtime/skill/model identities and versions, changed-scope comparability limits and existing recovery documentation. Runbook separates tutorial, field/formula reference, prerequisites, structural checks, troubleshooting and limitations, directly linked from README; verify links/findability/examples during the planned rehearsal.
<!-- /autoplan-accepted:dx -->

<!-- autoplan-accepted:eng -->
- Before any evaluation retry/rerun that reuses a job workspace, require a complete uniquely named immutable approved attempt snapshot with owner, provenance, file hashes, reports/ledgers/manifest, status, validator result and permitted redacted logs; or use a separate job per attempt. Existing retry deletes outputs and fixed worker logs are overwritten, so timing history alone is insufficient. Incomplete/missing snapshots remain unavailable evidence, never silently reconstructed. Demonstrate a copied synthetic baseline's hashes and failed/partial classification survive separately simulated replacement; preserve every baseline/comparison and original artifacts.
- Require a trusted pinned validator script, actual Python version and fresh result location for each approved copied attempt. Unexpected exit (outside normal 0/2), absent Python/script, unreadable files, timeout/crash/write failure or missing/malformed fresh JSON is structural validation unavailable/blocked; retain redacted diagnostics privately and never reuse stale results or manufacture a validator verdict. Document and demonstrate malformed nested-ledger and stale pre-existing result cases. A valid normal metadata result still needs all existing human evidence/narrative/readiness gates.
- Before copied-artifact validation, create a fresh approved destination containing ordinary files only; reject symlinks, special files and files whose identity/containment cannot be established, verify all resolved read/write paths stay within the copy, and ensure the output destination is not linked or stale. Retain originals separately and never execute commands supplied by artifacts/manifests. Demonstrate rejection of a synthetic outside-pointing link before invoking the validator. These are runbook copy prerequisites, not a new service or application rewrite.
- Carry forward the complete planning test map and saved engineering test plan: corpus/reference/scope/hidden-label checks; runtime-proof-or-offline choice; attempt snapshot/copy/validator failure demonstrations; independent matching/dedup/tolerance/version/count/N/A/zero-critical checks; narrative/required-limitations and synthetic-real separation; all visible citation/state examples; nonempty blinded five-or-all-fewer independent observation and scoped admin decision; retained auth/timing/chat regression, rendered accessibility and deployed boundary/protocol/cleanup checks. Reuse existing pytest and manual records, retire no tests, add no framework. All tests, model evals and live checks remain planned rather than executed; missing proof keeps the relevant gate unpassed.
<!-- /autoplan-accepted:eng -->
## Review record

### CEO methodology and context
Installed methodology read through EOF: 1–600 (240–405 recovered), 601–900, 901–1200, 1201–1500, 1501–1800 (1760–1800 recovered), 1801–2100, 2101–2457 (2345–2457 recovered). Full methodology includes the required eleven-section driver. The preamble and autoplan skip-listed outputs are loaded only. This is a planning run, not an executed evaluation.

System audit: HEAD e50f00c on main; base main. Existing code includes customer active-session upsert, account/IP rate limits, additive AuditRun tables/recovery and separate chat gateway. No stash, repository AGENTS.md/TODOS.md/DESIGN.md or architecture document was found. The injected daily-ledger instructions apply. No TODO/FIXME/HACK/XXX matches in backend/frontend; recent hotspots include README (10 commits), jobs API (9), customer job page (8), tests/jobs and schemas (7). The October 5 findings above are historical observations, not a description of current HEAD; verify their acceptance criteria against current code rather than rebuilding these features.

Taste references: reuse auth.py's database-enforced customer-session pointer and failed-login ordering; migrations.py's explicit interrupted-run recovery; JobTiming.tsx's separate turnaround/execution labels and unavailable history. Avoid audit_runner.py's category-only completion as an accuracy claim and raw text counter extraction as a scoring source. Prior learning applied: audit-completed-is-not-quality-verified (observed, confidence 9, 2026-10-06).

Landscape synthesis: Layer 1 is independent takeoff, spreadsheet reconciliation and drawing revision control. Layer 2 search found vendor workflows retaining evidence and reconciled quantities, e.g. [BOQBook](https://boqbook.com/) and [AEC Stack](https://www.aecstack.com/guides/reconcile-quantities-scope-and-quote/); these establish advertised approaches, not measured accuracy. Layer 3 inference: this internal pilot needs evidence that a human can verify a finding before another workbench or a benchmark dashboard. The approved office-hours research supplies additional context. No competitor accuracy claim or Vietnamese measurement standard is inferred.

### CEO Step 0 — scope challenge
0A: The real outcome is correctly finding and explaining scoped BOQ errors to an architect. Uploaded files, an exit code and a report count are proxies. Doing nothing preserves an unmeasured one-user pilot and cannot support expansion. Reasonable premises are accepted under autoplan P6; no user-direction challenge is presently supported.

0B reuse map: authenticated upload/download + SQLite job/run identity; existing Vietnamese Excel/PDF reports; boq-audit issue/calculation templates, validate_run.py and discipline-applicable regressions; human worksheet instead of a scoring service. Existing session/timing/chat requirements remain verification obligations. No new endpoint, model provider, queue, data store or UI is selected.

0C twelve-month ideal:
CURRENT: deployed internal tool + one user's test + unmeasured findings
   -> PLAN: frozen approved corpus + independent labels + evidence/limitations + observed verification
   -> IDEAL: repeatable discipline-specific evidence of quality + safe runtime + admin-controlled expansion
A 10x outcome is faster trusted engineering review across recurring packages, not more generated findings. This pilot measures the missing foundations; it does not promise the ideal or tenfold speed.

0D depth: implementation-ready for the pilot files and manual procedure; verification-only for existing production contracts and runtime isolation. Actual cloud/runtime changes require a separate scoped task if verification fails. User already chose A (quality first); do not reopen A/B/C. All choices below use autoplan's six principles and do not authorize implementation or production execution.

### Decision audit trail (append only)
| ID / owner | Contract / evidence | Current | Proposed / alternatives | Status | Exact approval scope and classification |
|---|---|---|---|---|---|
| U1 / admin | user: internal use, admin, GCP deployed, one user | early pilot | use internal pilot constraints | Approved by user | Product and deployment context only |
| U2 / admin | user chose A and approved design on Oct 7 | quality unknown | quality-first / runtime-first / new workbench | Approved by user A | Approved design's complete corpus, scoring, observation and gating procedure; no production change |
| U3 / CEO | original feature plan + current HEAD | features exist; tests not run | retain acceptance / rebuild | Auto: retain, mechanical P4 | Preserve all original requirements; map implemented status and verification gaps |
| C1 / CEO | pilot needs exact known inputs | no worksheet | add worksheet + procedure / service | Auto: worksheet, mechanical P5 | Two planned documentation deliverables with stable issue/attempt IDs, formulas, exclusions and readiness checklists; no app code |
| C2 / CEO | risk of draft being mistaken as approved | report exists, wording unverified | add a one-page Vietnamese pilot cover worksheet / new UI | Auto: cover worksheet, taste P1/P5 | Cover includes evaluated revision/scope, status, limitations and human review instruction; does not relabel or modify production screens |
| C3 / CEO | code mounts data/Codex home; live boundary unknown | live execution prerequisites unverified | verify then run, else offline / assume cwd safe | Auto: verify/else offline, mechanical P1 | No live audit before enforced per-job/credential boundary proof; preserve blocked execution and offline limitations |
| C4 / CEO | existing auth/timing/chat contracts retained | tests available, unexecuted | plan contract checks / omit old features | Auto: plan checks, mechanical P1 | Existing acceptance criteria remain exact; no claim of passing tests or live verification |
| C5 / CEO | weak README opening says manual | docs conflict | correct when pilot runbook lands / new docs system | Auto: correction, mechanical P3 | Planned README paragraph distinguishing automatic jobs, human approval and evaluated quality |

0E: SELECTIVE EXPANSION is the autoplan mode override, not a fresh user remedy approval; mode handoff persisted. Small documentation additions meet <5 files/no infrastructure/<1 day assisted effort; qualified human labeling has independent elapsed-time cost.

0F/0G proposal record (five adjacent delight opportunities):
| Proposal | User experience | Effort human / assisted | Risk | Disposition |
|---|---|---|---|---|
| P1: Vietnamese cover in worksheet | Know scope, revision, limitations and who checks results | S / S | Low; could overstate if status omitted | ACCEPTED C2; all status/limitation fields mandatory |
| P2: source-location checklist | Open the cited sheet/row without CLI work | S / S | Low | ACCEPTED C1; reuse report references |
| P3: formula/count glossary | Distinguish false alarms, misses and N/A | S / S | Low | ACCEPTED C1; formulas from approved design unchanged |
| P4: failed/partial attempt inventory | See exactly what could not be checked | S / S | Low | ACCEPTED C1/C3; no retry erasure |
| P5: observation script | Verify findings without admin coaching | S / S | Low | ACCEPTED C1; retain nonempty all-tasks-pass rule |
| P6: in-app evidence review screen | Potentially simplify repeated verification | M / S | Medium; need user observations first | DEFERRED to TODOS.md; does not block file pilot |
| P7: scoring service / new queue | Could automate large future experiments | L / M | High scope, no measured need | SKIPPED P4/P5 |

Scope sanity: expected pilot documentation touches <=4 files (worksheet, runbook, README, existing plan); no new classes or infrastructure. Runtime isolation remediation is separate work if proof fails, never waived. UI features already in source remain preserved verification work. No automatic rollout, billing features, new provider, training claim or claimed certification.

<!-- autoplan-accepted:ceo -->
- Preserve every original session, timing and project-chat requirement and acceptance criterion; annotate October 5 findings as historical and verify current implementation before proposing a rebuild. No original requirement is waived by this pilot.
- Implement the approved quality-pilot procedure using a file worksheet and runbook, reusing existing upload/download, Vietnamese reports, BOQ issue/calculation ledgers, validate_run.py and applicable regression cases. No new application UI, endpoint, service, queue or database is required for this pilot.
- The worksheet shall include a Vietnamese cover with package/revision/scope, attempt and evidence status, required limitations and human-review instruction; source-location checklist, approved precision/recall formulas with N/A for zero denominators, failed/partial attempt inventory, and a no-coaching observation script. Verify each field against the approved design and demonstrate on synthetic examples without treating those examples as measured pilot results.
- Preserve the approved corpus, independent hidden labels, numerical tolerances, deduplication, novel-error adjudication/version history, separate synthetic/real results, baseline attempt preservation, holdout separation, narrative/unsupported-all-clear checks and nonempty all-observed-tasks-pass usability rule exactly. Failed, partial, structurally blocked, unsupported-all-clear or omitted-limitation packages cannot earn readiness. All critical reference errors require evidenced detection and no false critical confirmed claim is allowed.
- Require enforced per-job filesystem and credential isolation plus approved synthetic/anonymized inputs before any live evaluation. If proof is unavailable, record live execution blocked and assess approved existing artifacts offline with limitations; repository Compose is not proof of deployed safety. The admin owns package/reviewer access and any later rollout decision; this review authorizes no production action.
- Plan verification of existing auth/timing/chat acceptance criteria and reconcile README's stale manual-audit description when pilot documentation is implemented; retain interrupted-run, concurrent-session, ownership, chat isolation, generic customer errors and timing semantics. Do not claim these checks were executed during planning.
- Record represented and unrepresented disciplines, error classes and severities; a zero critical-reference denominator is critical detection unassessed, never passed. The admin must restrict any wider-use decision to demonstrated coverage; synthetic critical cases demonstrate synthetic coverage only. Verify a zero-critical example cannot yield a critical-detection pass.
- Treat the first approved package as a rehearsal/dependency check; the complete three-package corpus is required before this pilot readiness decision. The admin owns source approval, independent reviewer availability and architect scheduling. Missing inputs block measured results; documentation and synthetic demonstrations may proceed without claiming pilot results.
- Record descriptive reviewer-preparation, adjudication, admin-assistance and architect-verification time, plus the actionable correction, clarification or justified unresolved decision for each verified finding. Do not infer general savings or introduce another numerical rollout threshold.
<!-- /autoplan-accepted:ceo -->

### CEO 0H spec review and document approval
Checkpoint: /home/thangpham/.gstack/projects/minhthang294-BOQ-Audit/autoplan-ceo-iq1F7Q/ceo-implementation.md (immutable). Current spec input: autoplan-ceo-tkVUa0/ceo-implementation.md, full read 1–162 and scope summary full read reconciled: P1–P5 accepted (5), P6 deferred (1), P7 skipped (1); existing acceptance criteria preserved. Native independent ceo_spec_1 read both full inputs and returned 9/10 overall PASS, all dimensions PASS, zero issues. Actual metrics: iterations 1, found 0, fixed 0, remaining 0. Not a cross-model review. Document approval auto-decided A under autoplan P6 because both reflect exact recorded choices; this approves the scope documents only. No implementation/evaluation execution authorized.

### CEO 0I temporal interrogation
- Foundation (~1h human / ~20min assisted documentation): admin approves corpus use and appoints independent reviewer; freeze scope/revisions and keep labels outside agent input. These human dependencies have unknown elapsed time and are not accelerated by writing code.
- Core (~2–3h human / ~45min assisted worksheet setup; expert labeling time unknown): establish case units/tolerances, expected limitations, root issue matching and count formulas. Verify no accidental label leakage or synthetic/real pooling.
- Integration (~1–2h human / ~30min assisted analysis): admin verifies deployed revision and enforced audit boundary, then live synthetic/anonymized evaluation only if permitted; otherwise offline approved artifacts with live run blocked. Existing runtime isolation remediation requires separate scope if failed.
- Review/polish (~1–2h human / ~30min assisted report setup, plus actual audit runtime and architect session): preserve all attempts, run structural validation, adjudicate findings/narrative, observe five or all fewer findings, admin records limited/repair/widen decision with evidence. No ETA forecast for audits.
Feasibility blockers: approved packages/reviewer and live isolation evidence absent. Pending design choices: none; case measurement rules are reviewer-owned inputs, not arbitrary defaults.

### CEO native voice and consensus
Native autoplan_ceo_voice completed INPUT ceo 124551da8408ff8357245971443db16e2c19d0567f7f63cc1302e6e182bf2e05. Three findings: high zero-critical denominator; medium rehearsal/full-corpus ambiguity; medium missing practical-effort description. All accepted as clarifications of the approved pilot, not a direction change. Claude Code preflight rechecked enabled/not_installed; no external review completed. Outside unavailable; native-only coverage, no cross-model agreement.
| Dimension | Native | Claude Code | Consensus |
|---|---|---|---|
| Premises valid | Explicit unknowns retained | Unavailable | N/A |
| Right problem | Quality/evidence verification appropriate | Unavailable | N/A |
| Scope calibrated | Three clarifications needed | Unavailable | N/A |
| Alternatives | File pilot/offline fallback appropriate | Unavailable | N/A |
| Competitive risk | Existing manual review is relevant alternative | Unavailable | N/A |
| Six-month trajectory | Avoid favorable-corpus generalization | Unavailable | N/A |

| ID / owner | Contract / evidence | Current | Proposed / alternatives | Status | Exact approval scope and classification |
|---|---|---|---|---|---|
| C6 / CEO | native high: critical denominator may be zero | critical gate vacuous | disclose coverage/limit expansion / pretend covered | Auto: disclose, mechanical P1 | Coverage inventory; critical=N/A when absent; synthetic separate; no general rollout claim |
| C7 / CEO | native medium: assignment one vs corpus three | sequencing implicit | rehearsal then full corpus / reduce corpus | Auto: explicit sequence, mechanical P5 | Admin dependency ownership; three-package gate unchanged |
| C8 / CEO | native medium: effort/value unknown | verification time only | record all descriptive effort/action / claim savings | Auto: fields, mechanical P1 | No new threshold; no general speed claim |

### Current scope
U1/U2 user-approved internal quality-first pilot governs; U3/C1–C8 provisionally accepted under autoplan, C2 taste surfaced at final gate. Five worksheet improvements accepted, P6 deferred to TODOS.md, P7 skipped. Existing features remain requirements, runtime redesign remains separately scoped if needed; all execution prerequisites are unverified.

### Section 1: Architecture Review
The pilot adds two documentation files around existing outputs; the source of truth remains independently adjudicated labels. Labels must never enter the agent context, and scoring must use ledgers and source evidence rather than free-text counters. Existing auth/timing/chat implementation is reused; source alone does not prove deployed permissions or all acceptance checks. Coupling stays one-way from immutable approved artifact copies into a human worksheet, with no write-back to production.
```
architect -> portal cookie auth -> owned job -> upload/download
                                   |-> SQLite Job/AuditRun
                                   |-> audit subprocess -> BOQ skill -> reports/ledgers
                                   |-> separate chat gateway (customer context)
admin -> approved input copies -> verified test boundary -> baseline attempt archive
independent reviewer -> hidden reference ----------------------> adjudication worksheet
baseline reports + ledgers -----------------------------------> worksheet -> admin decision
```
Happy: approved copies and labels frozen -> audit/artifacts -> structural check -> human score. Nil: no approval/reference -> blocked, no run/score. Empty: blank package -> invalid, zero findings do not imply clean. Error: timeout/missing files -> preserved failed/partial attempt, no readiness. No app dependency changed; at 10x human adjudication becomes limiting, at 100x archive volume and single worker become limiting, but no speculative infrastructure is justified. Rollback is stop the pilot and retain all immutable artifacts; production is untouched.

### Section 2: Error & Rescue Map
Current `_run_audit_job` catches broad exceptions and marks failure; `_record_outputs` infers output types by extension and scans all workspace; `_apply_codex_summary` parses counts from text. These do not establish structural validity or factual correctness. Pilot records failure distinctly and uses validate_run.py plus independent adjudication as approved safeguards. No new Python method is proposed; human procedure failure conditions have no exception class, explicitly N/A.
| Codepath/procedure | What can fail / exception | Rescue and visibility | Owner/proof |
|---|---|---|---|
| source/label intake | Missing approval/reference; N/A | Block scoring/run, show missing input | admin/reviewer checklist |
| run_audit_job/_run_audit_job | TimeoutExpired, OSError, subprocess exit; broad existing catch | Existing FAILED/REVIEW; preserve attempt and raw approved artifacts separately | admin, failure case later |
| _record_outputs | missing/stale/misclassified output; OSError | Pilot manifest + structural validator + human scope check; files alone never pass | reviewer; missing output demonstration |
| _apply_codex_summary | missing/misleading count; regex fallback | Never use portal counters as gold labels; classify worksheet root findings | reviewer; disagreeing-counter example |
| validate_run.py | missing ledgers/inconsistent metadata | BLOCKED/partial recorded, no readiness | admin; validator result retained |
| worksheet adjudication | unit/tolerance disagreement, duplicate, false all-clear | unresolved/exclusion reason; preserve original reference; narrative gate | independent reviewer |
| architect observation | empty, coached, misunderstood finding | inconclusive/fail; keep limited and repair/reassess | observer |
No automatic retry masks the baseline. UI messages are existing portal failure or Vietnamese worksheet limitation; privileged stderr remains admin-only.

### Section 3: Security & Threat Model
No endpoint or executable code is added by this pilot; document confidentiality and runtime access dominate the threat model. Compose currently gives audit subprocess access to shared data and Codex home; cwd/prompt does not establish the required boundary. This is a high-impact unknown at the live deployment and a live-execution blocker, not proof of compromise.
| Threat | Likelihood / impact | Mitigation / remaining verification |
|---|---|---|
| Agent reads other jobs/config/credentials | Unknown / High | Enforced per-job boundary and credential-path denial required; admin must inspect actual deployment before live test |
| Prompt injection in uploaded documents | Medium / High | Treat documents untrusted; isolated test environment; no new tools/network/escalation permission assumed |
| Reference leakage creates false accuracy | Medium / High | Keep labels outside uploads, manifests with agent-access boundary checked |
| Worksheet/source confidentiality leak | Medium / High | Approved anonymized/synthetic copies; admin-controlled storage, no public fixture publication |
| Guessed job/thread access | Unknown / High | Reuse owned_job/session_user and server-only thread settings; original two-account proof retained |
| Provider subscription permissions unknown | Unknown / Medium | Retained original deployment-account permission check; no licensing conclusion made |
No new secrets/dependencies. Case IDs replace identifying titles where possible; tokens/raw credential URLs never enter run records. Security unknowns remain visible and cannot receive a readiness pass.

### Section 4: Data Flow & Interaction Edge Cases
```
INPUT approved copies -> VALIDATE approval/hash/scope -> TRANSFORM isolated audit
  -> PERSIST immutable attempt files -> OUTPUT ledgers/reports -> human adjudication
nil/empty/wrong type -> reject or blocked intake, never clean
wrong units/revision -> mark reference unavailable or resolve before scoring
exception/timeout/OOM -> failed/partial attempt archived, no readiness
conflict/duplicate -> distinct attempt IDs, shared physical-root issue ID
stale/partial/encoding -> hash mismatch/structural failure, preserve source + limitation
```
No new async shared mutation is introduced: the runbook freezes input before a run and never edits baseline files; later references are new versions. Existing concurrent customer login is serialized by CustomerActiveSession.user_id upsert; verify A/B completion orders leave only latest committed pointer usable. Existing chat captures context_version before await, then rechecks session and version before storing reply; both reply-before-job-change and job-change-before-reply orders require the original stale-context proof. Backend global chat lock applies only to one process, so multi-instance safety is unknown and cannot be assumed.
| Interaction | Edge case | Required disposition |
|---|---|---|
| upload/run | empty/unapproved/revised source | reject/block; new revision is distinct input |
| run/retry | double request, timeout, restart | existing worker claim/timing checks; new attempt never erase baseline |
| score | no rows, duplicate/split issues, no critical labels | narrative still checked; dedup; critical unassessed if zero |
| observe | fewer than five, zero, navigate away/help | all fewer; zero inconclusive; record assistance/failure |
| chat | refresh, stale context, replaced session | retain pending/error history; reject stale/revoked completion |
These are planned checks, not executed evidence; failure inventory and contracts make every unsupported result visible.

### Section 5: Code Quality Review
Reusing a worksheet, validator and existing report formats avoids a scoring framework. The current audit runner combines invocation, discovery, counters and status with broad catches and dense statements; that is a maintenance concern, but rewriting it would displace the quality-first pilot. No new method exceeds branching limits because no executable feature is being introduced; improve it only under a separately scoped evidenced defect. Naming in documentation shall distinguish process-completed, structurally-valid, human-supported and readiness-within-scope; no shared helper extraction is needed.

### Section 6: Test Review
```
corpus -> approval/hidden-label/hash check
run -> live boundary proof OR offline-only status
attempt -> timeout/partial/missing ledger preserved
findings -> duplicate+false alarm+miss+unresolved+units/tolerance examples
narrative -> unsupported all-clear/omitted limitation example
metrics -> zero denominators + zero critical coverage + real/synthetic separation
observation -> 5 or all fewer; zero/help/failure cannot pass
readiness -> every independent gate + admin decision with coverage limits
existing app -> auth concurrency/download revocation; timing retry/restart; chat owner/stale/isolation
```
Happy-path synthetic worksheet demonstration must count expected unique matches and preserve evidence. Hostile cases are duplicate split rows, a plausible unsupported critical error, stale files and an empty issue ledger paired with an all-clear. Chaos case is interrupted baseline/retry with preserved original failures; no test shall convert it to a successful initial audit. Unit tests can prove workflow logic, integration checks prove actual caller boundaries, and human adjudication is necessary for domain correctness. External-model runs, expert availability and timing observations are nondeterministic; freeze inputs/config and report all attempts rather than repeatedly selecting a favorable result. No prompt/model/skill implementation change is approved; any later change requires frozen baseline and a separate holdout. None of these checks was run in this planning task.

### Section 7: Performance Review
No new queries, pools, caches or frequently called codepaths are added. Existing Codex invocation is the likely longest path, followed by document/validator processing and human source reconciliation; no measured p99 or maximum memory evidence exists, so these stay unknown. Capture actual per-attempt execution and human effort; no ETA estimate, caching of dynamic findings, or stress infrastructure is justified by a three-package pilot. Large files and subprocess capture_output may pressure memory; admin records payload size/time and failed/OOM attempts under existing resource limits, not a made-up capacity guarantee.

### Section 8: Observability & Debuggability Review
Attempt IDs, package hashes, versions and complete artifact inventories provide day-one reconstruction without a new dashboard. A discrepancy is traceable from worksheet root issue to reference version, source page/row, report, attempt and validator result. Existing privileged worker logs can aid admin diagnosis but must not become customer evidence or leak credentials. Missing labels, timeout, structurally blocked, false all-clear, unassessed critical coverage and failed observation each have an explicit blocked/limited disposition. Human preparation/adjudication/assistance/verification times remain descriptive and actionable outcome is recorded per finding.

### Section 9: Deployment & Rollout Review
The planned pilot documentation has no schema migration, feature flag or mixed-version deployment window. GCP is already deployed according to user; no live verification has been performed. The live-evaluation sequence is approve inputs -> inspect actual boundary and revision -> freeze copies/config -> baseline attempts -> structural/human checks -> architect observation -> admin scope-limited decision. If any live prerequisite fails, stop execution and use approved artifacts offline without claiming live readiness.
```
admin approvals -> boundary proof? --no--> LIVE BLOCKED -> approved offline artifacts only
                         |yes
                    isolated baseline -> gates -> admin limited/repair/widen (coverage stated)
rollback: any unsafe/incorrect result -> STOP pilot -> preserve evidence -> repair scoped separately
```
No merge, deployment or extra-user invitation follows this review. First-five-minute future live check is actual revision/health/boundary and permitted input; first hour inspect attempt status/artifact manifest and failure visibility. No such smoke check was executed here.

### Section 10: Long-Term Trajectory Review
Reversibility is 5/5 for file-based pilot artifacts; human labeling and adjudication are the principal recurring costs. One debt item is runtime proof/remediation if inspection fails; another is observed interface friction, already deferred P6. A future engineer can reproduce the frozen evaluated scope from references and attempt inventory without a new framework. The right next platform capability would follow repeated evidence, not a queue or review screen chosen before use. Five worksheet additions fit the pilot; no rejected service becomes a hidden prerequisite.

### Section 11: Design & UX Review
UI review applies because original session/timing/chat requirements remain in the plan, although the pilot introduces no screen. Existing ProjectChat uses a labeled textarea, keyboard button, log and alert roles; JobTiming names the two measurements and warns on incomplete history. Rendering, contrast and responsive behavior remain untested, so source-level intent is not a live design score. The worksheet's Vietnamese cover puts evaluated scope and limitations before findings; preserve native Excel/PDF navigation rather than inventing a visual system.
```
login -> replaced/error OR project list -> job status + timing
                                        -> chat collapsed -> loading/empty/error/reply
                                        -> reports -> cover/scope -> cited finding -> source -> human action
partial/failed -> visible limitation -> admin follow-up; zero findings -> no assumed all-clear
```
| Feature | Loading | Empty | Error | Success | Partial |
|---|---|---|---|---|---|
| existing chat | pending | prompt | stored failure | escaped reply | evidence-scope limitation |
| existing timing | current attempt | unavailable history | interrupted | stopped duration | measured subtotal flagged |
| pilot worksheet | awaiting inputs | inconclusive | blocked check | scoped evidence | explicit unresolved/limitation |
Human source verification is the journey's success; generic chat or dashboard styling cannot supply it. Live `/design-review` remains a post-implementation check when the user authorizes verification; design and DX phases follow now.

### NOT in scope
Deferred: P6 in TODOS.md, evidence screen only after observation demonstrates friction. Skipped: P7 scoring service/new queue, no measured need; public SaaS/automatic rollout/custom training claims, outside internal pilot. Runtime redesign remains a separate scoped response if required isolation proof fails; required isolation is not deferred or waived. All original application requirements remain obligations.

### What already exists
Auth API/deps and CustomerActiveSession reuse cookie ownership/session policy; AuditRun and migrate_and_recover reuse attempt timing/restart handling; audit_runner reuses Codex + full BOQ skill and outputs; project_chat/chat API reuse bounded customer context and private threads; ProjectChat/JobTiming reuse existing screens; external boq templates/validator/regressions supply structural checks. None substitutes independent labels or actual runtime permission checks.

### Dream state delta
This plan establishes a repeatable, scoped first quality measurement and architect observation. It does not establish statistical accuracy, broad disciplinary coverage, production isolation or net time savings. Those claims require the named independent inputs/proofs and later representative assessment.

### Failure Modes Registry
| Codepath | Failure | Rescued? | Proof planned | User sees | Logged? |
|---|---|---|---|---|---|
| intake | no independent reference/approval | Yes: block | checklist | missing dependency | worksheet |
| live runtime | shared filesystem/credentials | Unknown: execution blocked | admin enforced-boundary proof | live blocked | prerequisite record |
| attempt | timeout/partial/stale files | Yes: no pass | retained baseline/manifest | failed/partial | attempt inventory |
| ledgers | structural blocked/missing | Yes: no pass | validator | blocked | validator output |
| findings | hallucination/duplicate/miss | Yes: adjudicate | human labels | class and evidence | score worksheet |
| narrative | unsupported all-clear/omitted limitation | Yes: fail readiness | required-check comparison | unsupported/limited | narrative checklist |
| coverage | zero critical references | Yes: unassessed | zero-denominator case | limited coverage | coverage matrix |
| observation | no tasks/admin help/misunderstanding | Yes: inconclusive/fail | task record | limited pilot | observer record |
Runtime boundary remains one critical prerequisite gap until verified; none is silently accepted. No current production readiness or measured quality is claimed.

### CEO Implementation Tasks
- [ ] **T1 (P1, human ~2h / assisted ~40min, excludes expert labeling)** — pilot docs — Prepare worksheet and runbook with C1–C3/C6–C8 fields and gates. Files: planned docs/quality-pilot-worksheet.md, docs/quality-pilot-runbook.md; README.md factual correction. Verify: synthetic worked examples including zero denominators, duplicate root, partial attempt, unsupported all-clear and failed usability cannot pass; compare every field to approved design.
- [ ] **T2 (P1, human unknown / assisted ~30min analysis)** — prerequisites — Admin obtains approved source packages/reviewer and verifies actual runtime boundary/revision. Files: to be determined (private approved storage). Verify: record independent labels and denied sibling/config/credential access without displaying secret contents; no live execution until proof, offline mode explicitly limited.
- [ ] **T3 (P1, human unknown / assisted ~1h setup plus actual runtimes)** — evaluation — Rehearse one package, then complete three-package corpus and nonempty observation; preserve every attempt and scoped admin decision. Files: to be determined (private result storage). Verify: all approved gates, coverage and individual human outcomes; no statistical/speed claims.
- [ ] **T4 (P1, human ~2h / assisted ~30min, excludes live approval wait)** — existing contracts — Verify retained session/timing/chat criteria in a controlled test environment. Files: backend/tests/test_auth.py, backend/tests/test_timing_chat.py, backend/tests/test_chat_gateway.py, frontend/components/JobTiming.tsx, frontend/components/ProjectChat.tsx. Verify: current caller-level tests plus two-account/concurrent/restart/stale-context/runtime isolation proof; tests are planned, not run. Scope any found code repair separately.
Estimates are documentation/review effort, not guaranteed AI speedups or expert availability.

### CEO Completion Summary
| Item | Outcome |
|---|---|
| Mode / Step 0 | SELECTIVE EXPANSION; U1–U3, C1–C8; scope-doc spec PASS 9/10 |
| System audit | Features exist; category-only completion; shared audit mounts; deployed state unknown |
| Section 1 Architecture | 1 unknown boundary prerequisite, documentation reuses existing system |
| Section 2 Errors | 7 rows, runtime proof gap preserved separately |
| Section 3 Security | 6 threats, 1 high-impact unverified execution prerequisite |
| Section 4 Data/UX | 5 interaction groups, failure dispositions explicit; production proofs pending |
| Section 5 Quality | 1 maintenance concern; no runtime rewrite added |
| Section 6 Tests | Diagram produced; pilot examples + retained tests planned, unexecuted |
| Section 7 Performance | 1 capacity unknown; no p99 evidence |
| Section 8 Observability | Missing measurements handled by proposed file inventory |
| Section 9 Deployment | 1 live boundary/revision risk; no deployment authorized |
| Section 10 Future | Reversibility 5/5; 2 conditional debt items |
| Section 11 Design | Existing UI coverage evaluated from source; live usability unknown |
| NOT in scope / reuse / dream delta | Written above; P6 deferred, P7 skipped |
| Error/rescue / failure modes | 7 / 8 rows; 1 critical unverified runtime prerequisite |
| TODOS | 1 approved deferral P6 persisted |
| Scope proposals | 7 proposed, 5 accepted, 1 deferred, 1 skipped |
| CEO plan | Persisted in ceo-plans/2026-10-07-boq-quality-pilot.md |
| Outside / native | Claude Code unavailable not installed / completed 3 findings, all incorporated |
| Lake Score | N/A; no scored coverage options were presented |
| Diagrams / stale audit | 6 types: architecture/data/state/error/deploy/rollback; existing plan had none; new diagrams consistent |
| Unresolved design decisions | 0; packages/reviewer/runtime proof remain execution dependencies |
Approval readiness: PASS for review scope U1/U2 user-approved and U3/C1–C8 autoplan-authorized. This is not a production-ready verdict. No User Challenges; C2 taste awaits final gate. No cleanup or promotion requested; keep local CEO archive and current repository plan/design.

### CEO state and error-flow diagrams
```
manual package: DRAFT -> APPROVED/FROZEN -> ATTEMPTED -> ADJUDICATED -> OBSERVED -> ADMIN DECISION
                   | missing approval     | failure/partial         | failed/empty
                   v                      v                         v
                 BLOCKED              LIMITED (attempt kept)     LIMITED
Readiness is forbidden until the three-package corpus and every independent gate pass.
New revision/reference -> new version; never mutate the baseline to force a transition.
```
```
run/validator/evidence/observation error -> preserve attempt + cause + evidence
  -> identify blocked vs partial vs unresolved -> show scope/limitation
  -> keep pilot limited -> human review -> scoped repair -> distinct reassessment
```

### Design phase intake
Design methodology read 1–600 (250–420 recovered), 601–1200 (760–885 recovered), 1201–1800 (1400–1535 recovered), 1801–1931 through EOF. UI scope retained from original session/timing/chat acceptance obligations; pilot itself adds worksheet/runbook only. No DESIGN.md exists; reuse current Shell, JobTiming, ProjectChat, job-detail layout and Tailwind ink/brand/paper/line tokens. Source inspection is not live pixel QA. Review history read: NO_REVIEWS; no earlier verified design cycle. Step 0 initial completeness 6/10: evidence flow clear but concrete states, worksheet hierarchy and viewport/accessibility proof need detail. A 10 requires all visible states and navigation/accessibility outcomes specified and their future proof named; no new visual system required.
Focus all seven passes (autoplan P1). Designer version probe succeeded, but automatic approval review rejected generation for external transmission of internal workflow/product details. No images generated or approved. Optional user approval requested; continue text review with generation unavailable, no alternative egress attempted. No hand-built wireframe or board substituted. Mockup generation is not a production change.

### Design native review / litmus
Native autoplan_design_voice completed INPUT design c55905de97104f7170f03e6194f9eae1e366e18f3b4935bd99e8f9c3071640f6; 5 findings (2 high, 3 medium), no critical. Accept separate status hierarchy, concrete source locators, visible empty/blocked cases, separate observer key and participant material, and existing UI state inventory. Claude Code rechecked enabled/not_installed; no external voice. Every consensus cell N/A.
| Litmus | Native report / available evidence | Outside | Consensus |
|---|---|---|---|
| Brand unmistakable | Not rated by native; existing Shell says SBTech BOQ AUDIT | unavailable | N/A |
| Strong anchor | Not rated; source PDF workspace primary | unavailable | N/A |
| Scannable headlines | Gap: cover status order unspecified | unavailable | N/A |
| One job per section | Gap: observer key vs participant materials mixed | unavailable | N/A |
| Cards necessary | Not specified by native; existing sections use cards | unavailable | N/A |
| Motion useful | Not specified; new worksheet requires none | unavailable | N/A |
| Clear without shadows | Not specified; text-only artifact unaffected | unavailable | N/A |
No visual hard rejection can be cleared from a mockup: none generated. Source-level existing gradients, tiny secondary text and stacked aside cards are flagged for contextual verification, not falsely scored as rendered success. OPERATE for portal, READ for runbook, OPERATE for adjudication worksheet; marketing/experience rules do not apply.

### Design decision audit
| ID | Finding / alternatives | Auto-decision and principle | Classification |
|---|---|---|---|
| D1 | Completion visually conflated with readiness / prose warning only | Separate three labeled states before findings; P1/P5 | mechanical |
| D2 | Ambiguous source locator / filename-page only | Exact filename/revision + page/sheet/cell and object; P1 | mechanical |
| D3 | Blank/empty assessment ambiguous / leave blank | Explicit visible status, N/A, reason and responsible next action; P1 | mechanical |
| D4 | Observation answer leakage / shared worksheet | Separate neutral participant sheet and observer key; P1 | mechanical |
| D5 | Existing UI states generic / assume current code enough | Inventory current labels/actions and verify original acceptance; P4 | mechanical |
| D6 | Small text/focus/viewport proof unspecified / cosmetic redesign | Specify accessible verification outcomes; repair actual gaps under separate concrete scope; P3/P5 | mechanical |

### Design Pass 1 — Information Architecture (7 → 9/10)
The primary finding is D1: a cover without ordered statuses makes a generated file appear endorsed. Accept the cover hierarchy below; this is a worksheet specification, not a production status redesign. Reuse the job's primary PDF view and report-download path; chat is secondary and must not obscure evidence. A 10 also needs live confirmation with the architect, which remains an execution check.
```
Worksheet: package/revision/scope -> human-review instruction
 -> execution state | evidence state | pilot assessment state
 -> limitations + assessed/unassessed coverage -> finding ID/source/action rows
Portal retained: project/status -> drawing/report -> named time measures -> collapsed chat
Navigation: report finding -> exact source/revision -> calculation -> human judgment -> action
```

### Design Pass 2 — Interaction State Coverage (6 → 9/10)
D3 prevents missing cells from looking like clean findings. State copy must communicate what is unavailable and who acts next; no color-only indication and no percentage when the denominator is zero. Existing portal labels are inventoried under D5; API status alone is not sufficient proof of customer comprehension.
| Surface/state | Visible content / next action |
|---|---|
| Awaiting package/reference | “Chưa đánh giá — thiếu hồ sơ hoặc đối chiếu độc lập”; admin supplies approved inputs |
| Process finished only | “Chạy hoàn tất — chưa xác nhận chất lượng”; human verifies evidence |
| Zero findings | “Không có phát hiện trong báo cáo”; show required-check/narrative assessment and known reference misses; no automatic all-clear |
| No reference denominator | “Chưa đủ đối chiếu — N/A”; show unavailable reference and excluded counts |
| Only unresolved findings | “Chưa thể kết luận”; show missing source/assumption and reviewer next step |
| Failed/partial/structurally blocked | “Chưa hoàn tất / chỉ có kết quả một phần / chưa đạt kiểm tra cấu trúc”; retained attempt ID and admin follow-up |
| Offline provenance missing | “Đánh giá ngoại tuyến — nguồn lần chạy chưa xác minh”; no live execution or isolation claim |
| Critical references zero | “Phát hiện lỗi nghiêm trọng: chưa đánh giá”; no critical-detection pass |
| Scoped supported findings | Evidence-supported classification with source and human next action; pilot assessment remains separate |
Synthetic state demonstrations include each row, showing denominator/N/A, evidence missing, owner/action. This is planned documentation validation, not a real pilot result.

### Design Pass 3 — Journey & Emotional Arc (7 → 9/10)
The high-value transition is from report received to source independently located. D2 names a complete locator and D4 prevents the observation itself from revealing the answer. The observer records the architect's interpretation before showing adjudication; every task retains the existing all-pass rule. A 10 requires an actual observation, absent today.
| Step | User does | Expected feeling (hypothesis) | Plan support |
|---|---|---|---|
| 1 / first 5 seconds | Identifies package/revision/status | Oriented, aware of limits | Ordered Vietnamese cover |
| 2 | Chooses finding from ordinary report | Curious, appropriately skeptical | Neutral participant ID, no answer key |
| 3 / first 5 minutes | Opens exact source location | Confident or clearly blocked | Filename/revision/page/sheet/cell/object; missing fields explicit |
| 4 | Explains issue, uncertainty and next action | Owns engineering judgment | No admin coaching; separate observer record |
| 5 / continuing use | Compares observations and limitations | Trust grounded in evidence | Individual outcomes and scope-limited admin decision |
No emotional or time-saving claim is measured. Blank source locators cannot become successful independent verification.

### Design Pass 4 — Specificity / AI Slop (7 → 8/10)
The plan now describes exact user work rather than a generic dashboard: source citation, separate state labels, reviewer key and participant task. No hero, carousel, imagery or new card grid is introduced; new worksheet motion is unnecessary. Existing source has a decorative radial background, gradient CTA and multiple card panels, so visual compliance is unverified; retain evidence-first hierarchy and contextual live checks instead of redesigning the app during this pilot. Typography/contrast must serve reading and never hide limitations. A 10 needs rendered inspection and successful observed verification, not a more elaborate style brief.

### Design Pass 5 — Design System Alignment (6 → 8/10)
No DESIGN.md exists, so no alignment claim is possible. Reuse existing Tailwind colors ink #17212b (main text), brand #164e63 (action/identity), paper #f4f6f5 (background), line #d8dfdc (boundaries), and the current inherited font (app/layout.tsx declares no explicit font family); the pilot Markdown inherits the approved viewer and introduces no fonts/dependency. Worksheet rows have labeled columns and semantic headings, no color-coded-only meaning. Do not generate an unsolicited design system; document these current tokens and inventory actual styling before any later UI change. Score measures plan specificity only; no rendered style acceptance implied.

### Design Pass 6 — Responsive & Accessibility (5 → 8/10)
D6 adds explicit proof requirements to the existing contract check. On desktop keep the drawing/report as primary workspace and status/time/chat as secondary; on tablet and narrow widths verify a single coherent reading order, reachable report downloads and readable source references without overlapping or clipping controls. Check at 375, 768 and 1440 CSS px, 200% zoom, keyboard-only navigation and a screen reader; source flags (small text, embedded PDF and sticky aside) guide what to inspect. Body instructions/findings must be readable at 16px equivalent, contrast at least 4.5:1, important controls at least 44px, clear visible labels/focus, and status/limitations conveyed in words. Existing source-level log/alert/aria-expanded labels are reused; their rendering and PDF-reader support are unknown. If an actual current UI acceptance gap is found, keep that verification failed and scope the smallest repair before claiming readiness; do not waive accessibility or silently turn this worksheet pilot into a redesign. A 10 needs verified rendered results.

### Design Pass 7 — Decision register (unscored)
D1–D6 are individually auto-decided under autoplan; they preserve the approved human-judgment and file-pilot approach. No unresolved policy choice remains; optional external mockup permission is pending, no images are approved or necessary for executing the text fallback. P6 evidence screen remains the prior explicit deferral in TODOS.md, not an accepted implementation task. Current screens and source locators require the planned verification; their absence today is execution uncertainty.

### Design required outputs
NOT in scope: new review screen P6 deferred pending observed friction; full visual rebrand/design-system creation and decorative animation skipped as unrelated; external mockups blocked by automatic approval review pending optional user authorization. What already exists: Shell/logo/navigation, job PDF viewer/downloads, JobTiming, ProjectChat, Tailwind palette and layout font; no DESIGN.md. No additional deferred TODO proposal: P6 already recorded, required state/accessibility verification stays in tasks.

### Design Implementation Tasks
- [ ] **T1 (P1, human ~1h / assisted ~20min)** — worksheet — Specify D1–D4 in docs/quality-pilot-worksheet.md and docs/quality-pilot-runbook.md: visible state examples, complete evidence locators and separate participant/observer material. Verify every synthetic state and usable/incomplete citation; observer answer key never shown first. Combines with CEO T1 during aggregation.
- [ ] **T2 (P1, human ~2h / assisted ~30min, excludes live approval wait)** — retained UI — Inventory current Vietnamese auth/timing/chat labels and verify D5/D6 source-to-rendered behavior. Files: planned docs/quality-pilot-runbook.md; existing frontend/components/ProjectChat.tsx, frontend/components/JobTiming.tsx, frontend/app/jobs/[job_code]/page.tsx are inspection targets, not approved edits. Verify viewport/zoom/keyboard/screen-reader, labeled metrics, saved pending/retry/quota/conflict states; log any failed criterion and scope repair. Combines with CEO T4.

### Design Completion Summary
| Item | Outcome |
|---|---|
| System audit | Existing UI, no DESIGN.md; no live/rendered evidence |
| Step 0 initial impression | 6/10, all passes selected by autoplan |
| Pass 1 hierarchy | 7 → 9 |
| Pass 2 states | 6 → 9 |
| Pass 3 journey | 7 → 9 |
| Pass 4 specificity | 7 → 8 |
| Pass 5 design system | 6 → 8 |
| Pass 6 responsive/accessibility | 5 → 8 |
| Pass 7 decisions | 6 resolved; P6 prior deferral carried, no new deferrals |
| NOT in scope / reuse | Written; prior screen deferral and no rebrand |
| TODOS | 0 new; P6 retained |
| Mockups | 0 generated, 0 approved; automatic approval review blocked external brief |
| Decisions / unresolved | 6 added / 0 substantive design choices; optional mockup permission pending |
| Overall plan completeness | lowest pass: 5 → 8/10; not a measured live UI score |
Plan specifications are sufficient for the planned pilot, with rendered usability/accessibility proof still required. Native findings incorporated; outside coverage unavailable. No new durable learning beyond the already recorded process-completion/quality distinction; no brain calibration write-back authorized.

<!-- autoplan-accepted:design -->
- Order the Vietnamese worksheet cover as package/revision/scope, prominent human-review instruction, separately labeled execution/evidence/pilot-assessment states, required limitations and assessed/unassessed coverage, then findings/source/actions. Include “Chạy hoàn tất — chưa xác nhận chất lượng” and zero-critical “Chưa đánh giá”; never convey these distinctions through color alone.
- Each finding row shall include stable ID, exact source filename/revision, PDF page and printed drawing/sheet identifier when different, Excel worksheet and cell/row, object/location, calculation/assumption and corrective/clarifying/justifiably unresolved next action. Explicitly mark unavailable locators. Verify one usable and one incomplete synthetic citation.
- Demonstrate visible worksheet outcomes for awaiting inputs, zero findings, no reference denominator, unresolved-only findings, failed/partial/structurally blocked attempts, offline artifacts with missing provenance, zero critical coverage and supported scoped findings. Each shows denominator/N/A, missing evidence, next action and owner; zero findings still requires narrative/required-check adjudication.
- Keep participant task material separate from the observer answer key and recording sheet. Give the architect the ordinary Vietnamese report and neutral finding ID/source task only; record source location, interpretation, uncertainty, next action, time and assistance before revealing adjudication. Preserve five-or-all fewer selection, nonempty exercise and all-observed-tasks-pass rule.
- During retained UI verification inventory existing Vietnamese labels/actions for session replacement, total turnaround and AI execution/history/interruption, saved pending/error chat, refresh recovery, quota/capacity and concurrent-turn conflicts. Preserve primary project/report/source navigation and secondary collapsible chat; propose changes only for concrete acceptance gaps.
- Verify retained UI at 375/768/1440 CSS px, 200% zoom, keyboard-only and screen-reader use, including embedded report/download alternatives. Require readable 16px-equivalent body instructions/findings, 4.5:1 body contrast, 44px important controls, visible labels/focus and word-based status/limitations. Use existing components/palette/font, no redesign; failed criteria remain failed until the smallest separately scoped repair is verified. No rendered accessibility or quality pass is claimed by source review.
<!-- /autoplan-accepted:design -->

### DX intake / Step 0A–0C (before scoring)
Full methodology read: 1–600, 601–1200, 1201–1800, 1801–2107 through EOF, no truncation. Hall reference Pass 1 read for Step 0D. Detected API/service + internal operator documentation/platform; not a public SDK or agent-primary product. DX remains applicable by positive scope terms even though architects are end users. Mode DX POLISH (autoplan override). Persona auto-inferred A under P6 from README GCE/Compose and admin-owned deployment: a developer/technical operator helping Thang maintain this single-instance service. No cold persona question replaces confirmed internal-user context. Aside unavailable from prior probe; WebSearch used with generalized primary-source queries. No prior DX review record.

TARGET DEVELOPER PERSONA
Who: technical maintainer/admin helper; architects are not required to use CLI.
Context: understand and rehearse the quality-pilot procedure without touching production; later inspect the existing deployment under explicit permission.
Tolerance: target a first understood synthetic example in <=5 min; actual full install/audit durations unknown.
Expects: explicit prerequisites, safe paths, concrete expected evidence and failure/action instructions.

### Developer Perspective (Step 0B; inferred, not an observed interview)
I open the repository to help maintain an internal service. The README tells me how to start Docker and create accounts, and its rollout link describes session, timing and chat behavior. I can see a familiar FastAPI and Next.js stack. I expect to reuse it rather than install another framework.

The introduction says the audit is manual, but the automatic-audit section describes a Codex worker. I would need to resolve that contradiction before interpreting a completed job. The deployment guide also tells me to isolate untrusted drawings; Compose alone does not demonstrate that audit boundary. I cannot treat a working login or a healthy container as evidence of safe, correct engineering review.

For the pilot, I want one clear entry point: what I can rehearse locally, which files are approved inputs, where the independent answer key belongs, and what a supported finding looks like. I should be able to open the planned synthetic worksheet example, locate its source and understand why another issue remains unresolved. I should not need production credentials to do that.

When real artifacts become available, I need every failed attempt retained and each limitation visible. If the runtime prerequisite is unknown, I expect an explicit blocked state and an offline alternative with its limits. Actual setup time, confusion and reviewer effort have not been observed; these expectations are a grounded prediction from the inspected docs and code.

### Competitive DX benchmark (Step 0C)
| Tool / reference | Documented start → useful result | Time / evidence | DX choice | Source |
|---|---|---|---|---|
| Docker Compose tutorial | prepared Docker + project files → running sample | unmeasured here; documented sequence | exact command and browser result | https://docs.docker.com/compose/gettingstarted/ |
| FastAPI tutorial | prepared Python environment + first code → development response/docs | unmeasured here; different endpoint | minimal runnable example, then details | https://fastapi.tiangolo.com/tutorial/first-steps/ |
| Redash self-host setup | prepared host/image → configured service | unmeasured; multi-service setup, not equivalent to worksheet | explicit deployment prerequisites | https://redash.io/help/open-source/setup/ |
| BOQ pilot | checked-out repository + ordinary Markdown/PDF/spreadsheet viewer → first understood synthetic evidence example | current unknown, target <=5 min proposed | reuse planned worksheet/runbook; no model/production access |
No peer onboarding timing was observed or claimed, so only DX choices are comparable. Full cold installation, credentials, first real audit and expert verification have separate unknown clocks and cannot be relabeled as this rehearsal.

### DX Step 0C target decision
X1 auto-select Competitive (2–5 min, <=5 min) under P5/P6 for the exact documented rehearsal clock above, including reading and opening the synthetic example. Start: maintainer first opens README in a checked-out repo with an ordinary file viewer already installed. End: explains one source-supported match and one explicitly unresolved example from the planned worked worksheet, including the correct limitation. This is a documentation rehearsal, not a healthy deployment or real audit. Feasibility depends on implementing the already-approved synthetic example and timing a fresh reader; current time remains unknown. Target selection adds no telemetry, recurring process or automatic release gate.

### DX Step 0D–0G
X2 magical moment auto-select existing file-based worked example (P5): a maintainer follows the cited source and can tell supported from unresolved without credentials. Alternative live hosted playground is out of scope; plain prose without the approved demonstration is less complete. No new artifact beyond planned worksheet/runbook is added.
0E: DX POLISH fixed by autoplan; no API redesign for score.
| Stage | Maintainer does / observed source | Friction | Disposition |
|---|---|---|---|
| Discover | README intro + automatic audit + rollout link | contradictory manual/automatic description | C5 factual correction planned |
| Install | README Development/GCE; Docker/env/auth prerequisites | cold setup time unknown; production config unsuitable for tests | explicit prereqs and offline rehearsal, C3/X1 |
| Hello World | planned synthetic worksheet example | no example yet | CEO C1/D1–D4 already require demonstration |
| Real Usage | approved 3 packages, hidden labels, validator | admin/reviewer/input dependencies | preserved blocked prerequisites, no default clean |
| Debug | README logs + FEATURE_ROLLOUT troubleshooting | raw diagnosis may be privileged; outcome vs cause/action must be clear | public generic errors, private safe runbook mapping |
| Upgrade | FEATURE_ROLLOUT migration/recovery; backup docs | docs claim prior tests, not current deployment proof | pin observed version/hash and retain all baseline versions |
First-time confusion roleplay (simulated timestamps, not measurements): T+0:00 README says manual audit; T+0:30 automatic-audit heading disagrees (C5). T+1:00 rollout explains current features but its historical verification does not prove this GCP revision (C3). T+2:00 reader seeks planned synthetic example; not implemented yet (C1). T+3:00 reader can identify missing prerequisites and next action from the future runbook; successful rehearsal remains unobserved. No imagined behavior is labeled a measured defect.

### DX native review and decisions
Native autoplan_dx_voice completed INPUT dx eb49b0a21bde0bcbc315b870133d2d7eedf228f4e1a8095e52af7d935bbb0a5f: three medium findings, zero critical. X3: exact offline rehearsal entry point/commands/completion; X4: safe troubleshooting problem/cause/fix/role/link; X5: discoverable existing config and worksheet choices. Each auto-accepted independently under P1/P5; documentation within existing scope, no new runtime or interface. Outside preflight rechecked enabled/not_installed, unavailable.
| Dimension | Native | Outside | Consensus |
|---|---|---|---|
| Getting started <5 min | Unmeasured; walkthrough missing | unavailable | N/A |
| Naming guessable | Existing routes sensible; commands need documentation | unavailable | N/A |
| Actionable errors | Recovery table missing | unavailable | N/A |
| Docs complete/findable | Entry point missing | unavailable | N/A |
| Safe upgrade | No new runtime selected; version proof pending | unavailable | N/A |
| Environment friction | Offline prerequisites/config map needed | unavailable | N/A |

### DX Pass 1 — Getting Started (5 → 8/10 plan)
Hall Pass 1 read at Step 0D and again for this pass. Adopt X3's three-step documentation rehearsal: (1) open README's planned quality-pilot link and runbook prerequisites (~1 min budget); (2) open its linked synthetic worked worksheet and cited synthetic source (~1–2 min budget); (3) independently explain the supported match and unresolved example (~1–2 min budget). These are target budgets, not measured times. Source and example shall be self-contained inside the already planned worksheet/runbook, with a literal SYNTHETIC / not pilot result label; no separate fixture service is needed. Local install/model credentials/production access are unnecessary for this clock. A full cold deployment or real audit is a separate journey and remains unknown. A 10 requires a fresh-reader walkthrough at the exact start/end; no canned output may stand in for engineering quality.

### DX Pass 2 — API/CLI/SDK (6 → 8/10 plan)
Hall Pass 2 read fully. Existing job/chat routes and argparse admin CLI are discoverable to the technical maintainer; architects use the portal. No new API/SDK or ID scheme is warranted. X5 documents current worksheet choices (artifact copy path, package/revision/scope, independently set tolerances, observed runtime/config version) with owner, permitted alternatives and effects; readiness gates are fixed rather than configurable defaults. Advanced runtime diagnosis follows the simple offline example.
The installed BOQ validator source accepts `workdir` and `--out coverage_result.json`. Planned runbook example: `python3 /path/to/installed/boq-audit/scripts/validate_run.py "<approved-copied-run-dir>" --out coverage_result.json`; resolve placeholders and verify Python/skill location before execution. The command writes into the copied run directory, so never point it at production originals. It prints candidate_release_status, blocking_checks and report, exits 2 for BLOCKED and 0 otherwise. A zero exit may still mean PARTIAL_WITH_OPEN_ITEMS_REVIEW_REQUIRED; READY_FOR_ENGINEER_REVIEW remains metadata-only and requires human engineering signoff. No validator call was executed today. Retries preserve attempt IDs/artifacts; chat quota/concurrency and unavailable history are documented rather than hidden. No latency guarantee is established. A 10 requires a fresh maintainer to use the exact example correctly; API latency/idempotency and deployed behavior remain verification work.

### DX Pass 3 — Errors & Debugging (5 → 8/10 plan)
Hall Pass 3 read fully. X4 adds a compact problem/cause/fix/role/runbook-link table; exact internal details stay admin-only. Three traced paths:
| Path / actual current output | Missing guidance / planned operator recovery |
|---|---|
| audit_runner: “Không tìm thấy Codex CLI trên audit worker”; missing skill names an internal path in admin_notes | Admin checks installed worker binary/skill and observed version, retains FAILED attempt, resolves prerequisite before an explicitly approved retry. Customer gets safe failure/action wording, not private paths. Link planned runbook Prerequisites/Troubleshooting. |
| chat: 429 “Bạn đã dùng hết lượt chat trong khoảng thời gian này.” with Retry-After; 409 busy/pending or changed project | Explain quota versus capacity versus stale-context state separately; wait according to server guidance, inspect saved question, refresh changed report and explicitly resubmit only after checking prior outcome. Do not promise transparent/idempotent retries. Link Troubleshooting/Chat. |
| validate_run: BLOCKED / blocking_checks / report, exit 2 | Admin opens copied coverage_result.json, identifies exact missing/invalid ledger references, preserves attempt and asks reviewer to repair missing evidence. No repeated generation to clear an engineering failure; metadata success never proves truth. Link Structural checks/Limitations. |
The same table covers session replacement, interrupted attempt, missing source evidence and generic chat 503 with saved question. Public generic errors protect details; privileged diagnostics use approved redacted logs only, never raw credentials or uncontrolled stack traces. A prompt/cwd is not a filesystem or credential boundary; absent proof blocks live tests. No new debug API or verbose customer stack trace is proposed. A 10 needs actual source-to-user recovery verification; current strings are evidence, not an assertion that every existing error meets all tiers.

### DX Pass 4 — Documentation & Learning (5 → 8/10 plan)
Hall Pass 4 read fully; its industry statistics are not adopted as this project's evidence. README links directly to the planned runbook, which links worksheet and separates three-step tutorial, field/formula reference, prerequisites, structural validation, troubleshooting and limitations. A maintainer should find the relevant section within two minutes, to be observed rather than asserted. X3 requires resolved local validator paths, approved copied artifact paths, exact expected statuses/exit meanings and completion signal; placeholders are labeled until actual approved inputs exist. The embedded synthetic worked example permits learning without secrets or production. Pin repository/skill/model/config identities in each attempt and keep historical rollout claims separate from current verification. Markdown headings/editor search suffice; no docs site, injected credentials or playground. A 10 needs a fresh-reader walkthrough with verified links/examples and the documented version.

### DX Pass 5 — Upgrade & Migration (6 → 8/10 plan)
Hall Pass 5 read fully. This pilot changes documentation only, with no runtime/schema/API migration; codemods and deprecation machinery are inapplicable. Existing FEATURE_ROLLOUT and README backup/recovery instructions remain the operator reference and are historical, not deployment proof. Version the corpus, labels, scope/tolerances, artifacts and validator/skill/runtime identities together; changed adjudication produces a new version while preserving baseline and failed attempts. Compare only compatible scope/version and state all changes. Any later runtime repair/upgrade needs separate reviewed scope and its migration/rollback checks; no dependency upgrade is bundled. A 10 requires an actual reproducible versioned rehearsal and verified recovery, absent today.

### DX Pass 6 — Environment & Tooling (6 → 8/10 plan)
Hall Pass 6 read fully; no claimed speed ratios or generic productivity statistics are treated as local evidence. The chosen rehearsal needs only a checked-out repo and ordinary viewers; actual validator analysis additionally needs Python 3 and the installed BOQ skill/approved artifact copy. Existing FastAPI/Next TypeScript, Docker development flow and mock-based tests are reused; source/docs describe them, but hot reload, CI behavior, architecture compatibility and cold installation are unverified. No editor extension, new test framework, fixture service or CI job is needed for a file pilot. The synthetic worked example is a safe documentation rehearsal, not a dry-run implementation of Codex. Linux/GCE deployment docs do not establish Mac/Windows/ARM compatibility. Prerequisite mismatch blocks the respective step with an owner/action, not the viewer-only exercise. A 10 requires demonstrated reproducibility in the maintainer's actual environment and separately authorized test execution.

### DX Pass 7 — Internal Support & Ecosystem (6 → 8/10 plan)
Hall Pass 7 read fully; public acquisition and ecosystem multipliers are inapplicable to this admin-owned internal service. Thang owns access/package approval and routes engineering questions to the independent reviewer. The runbook identifies those roles and how to record a blocked step, observed confusion or reproducible issue with attempt ID and redacted evidence. No external messages are sent. The synthetic demonstration plus later approved three-package exercise supplies context; public examples must not expose drawings. Open-source license, public plugin ecosystem, contribution program, free tier and public pricing are outside the confirmed product scope; no license/community claim is made. Model/cloud usage still has actual cost unknown, so the pilot records approved attempts and makes no free-use guarantee. A 10 needs observed successful support handoff, not a public forum.

### DX Pass 8 — Measurement & Feedback (5 → 8/10 plan)
Hall Pass 8 read; Claude Code Skill appendix loaded but inapplicable: this is a portal/service using an existing skill, not a new skill/MCP product. Measure the approved README-open to correctly explaining supported plus unresolved synthetic examples clock with a fresh reader and ordinary viewers already installed. Include reading/navigation/example opening; <=5 minutes is a documentation target, current unknown. Record elapsed time, completion, assistance and confusion in the existing observation worksheet; record incomplete/blocked results rather than dropping them. This is a single planned manual rehearsal, with no telemetry, automated release gate or recurring process inferred. Full cold setup, real model audit, structural validation and engineering adjudication have separate clocks. The existing pilot observation and attempt/version records permit later comparison against this plan; actual friction supplies any subsequent improvement decision. A 10 requires observed evidence, not an instrumentation project.

### DX Scorecard / required outputs
Scores are plan specificity before → after this review; no previous completed DX baseline exists and no live DX measurement was performed.
| Dimension | Before | After | Trend |
|---|---:|---:|---|
| Getting started | 5 | 8 | +3 |
| API/CLI/SDK | 6 | 8 | +2 |
| Errors | 5 | 8 | +3 |
| Documentation | 5 | 8 | +3 |
| Upgrade | 6 | 8 | +2 |
| Environment | 6 | 8 | +2 |
| Internal support/community applicability | 6 | 8 | +2 |
| Measurement | 5 | 8 | +3 |
| Overall (arithmetic mean) | 5.5 | 8 | +2.5 |
TTHW: unknown → <=5 min documentation target only; Competitive tier selected, actual rank unmeasured. Magical moment designed via existing planned file example. Product API/service/internal operator docs; mode DX POLISH. Principle coverage: Zero Friction (three viewer-only steps); Learn by Doing (synthetic source/example); Fight Uncertainty (safe recovery/state meanings); Opinionated + Escape Hatches (fixed gates, explicit approved alternatives); Code in Context (copied-artifact validator example); Magical Moments (supported versus unresolved understanding). All covered in the plan, proof remains outstanding.
Required persona/empathy/benchmark/moment/journey/confusion outputs are above. NOT in scope: public SDK/API redesign, hosted playground, new telemetry/CI/docs portal/support community. Reuse: README, rollout, worksheet/runbook, installed validator, current portal and source tests. TODOS: no new deferred proposals; prior evidence-screen P6 remains.

### DX Implementation Checklist
- [ ] <=5-minute exact documentation rehearsal; unknown until observed.
- [ ] One-command installation: N/A to viewer-only rehearsal; actual app cold install remains documented multi-step and unmeasured.
- [ ] First useful output and magical moment: implement and independently explain self-contained synthetic example.
- [ ] Error problem/cause/fix/link: implement safe runbook mapping; verify real UI gaps separately, no universal current compliance claim.
- [ ] Guessable CLI/fields and sensible defaults: document existing flags/choices; no default engineering tolerances or configurable gates.
- [ ] Working copy-paste examples and contextual use cases: resolve paths, copied inputs, expected output/status; synthetic is labeled, real cases require approval.
- [ ] Upgrade/migration: preserve version history and reference existing recovery docs; no new runtime change.
- [ ] Breaking-change warnings/codemods: N/A, no breaking change selected.
- [ ] TypeScript types: existing frontend; source only, not reverified.
- [ ] CI without special configuration: unverified and not required for file rehearsal; no CI addition.
- [ ] Free tier/no credit card: N/A internal tool, actual compute cost unknown.
- [ ] Changelog: no dedicated file found; existing rollout plus versioned pilot records suffice for this documentation scope.
- [ ] Documentation search: ordinary heading/editor search and README direct links; verify findability manually.
- [ ] Monitored community: N/A public channel; admin/reviewer roles specified, actual handoff unobserved.

### DX Implementation Tasks
- [ ] **T1 (P1, human ~2h / assisted ~30min)** — documentation — Add exact three-step rehearsal, worked synthetic example, field/config choices and validator example/status/exit interpretation in planned docs/quality-pilot-runbook.md and docs/quality-pilot-worksheet.md; link/correct README. Surfaced X1–X5 and Passes 1–4; combine with CEO T1/Design T1. Verify safe copied paths, links, all status examples and a fresh-reader exact clock without claiming audit accuracy.
- [ ] **T2 (P1, human ~1h / assisted ~20min, plus observation availability)** — operator verification — Map safe problem/cause/action/role links, preserve attempt/version identities and record the documentation rehearsal outcome/assistance. Same runbook/worksheet; combines existing retained UI verification where relevant. Verify session/chat/validator/interruption/missing-evidence branches and separate full-install/runtime clocks. No new service or automated process.
Unresolved policy decisions: none. External mockup permission remains optional and pending, outside DX scope; live tests, independent labels and observed timings remain blocked/unexecuted prerequisites.

<!-- autoplan-accepted:dx -->
- Add an exact three-step documentation rehearsal from README to the planned runbook and self-contained SYNTHETIC/not-pilot-result worksheet/source example. Prerequisites are a checked-out repository and ordinary viewers. Start when the maintainer opens README; finish only when they correctly explain a supported source match and an unresolved example with limitations. Target <=5 minutes includes reading/navigation; current time unknown. Keep full installation, real audit and engineering adjudication clocks separate. Record elapsed time, completion, assistance and confusion in the existing worksheet using a fresh reader; no telemetry, automatic release gate or recurring process is selected.
- Document the existing validator with resolved installed script/Python prerequisites, approved copied run-directory path and --out coverage_result.json, expected output/report and exit meaning. It writes only to the approved copy: exit 2 means BLOCKED; exit 0 can still mean PARTIAL_WITH_OPEN_ITEMS_REVIEW_REQUIRED. READY_FOR_ENGINEER_REVIEW is METADATA_ONLY, requires human signoff and never proves engineering accuracy or publication permission. Preserve original and failed artifacts; no production execution authorized.
- Add a compact safe troubleshooting table with actual problem, likely cause, corrective action, responsible role and runbook section link for replaced session, concurrent/saved pending chat, quota/capacity, stale project, generic gateway failure, interrupted audit, missing CLI/skill/evidence and structurally blocked validation. Customer errors remain generic; internal paths/logs/diagnostics are admin-only and redacted. Inventory actual UI behavior and scope the smallest separately reviewed repair for any observed acceptance gap.
- Explain existing artifact/package/revision/scope/tolerance/runtime-version and configuration choices, owners, permitted alternatives and their effects; do not invent a new configuration system, default engineering tolerances, configurable readiness gates or new API. Preserve corpus/adjudication/attempt/runtime/skill/model identities and versions, changed-scope comparability limits and existing recovery documentation. Runbook separates tutorial, field/formula reference, prerequisites, structural checks, troubleshooting and limitations, directly linked from README; verify links/findability/examples during the planned rehearsal.
<!-- /autoplan-accepted:dx -->

### Engineering phase binding
Target fixed: FEATURE_PLAN.md, current quality-first Implementation plan. Full engineering methodology read 1–600, 601–1200, 1201–1800, 1801–2208 through EOF without truncation. Autoplan overrides target questions, already-completed setup, standalone outside fallback/chaining/terminal reports; final workflow gate owns aggregate report. No app implementation or tests executed. Approved design already exists; office-hours is complete and is not rerun.

### Engineering Step 0 — scope challenge and preparation
Scope accepted as-is, FULL_REVIEW. Selected work: two new documentation files (worksheet/runbook), README correction/link and existing plan tracking, estimated four changed files, zero new classes/services. Existing feature files are inspection/verification targets, not implementation changes. Complexity selector does not trigger (<8 files, <2 classes). Scope reductions prohibited by autoplan; none needed. [Layer 1] reuse existing reports, ledgers, validator, ownership/session enforcement and run records, plus ordinary files/manual expert review. No new architecture/infrastructure/concurrency pattern is introduced, so no new-pattern web search is applicable. Distribution is checked-in Markdown opened by ordinary viewers; GCE/Compose app distribution remains unchanged, with no new build/publish pipeline. Future docs paths are not available yet; their absent history is not a defect. git history shows e50f00c feature addition and earlier audit/security work, no matching revert. Prior learning applied: audit-completed-is-not-quality-verified (9/10, 2026-10-06). TODOS P6 is the sole prior deferral; no mandatory pilot work is deferred.
Reuse mapping: corpus/scoring/observer material → installed BOQ ledgers plus planned worksheet; operator procedure/version/recovery → README/FEATURE_ROLLOUT plus planned runbook; session/timing/chat → existing APIs/components/tests. Missing independent corpus and live boundary proof remain execution dependencies, not choices to weaken. Scope Challenge findings: no scope changes required; dispositions: scope accepted, current contracts retained under U2/U3/C1–C8/D1–D6/X1–X5.

### Engineering native voice and decisions
Native autoplan_eng_voice completed INPUT eng 31a983c8f284c1ad173c4f6b5c74c168a5bd509dc4395840e235027ba256239d, three findings (one high, two medium). Outside preflight enabled/not_installed; Claude Code unavailable, no substitute outside credit. Native findings are independently checked below and each accepted mechanically as required proof of approved preservation/copied-artifact contracts, not application repair or production authorization.
| ID | Phase / finding | Classification / principle | Decision and reason | Rejected alternative |
|---|---|---|---|---|
| E1 | Eng / retry erases artifacts | mechanical P1/P5 | Require verified immutable uniquely named attempt snapshot before reuse, or separate job per attempt; runbook satisfies existing preservation contract without redesign | Rely on AuditRun timing rows for artifact history |
| E2 | Eng / abnormal validator failures | mechanical P1 | Treat crash/timeout/missing or malformed fresh result as validation unavailable/blocked; record script/Python identity; stale result cannot pass | Read an earlier result or infer success from exit alone |
| E3 | Eng / links escape copied artifacts | mechanical P1/P5 | Fresh ordinary-file copy only, reject links/special files, verify resolved containment before validator execution | Preserve generated links or trust a filename alone |
| Dimension | Native | Outside | Consensus |
|---|---|---|---|
| Architecture | File pilot appropriate; E1/E3 prerequisites missing | unavailable | N/A |
| Tests | Three targeted demonstrations needed | unavailable | N/A |
| Performance | No new online path; bounded pilot | unavailable | N/A |
| Security | Live boundary required; safe-copy preflight needed | unavailable | N/A |
| Errors | Abnormal/stale validator outcomes need handling | unavailable | N/A |
| Deployment | No deployment selected; actual boundary unknown | unavailable | N/A |

### Engineering Section 1 — Architecture
[P1] (confidence 10/10, source-verified behavior, no retry executed) backend/app/api/jobs.py:230–232: `shutil.rmtree(output_dir)` and `job.outputs.clear()`; audit_runner.py:143–144 writes fixed `codex-run.jsonl` / `codex-run.stderr.log`. FEATURE_PLAN.md:118 requires “Repeats and changed prompts are separate attempts, never replacements for the baseline.” Existing timing history cannot enforce artifact preservation. E1 requires a complete hashed snapshot with owner/completeness before retry/rerun that reuses its workspace, or a distinct job for each attempt. Copy only approved reports/ledgers/manifest/provenance and permitted redacted logs; missing evidence remains unavailable, never manufactured. Demonstrate baseline hashes/status remain unchanged after synthetic replacement. Disposition: E1 accepted auto P1/P5, documentation only.
[P1] (confidence 9/10, source reasoning, no exploit executed) installed validate_run.py:22–23: `p=dir_/f'{stem}.json'` and `p.read_text(encoding='utf8')`; :186: `dest=Path(a.workdir)/a.out;dest.write_text(...)`. Containment checks on deliverables at :173–175 do not cover ledger reads/output links. The approved copied-directory contract needs E3: fresh destination, approved ordinary files only, reject symlinks/special files (and files whose identity/containment cannot be established), resolved containment check, no linked output destination; never execute manifest-provided commands. Do this before invoking a trusted pinned script in an approved environment. Disposition: E3 accepted auto P1/P5; no validator/library rewrite.
Dependency graph:
```
Existing portal/worker (unchanged)              Human reviewer (independent)
   | reports + ledgers + status/attempt                  | hidden reference vN
   v                                                     v
Admin-approved immutable attempt snapshot         observer-only answer key
   | fresh ordinary-file contained copy                  |
   v                                                     |
Trusted installed validator -> fresh structural result    |
   | BLOCKED/unavailable/partial/metadata-ready            |
   +----------> NEW worksheet <---------------------------+
                   | evidence + counts/N/A + limitations
NEW runbook ------>| safe procedure / source examples
README link ------>| participant report + neutral tasks
                   v
         Architect observation -> admin scoped decision
                   (no production write-back/deploy)
```
Boundary: labels/key never agent inputs or initial participant material. Coupling is file input only; no synchronous request path, DB schema or API dependency added. Single admin/reviewer availability is an explicit human bottleneck. Each artifact path has failure handling below. Existing backend and separate chat gateway have different mount/permission boundaries; chat tests do not prove audit isolation. No audit source inline diagram change is selected; diagrams belong in the runbook.

### Engineering Section 2 — Code Quality
[P2] (confidence 8/10, source-reviewed risk, no malformed ledger executed) validate_run.py:49–51: `ident=r.get(k) if isinstance(r,dict) else None`, `if not ident or ident in seen`, `seen.add(ident)`; :185 calls `result=audit(a.workdir)` without top-level error conversion before report write. Nested unhashable IDs or filesystem failure can bypass normal 0/2/result behavior. FEATURE_PLAN.md:181's normal status instructions need E2 abnormal handling: unavailable Python/script/unreadable copy/timeout/unexpected exit or malformed/missing fresh result => unavailable/blocked. Preserve redacted stderr privately; unique fresh validation destination prevents accepting stale results. Record Python version and script digest. Disposition: E2 auto-accepted P1 completeness, no third-party script fix.
No runtime code is added, so no verified pair of new equivalent callers or useful shared extraction exists. Keep corpus/scoring/observer rules once in worksheet reference and link from runbook; do not duplicate formulas or introduce a scoring helper. Naming uses existing package/revision/job/attempt IDs; statuses remain distinct from pilot judgment. README current automatic/manual contradiction is already C5, not a new issue. Suppressed findings: none; inferred deployment vulnerabilities are verification risks rather than demonstrated exploits.

### Engineering Section 3 — Test Review (full trace, no execution)
Framework: backend/requirements-dev.txt pins pytest 8.3.5; inspected backend/tests use pytest fixtures/ASGI clients. Frontend package scripts include typecheck/build/lint, no browser suite found. Injected AGENTS has no Testing section. Reuse existing pytest and manual browser/document observation; no new framework or production testing seam selected. Dedicated full source/test reads completed before diagram: jobs API/runner/validator; auth/deps/chat/project_chat/migrations; test_auth/test_jobs/test_timing_chat/test_chat_gateway; gateway/Compose/JobTiming/ProjectChat. Existing tests below are source coverage, not current passing results. Future worksheet/runbook do not exist, so proposed manual checks have no existing implementation coverage.

Code/data/UX trace:
```
NEW documentation / pilot paths (manual unless ->EVAL)
README -> runbook prereqs
 | missing viewer/link/path -> visible blocked + owner/action [N1]
 | ready -> SYNTHETIC source + worksheet -> supported/unresolved explanation [N1]
Approved package -> revisions/hashes/discipline/scope [N2]
 | missing approval/reviewer/reference -> unavailable, never clean
 | disagreement -> exclude with reason; hide keys from agent/participant
 | complete -> freeze 3 packages (first rehearsal alone insufficient)
Execution choice [N3]
 | boundary evidence missing -> live BLOCKED -> approved offline artifacts
 | boundary proven + approved inputs -> baseline run [->EVAL]
 | offline provenance missing -> unknown, no live/isolation claim
Attempt collection [N4]
 | snapshot missing/incomplete -> preserve unavailable; no workspace reuse
 | snapshot complete/hashed -> separate job or preserve-before-retry
 | repeated/changed prompt -> new attempt, never replace baseline
Safe copy [N5]
 | symlink/special/escaping or unverifiable identity -> reject before validator
 | ordinary/contained/fresh -> trusted script only, no manifest commands
Validator [N6]
 | absent script/Python/unreadable/timeout/crash/non-0-or-2 -> unavailable/blocked
 | stale/missing/malformed result -> unavailable/blocked
 | 2 + fresh BLOCKED -> blocked
 | 0 + fresh PARTIAL -> incomplete, cannot earn readiness
 | 0 + fresh READY -> METADATA_ONLY, human review still required
Adjudication [N7,N8; ->EVAL]
 | report duplicates -> one root issue; uncertain match -> human review
 | supported/false alarm/unresolved/out-of-scope -> distinct raw counts
 | novel finding -> independent decision + new labels, retain baseline comparison
 | denominator 0 -> N/A; critical absent -> unassessed
 | narrative unsupported all-clear/omitted limitation -> readiness fails
 | synthetic vs real -> separate coverage, never pool claims
Report/worksheet [N9]
 | source absent/incomplete -> explicitly unresolved/blocked locator
 | complete locator -> independent page/sheet/row/object/calculation lookup
 | zero/partial/failed/offline -> visible status + limitations + owner/action
Observation/admin decision [N10]
 | empty -> inconclusive
 | 1..4 findings -> all; >=5 -> five with available diversity
 | any assistance/misinterpretation -> task failed; keep limited, repair/reassess
 | all independent tasks pass + all package gates -> admin scoped decision
 | observation key revealed early -> invalid observation, repeat blinded

RETAINED existing paths (no app edit selected)
Auth login -> limits/password -> active-session upsert [R1]
  failed login retains old; replacement invalidates API/files; admin unaffected
Timing claim -> actual worker -> success/failure/finally -> summed attempts [R2]
Startup recovery/reopen -> incomplete timing / terminal display [R3]
Job owned lookup -> completed-only outputs -> download/view/ranges [R4]
Chat request -> auth/owner/validation -> disabled/quota/lock/pending [R5]
  save pending -> bounded history -> gateway -> success/failed saved transcript
Slow reply -> recheck session/context -> reject stale answer [R6]
Gateway request -> auth/body/schema -> session-bound thread -> protocol [R7]
  profile/tool rejection/timeout -> generic error + terminate/kill process
Context -> status only or bounded approved excerpts -> evidence limits [R8]
Portal interaction [R9; ->E2E]
  expand/empty/load failure; submit whitespace/max/rapid; navigate/refresh
  slow pending/offline/500; stale two tabs; generic errors; retry/status labels
  PDF/download alternatives; viewport/zoom/keyboard/screen-reader
Deployed boundaries [R10; ->E2E]
  audit cross-job/credential/config read and writes + chat tool/network denial
  denial must be runtime-enforced, never inferred from mocks/prompt
```

Branch/flow coverage mapping (each row is a scenario group, not a line-coverage claim):
| ID | Test type / inspected coverage | Gap / specific assertions and disposition |
|---|---|---|
| N1 | Manual docs, no existing test | GAP: exact start/end/<=5 target, links, prereq failure, supported+unresolved explanation and assistance; approved X1–X3 |
| N2 | Manual corpus/reference inspection, no existing test | GAP: >=3 categories, hidden labels, unknown/disagreement exclusions, frozen scope/tolerances/revisions; U2/C1 |
| N3 | Approved isolation probe + offline record, no existing proof | GAP: no live run absent enforced boundary; offline provenance explicit; C3, no probe executed |
| N4 | Manual synthetic replacement/hash check, no existing artifact test | GAP: snapshot completeness, original hashes/status immutable after copied workspace replacement; E1 |
| N5 | Manual synthetic link/special-file/containment preflight, no existing test | GAP: reject before invoking validator, reject linked output, no artifact commands; E3 |
| N6 | Manual validator outcome matrix, no existing local assertion | GAP: fresh normal statuses plus unexpected exit/malformed nested ledger/stale result/write failure/timeout unavailable; E2/X3 |
| N7 | Manual worked scoring + independent real eval | GAP: dedup/match, tolerance, novel version, unresolved/out-of-scope counts, precision/recall/N/A/zero critical; U2/C6 |
| N8 | Human narrative/required-check eval | GAP: zero issue rows cannot bypass unsupported-all-clear/omitted-limitations; synthetic separated; U2 |
| N9 | Manual worksheet states/source lookup | GAP: all D1–D3 visible states, usable/incomplete locator, owner/action, word labels and readable hierarchy |
| N10 | Blinded architect observation + scoped admin review | GAP: empty, five-or-all-fewer, no-coaching, every-task-pass, task failure/repair/reassessment, retained baseline; U2/D4/C7–C8 |
| R1 | ★★★ test_auth:57,76,88,101 + tests/job access | Existing assertions: failed-login retention, second/concurrent login one usable, same-cookie tabs, admin and password revoke, account/IP rate; browser explanation still R9 |
| R2 | ★★★ test_timing_chat:13,45 | Existing fake-clock success/failure/retry monotonic totals; source not proof of artifact persistence N4 |
| R3 | ★★★ test_timing_chat:59,83 | Existing restart migration idempotence/interruption/pending FAILED/reopen total split; old-record display belongs R9 |
| R4 | ★★★ test_jobs:18,118,184,227,271,296 | Existing owner/cross-owner, draft/completed/reopened/ranged download, missing output/upload/optional-input validation |
| R5 | ★★★ test_timing_chat:99,135 | Existing blank/extra fields, ownership/history/private IDs, disabled/failure/busy/quota, failed question persisted |
| R6 | ★★★ test_timing_chat:180,197 | Existing session replacement during wait and report reopening discard; inspect actual concurrent navigation in R9 |
| R7 | ★★★ test_chat_gateway:14,63,95,104 (individual-file lines) | Config/protocol fake process/resume/tool reject/session binding/untrusted fields/auth/body/generic failure; no real CLI permission/timeout kill proof |
| R8 | ★★ test_timing_chat:160 + context tests:99 | Summary sanitization/private notes withheld/unavailable evidence/context version; actual PDF bound/extraction/remaining-pages disclosure needs R9/R10 check |
| R9 | Browser/manual integration, no suite found | GAP: retained UI user/error/empty/rapid/stale/navigation/refresh/offline/large transcript behaviors, source-to-rendered evidence limits/timing and D6 accessibility |
| R10 | Runtime integration, mocks inadequate | GAP: actual approved test account boundary, gateway denial/protocol/timeout cleanup and audit per-job/credential isolation; no production probe authorized here |
Source coverage: 8/20 scenario groups have meaningful existing assertions; 12/20 have planned verification gaps. This count is not executed coverage or line/branch percentage. No smoke-only check earns coverage. R7 timeout/protocol gaps remain explicitly R10; partial R8 coverage is not a full proof.

Test decisions: carry forward all twelve gap groups as required proof of exact approved contracts; E1–E3 define necessary demonstrations. Reuse docs/quality-pilot-worksheet.md and docs/quality-pilot-runbook.md as manual assertion/record locations, existing backend/tests/test_auth.py, test_jobs.py, test_timing_chat.py, test_chat_gateway.py for retained regression execution. Extend these only if later separately scoped app repairs need a credible regression; no test file is implemented now. No prompt/LLM/tool-definition change selected; eval still covers the existing BOQ workflow on the frozen errorful/scoped-clean/ambiguous packages, discipline-applicable regression patterns, explicit critical and uncertainty coverage and original baselines. Exact synthetic cases are worked checks, never model-accuracy evidence. Tests made obsolete: none. No unapproved optional test-policy choice introduced, so no regression menu is reopened. All checks remain unexecuted.

Engineering test plan persisted at /home/thangpham/.gstack/projects/minhthang294-BOQ-Audit/thangpham-main-eng-review-test-plan-20261007-082656.md. Contains affected pages/routes, interactions, all critical/edge value cards, existing suite/eval mapping, no retired tests and no pending test-policy decision. Reviewed complete saved artifact; all checks are future work.

### Engineering Section 4 — Performance
No new online path/query/cache/process is introduced. Manual pilot is three packages, five-or-all-fewer observation tasks and file-based records; actual file sizes, model time, reviewer/admin effort and memory remain unknown. Existing list_jobs loads all user jobs with selectinload (no query-per-row in this path), and AuditRun display/relationship growth can scale with retained attempts; at tenfold usage query/response size needs measurement before pagination/cache work. Existing runner recursively scans job files and capture_output buffers subprocess output, while gateway context/history/body/response and timeout are bounded in source. Whole-file/PDF parsing and model execution remain slow-path risks, not measured regressions introduced by this documentation plan. Avoid re-reading raw reports in the worksheet; cite original source. Preserve all evidence, do not solve storage limits by deleting baseline attempts. Existing global chat lock/single-instance SQLite and in-process audit worker limit throughput; no new queue/cache/concurrency mechanism is justified for this pilot. Performance findings requiring new work: none. Dispositions: retain limitations and record actual runtimes/effort under C8, no benchmark or scale claim.

### Engineering failure modes registry
| Path | Realistic failure | Coverage / handling | User-visible outcome / critical gap |
|---|---|---|---|
| README/rehearsal | missing viewer/link/example | N1 manual; prerequisite/table | visible blocked; no silent success |
| corpus/reference | wrong revision/no reviewer/disagreement | N2 freeze/hide/exclude | unavailable reference, explicit scope |
| runtime/offline | cwd mistaken for enforced boundary | N3/R10 mandatory proof; offline fallback | live BLOCKED; critical prerequisite unknown, not waived |
| attempt snapshot | retry erases baseline or log | E1/N4 immutable copy/hash/owner before reuse | incomplete snapshot unavailable; no pass |
| copied artifacts | ledger/output link escapes copy | E3/N5 reject preflight | blocked before validator; no blind script invocation |
| validator | crash/timeout/stale/malformed output | E2/N6 fresh result matrix | validation unavailable/blocked, redacted admin cause/action |
| matching/scoring | duplicates or changed labels inflate scores | N7 root issue/version/denominator checks | counts/N/A and original comparison visible |
| narrative | zero rows falsely all-clear | N8 required-check adjudication | readiness fails with reason |
| reports/states | missing citation/limitation | N9 D1–D3 state/examples | unresolved source and next action visible |
| observation | answer leakage/coaching/empty exercise | N10 D4 blinded record | invalid/failed/inconclusive, limited pilot |
| retained auth/timing/chat | stale session/history/failed reply | R1–R8 tests exist; R9 rendered and R10 runtime outstanding | source labels/recovery mapped, actual behavior unverified |
Critical silent-gap definition (no handling AND no test AND silent): 0 in the amended plan. Planned checks are not executed coverage; live isolation is an unresolved critical execution prerequisite, and quality/readiness remains unestablished.

### Engineering NOT in scope / reuse / deferred TODOs
NOT in scope: new UI evidence screen deferred P6 until observed friction; audit sandbox redesign or runtime/validator repairs separately scoped if verification fails; public SDK/docs portal, scoring service, queue/cache, model/prompt changes and dependency upgrades have no measured need. Reuse: existing upload/download/ownership, customer active session, AuditRun/recovery, Vietnamese report outputs, BOQ ledgers/validator, gateway and backend pytest sources. No shared abstraction/extraction accepted. Existing README/rollout are references, not proof of deployed identity/safety. TODOS.md carries exactly one prior P2 screen proposal with context/effort/benefits/cost/dependency; no required check was deferred. Sequential implementation, no parallelization opportunity: worksheet/runbook share contracts, then README link, then human/runtime-dependent verification. No worktree agents are planned for implementation.

### Engineering Implementation Tasks
- [ ] **T1 (P1, human ~1h / assisted ~20min)** — attempt preservation — In planned runbook/worksheet require full uniquely named approved snapshots and owner/hash/completeness before workspace reuse, or separate job per attempt. Surfaced E1; demonstrate copied synthetic replacement preserves baseline hashes/status. Combine CEO T1/T2.
- [ ] **T2 (P1, human ~1h / assisted ~20min)** — structural verification — Document fresh result ownership, script/Python identities, normal and abnormal exit/result handling; preserve redacted diagnostics and fail closed for malformed/stale/missing/timeout. Surfaced E2; synthetic examples include malformed nested ledger and stale prior result. Combine DX T1.
- [ ] **T3 (P1, human ~1h / assisted ~20min)** — safe copy — Document fresh ordinary-file contained copy, links/special-file/identity/output checks, no manifest commands. Surfaced E3; reject outside-pointing synthetic link before validator execution. Combine CEO T2/DX T1. No product/script changes authorized.
Effort estimates assume documentation plus synthetic manual proof; human approvals, independent labeling, runtime work and observation elapsed time excluded.

### Engineering approval readiness and completion
Approval readiness: PASS for current planning decisions U1–U3/C1–C8/D1–D6/X1–X5/E1–E3 under user approved quality-first design and autoplan's authorized mechanical decisions. No implementation or production approval inferred. Step 0 scope accepted as-is FULL_REVIEW. Architecture 2 issues (E1/E3); code quality 1 (E2); test diagram produced with 12 verification-gap groups, existing assertions in 8 groups; performance 0. Four-section issues_found=15 (includes mapped future checks, not fifteen observed bugs). NOT in scope/reuse written; TODOS one prior accepted deferral, 0 new proposals. Failure registry 0 unhandled silent critical gaps; live isolation prerequisite unknown/blocked. Unresolved substantive choices in this review 0; external mockup question remains optional/pending from Design. Outside unavailable (Claude Code not installed); no cross-model consensus. Parallelization 0 lanes / 0 parallel / sequential docs then dependent checks. Lake Score N/A (no coverage-scored human choices in this review). Required test/task artifacts persisted; no tests/evals/live/visual checks executed. Durable insight: retry output/log replacement needs attempt snapshots independently of AuditRun timing history; preserve in project-local planning record, no cross-project learning write.

<!-- autoplan-accepted:eng -->
- Before any evaluation retry/rerun that reuses a job workspace, require a complete uniquely named immutable approved attempt snapshot with owner, provenance, file hashes, reports/ledgers/manifest, status, validator result and permitted redacted logs; or use a separate job per attempt. Existing retry deletes outputs and fixed worker logs are overwritten, so timing history alone is insufficient. Incomplete/missing snapshots remain unavailable evidence, never silently reconstructed. Demonstrate a copied synthetic baseline's hashes and failed/partial classification survive separately simulated replacement; preserve every baseline/comparison and original artifacts.
- Require a trusted pinned validator script, actual Python version and fresh result location for each approved copied attempt. Unexpected exit (outside normal 0/2), absent Python/script, unreadable files, timeout/crash/write failure or missing/malformed fresh JSON is structural validation unavailable/blocked; retain redacted diagnostics privately and never reuse stale results or manufacture a validator verdict. Document and demonstrate malformed nested-ledger and stale pre-existing result cases. A valid normal metadata result still needs all existing human evidence/narrative/readiness gates.
- Before copied-artifact validation, create a fresh approved destination containing ordinary files only; reject symlinks, special files and files whose identity/containment cannot be established, verify all resolved read/write paths stay within the copy, and ensure the output destination is not linked or stale. Retain originals separately and never execute commands supplied by artifacts/manifests. Demonstrate rejection of a synthetic outside-pointing link before invoking the validator. These are runbook copy prerequisites, not a new service or application rewrite.
- Carry forward the complete planning test map and saved engineering test plan: corpus/reference/scope/hidden-label checks; runtime-proof-or-offline choice; attempt snapshot/copy/validator failure demonstrations; independent matching/dedup/tolerance/version/count/N/A/zero-critical checks; narrative/required-limitations and synthetic-real separation; all visible citation/state examples; nonempty blinded five-or-all-fewer independent observation and scoped admin decision; retained auth/timing/chat regression, rendered accessibility and deployed boundary/protocol/cleanup checks. Reuse existing pytest and manual records, retire no tests, add no framework. All tests, model evals and live checks remain planned rather than executed; missing proof keeps the relevant gate unpassed.
<!-- /autoplan-accepted:eng -->


### Final pre-gate verification / canonical decision audit
Applicable phase outputs were semantically reconciled against the approved design and full current implementation packets. CEO: all eleven sections, registries, scope/reuse/dream delta/summary and native consensus table present. Design: six scored passes plus seventh unscored decision pass, seven-item litmus table, all issue decisions, source/visual limitations present. DX: eight scored dimensions, six-stage journey, empathy, benchmark, exact clock/target/checklist and consensus present. Eng: code-grounded scope, architecture/coverage diagrams, full test plan on disk, failure registry/summary/reuse/TODO and consensus present. Every native phase completed against its bound input; outside preflights unavailable, no external completion credit. No pending native task. Mandatory planning outputs complete; external mockup generation unavailable and no images approved, actual execution evidence remains absent. No affected-phase rerun is triggered by this report-only aggregation.
There are 23 review decisions: 22 mechanical auto-decisions and one provisional taste choice C2. Prior user decisions U1/U2 govern context/direction and are not counted again. U3 and C1–C8/D1–D6/X1–X5/E1–E3 are the 23 distinct IDs. No User Challenge exists. All substantive constraints remain intact; optional mockup permission and final gate remain unanswered. Aggregation normalized short task commit identifiers to actual full HEAD so the skill's exact recent-commit filter includes this run; this is metadata correction only.
| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| U3 | CEO | Retain original feature contracts | mechanical | P4 | Already implemented source still needs verification | Rebuild or omit |
| C1 | CEO | Two-file worksheet/runbook pilot | mechanical | P5 | Existing reports/ledgers suffice | Scoring service |
| C2 | CEO | Vietnamese cover in worksheet | taste, provisional | P1/P5 | Scope and limits visible before findings | Linked standalone cover, extra file/navigation |
| C3 | CEO | Verify boundary or limited offline assessment | mechanical | P1 | Prompt/cwd cannot prove isolation | Assume live safety |
| C4 | CEO | Retain auth/timing/chat acceptance checks | mechanical | P1 | Existing source is not passing evidence | Omit checks |
| C5 | CEO | Correct README automatic/manual wording | mechanical | P3 | Current documentation contradicts worker | Leave stale intro |
| C6 | CEO | Coverage inventory; zero critical unassessed | mechanical | P1 | Avoid vacuous critical pass | Treat absent critical as pass |
| C7 | CEO | First rehearsal then complete three packages | mechanical | P5 | Sequencing preserves corpus gate | One package earns readiness |
| C8 | CEO | Record descriptive effort and next action | mechanical | P1 | Make pilot value observable | Claim general savings |
| D1 | Design | Separate execution/evidence/assessment labels | mechanical | P1/P5 | Completion is not approval | Implicit prose only |
| D2 | Design | Complete explicit source locators | mechanical | P1 | Independent source lookup | Filename/page alone |
| D3 | Design | Visible empty/blocked/partial states | mechanical | P1 | Blank cannot imply success | Blank assessment |
| D4 | Design | Separate participant material and answer key | mechanical | P1 | Avoid coached measurement | Shared revealed key |
| D5 | Design | Inventory existing UI state/copy | mechanical | P4 | Preserve primary report workflow | Redesign based on assumptions |
| D6 | Design | Explicit responsive/accessibility proof | mechanical | P1 | Source intent is not rendered proof | Assume accessibility |
| X1 | DX | <=5-minute exact documentation rehearsal target | mechanical | P5/P6 | Defined useful result and start state | Relabel warm audit time |
| X2 | DX | Existing file worked example | mechanical | P5 | Useful rehearsal without credentials | Hosted playground |
| X3 | DX | Exact entry point/prerequisites/commands/outcomes | mechanical | P1/P5 | Fresh maintainer can follow procedure | Vague instructions |
| X4 | DX | Safe recovery table with role/action/links | mechanical | P1 | Errors support recovery without disclosure | Raw logs to customer |
| X5 | DX | Document existing choices/versions | mechanical | P5 | Discoverability without config service | New config system |
| E1 | Eng | Verified snapshot before workspace reuse | mechanical | P1/P5 | Retry deletes outputs/overwrites logs | Timing rows as artifact history |
| E2 | Eng | Fresh validator result or unavailable/blocked | mechanical | P1 | Crash/stale results cannot pass | Infer from normal exits alone |
| E3 | Eng | Fresh ordinary-file contained copy preflight | mechanical | P1/P5 | Links escape naive copied boundary | Preserve untrusted links |

### Cross-phase themes
- CEO/Design/DX/Eng independently separate process completion, structural checks, supported evidence and human readiness; no score triggers deployment.
- CEO/Design/Eng require source locators and blinded human verification; DX supplies a simple worked example/recovery path.
- CEO/DX/Eng require provenance and preserved failures; Eng adds concrete retry snapshots and safe fresh validator results.
- CEO/Eng retain runtime isolation as a live prerequisite; source mocks and existing deployment do not establish it.

### Final implementation order and possible duplicates
The aggregator keeps exact nonmatches separately. CEO T1, Design T1, DX T1/T2 and Eng T1–T3 overlap in the same two documentation files and shall be implemented together sequentially, not counted as separate builds or summed as independent estimates. CEO T4 and Design T2 overlap in retained verification. CEO T2 is human/runtime prerequisites; CEO T3 depends on those prerequisites and the documentation, then full evaluation/observation. File lists on verification tasks name inspection targets, not approved product edits. Empty file lists on corpus/evaluation tasks refer to admin-approved private storage whose location is not known. No extra workstream/service introduced.

### Implementation Tasks (aggregated across phases)
- [ ] **T3 (P1, human: unknown / CC: 1h setup) — evaluation** — Rehearse then evaluate full corpus and observe architect
  - Surfaced by: ceo-review — Unmeasured quality and human verification
  - Files:
- [ ] **T4 (P1, human: 2h / CC: 30min) — existing contracts** — Verify existing auth timing chat acceptance
  - Surfaced by: ceo-review — Historical requirements remain unverified
  - Files: backend/tests/test_auth.py, backend/tests/test_timing_chat.py, backend/tests/test_chat_gateway.py
- [ ] **T1 (P1, human: 2h / CC: 40min) — pilot docs** — Prepare worksheet and runbook
  - Surfaced by: ceo-review — C1-C3 C6-C8: missing operational pilot fields
  - Files: docs/quality-pilot-worksheet.md, docs/quality-pilot-runbook.md, README.md
- [ ] **T2 (P1, human: unknown / CC: 30min) — prerequisites** — Obtain corpus and prove runtime boundary
  - Surfaced by: ceo-review — Unverified live isolation and unavailable independent corpus
  - Files:
- [ ] **T1 (P1, human: 1h / CC: 20min) — pilot design** — Specify worksheet hierarchy states citations and observation separation
  - Surfaced by: design-review — D1-D4 independent design findings
  - Files: docs/quality-pilot-worksheet.md, docs/quality-pilot-runbook.md
- [ ] **T2 (P1, human: 2h / CC: 30min) — pilot design** — Verify retained UI states accessibility and viewports
  - Surfaced by: design-review — D5-D6 existing UI verification lacks concrete outcomes
  - Files: docs/quality-pilot-runbook.md, frontend/components/ProjectChat.tsx, frontend/components/JobTiming.tsx, frontend/app/jobs/[job_code]/page.tsx
- [ ] **T1 (P1, human: 1h / CC: 20min) — pilot documentation** — Specify and demonstrate immutable attempt snapshots before retry
  - Surfaced by: eng-review — E1 architecture artifact preservation gap
  - Files: docs/quality-pilot-runbook.md, docs/quality-pilot-worksheet.md
- [ ] **T2 (P1, human: 1h / CC: 20min) — pilot documentation** — Specify fresh validator output and abnormal failure handling
  - Surfaced by: eng-review — E2 validator crash stale and missing result gap
  - Files: docs/quality-pilot-runbook.md, docs/quality-pilot-worksheet.md
- [ ] **T3 (P1, human: 1h / CC: 20min) — pilot documentation** — Specify safe ordinary-file artifact copy preflight
  - Surfaced by: eng-review — E3 ledger and output symlink boundary gap
  - Files: docs/quality-pilot-runbook.md, docs/quality-pilot-worksheet.md
- [ ] **T1 (P1, human: 2h / CC: 30min) — pilot documentation** — Document exact synthetic rehearsal fields and safe validator example
  - Surfaced by: devex-review — X1-X5; DX Passes 1-4
  - Files: docs/quality-pilot-runbook.md, docs/quality-pilot-worksheet.md, README.md
- [ ] **T2 (P1, human: 1h plus observation availability / CC: 20min) — pilot documentation** — Record safe recovery version evidence and fresh-reader rehearsal
  - Surfaced by: devex-review — X4-X5; DX Passes 5-8
  - Files: docs/quality-pilot-runbook.md, docs/quality-pilot-worksheet.md, README.md


### Final gate state
Review work: DONE_WITH_CONCERNS. Plan status: APPROVED by the user with final gate choice A on 2026-10-07; documentation implementation may proceed within the approved pilot scope. This does not approve a live audit, production change, deployment, private input access or external mockup transmission. Required review logs have been written after the final answer A; outside voices remain unavailable. Outside coverage missing in all four phases; native findings incorporated (CEO 3, Design 5, DX 3, Eng 3). Design score and DX score are plan-completeness judgments, not measured experience. No application code, deployment, model run, tests, production inspection or private credentials accessed. One user test remains unmeasured quality evidence. No new cross-project learning write or brain calibration performed.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|---|---|---|---|---|---|
| CEO Review | /autoplan → /plan-ceo-review | Scope and strategy | 1 | CLEAR (PLAN via /autoplan) | Spec 9/10; 5 proposals accepted, 1 deferred, 1 skipped; live boundary remains a required prerequisite |
| Outside Review | Claude Code preflight in each phase | Independent second opinion | 4 attempts; 0 completed | UNAVAILABLE | CLI not installed; CEO/design/DX/eng consensus N/A |
| Eng Review | /autoplan → /plan-eng-review | Architecture and tests | 1 | ISSUES OPEN (PLAN via /autoplan) | 3 source findings; 12 verification-gap groups; 15 four-section items, 0 unhandled silent critical gaps |
| Design Review | /autoplan → /plan-design-review | Evidence usability/accessibility | 1 | CLEAR (PLAN via /autoplan) | Plan completeness 5 → 8/10; 5 native findings incorporated; rendered proof remains required |
| DX Review | /autoplan → /plan-devex-review | Maintainer procedure | 1 | CLEAR (PLAN via /autoplan) | Plan completeness 5.5 → 8/10; 3 native findings incorporated; walkthrough remains unmeasured |

**OUTSIDE COVERAGE:** host codex, provider claude-code, status unavailable in CEO/design/DX/eng; native completed in all phases. No cross-model confirmation or known distinct-model claim.

**VERDICT:** User approved the reviewed plan as-is (A) on 2026-10-07. CEO/design/DX planning reviews are recorded clear; engineering review remains issues open because required verification tasks remain. Approval authorizes implementing the worksheet, runbook and README changes in scope. It does not establish BOQ accuracy, runtime isolation, rendered accessibility or deployment readiness.

**UNRESOLVED DECISIONS:**
- Optional external mockup brief transmission remains unapproved after automatic approval review rejection. No brief was sent and no mockup generated; the text review is complete and this optional permission does not block the approved documentation work.

## Implementation continuation — 2026-10-08

Current evidence: [local verification record](docs/quality-pilot-verification.md). Historical unchecked review tasks above describe the approved acceptance scope; the statuses below distinguish implemented documentation from unperformed evaluation.

- [x] Documentation portions of CEO T1, Design T1, DX T1/T2 and Eng T1–T3: runbook/worksheet and README entry point prepared; snapshot inventory, actual validator schema, blocked/stale-copy handling, coverage/observation rules and recovery fields reconciled. Eight synthetic assertion groups pass, including preserved failed baseline, stale result, symlink/special-file rejection and malformed validator input.
- [x] Frontend TypeScript check and production build pass locally.
- [x] CEO T4 local backend regression: targeted auth/timing/chat/gateway 24 tests pass; full existing backend suite 57 tests pass. This verifies mocked local contracts, not the deployed gateway/protocol/boundary.
- [ ] CEO T2/T3: approved corpus, independent references, live runtime boundary and real evaluation/architect observation remain pending.
- [ ] Design T2 and human portions of DX T2: rendered UI/accessibility and independent fresh-reader rehearsal remain pending.

No model run, live worker isolation test, production change or deployment performed. Documentation completion and synthetic checks do not establish engineering accuracy or rollout readiness.
