# Implementation roadmap

Phase 1 scope and dependencies were approved on 2026-11-25. Milestone 1.1 was merged in PR #1. Milestones 1.2 and 1.3 implement Greenhouse collection and SQLite persistence on their dedicated branches, pending PR review.

## Branches, reviews and CI/CD

The user authorized a direct commit and push of this initial baseline to `main`. Every later increment must use a dedicated branch and a PR to `main`. Start milestone 1.1 on `feat/phase-1-1-bootstrap`; keep each PR focused on a reviewable milestone and report its actual validation results.

Introduce CI in milestone 1.1 rather than waiting for the end of Phase 1. It must validate locked installation, Ruff lint/format, Pytest on Windows and Linux, and package building. Later milestones extend the same pipeline with their regression tests. In Phase 4, introduce delivery automation for Docker/release artifacts and the selected deployment target; deployment credentials and hosting are not assumed.

## Phase 1: deterministic MVP

Deliver a useful single-user CLI that collects at least one verified Greenhouse board, persists normalized postings and history, detects changes safely, and explains deterministic filtering. Keep the application executable after each implementation milestone. Do not implement all milestones in one iteration.

Dependency order: **1.1 -> 1.2 -> 1.3 -> 1.4 -> 1.5 -> 1.6**. Each milestone is independently reviewable and has a reproducible exit check.

### 1.1: executable package and configuration contracts

Implementation status: package, configuration CLI/models, examples, watchlist, lock, tests and CI workflow delivered on the dedicated branch. Local Ruff checks, 74 offline tests, source/wheel build and fresh-environment wheel verification pass on Windows/Python 3.12.14. Hosted CI results must be checked on the PR before declaring the milestone's cross-platform gate complete.

Deliver:

- `pyproject.toml`, a reproducible development lock, Python 3.12 baseline, `src/` package, and standard-library CLI entry point.
- Approved dependencies only: Pydantic, PyYAML, HTTPX, SQLAlchemy; Pytest and Ruff for development.
- Validated settings, source, and profile models; synthetic profile example; extensible company watchlist preserving all requested names/groups, with unverified sources disabled.
- `.gitignore` protecting `.env`, local profiles, CVs, databases, payloads, caches, logs, build output, and environments; `.env.example` with placeholders.
- README installation/help instructions that actually work on the tested environment.
- GitHub Actions for locked installation, Ruff lint/format checks, Pytest on Windows and Linux, and package-build validation, running on PRs to `main` and pushes to `main`.

Exit check: clean install from the lock; `--help` and `config validate` work with no API key or network. Unknown YAML fields, duplicate source keys, invalid dates/limits, unsafe YAML, and missing required configuration fail with actionable errors. Importing the package has no collection or database side effects. No candidate details appear in source code or fixtures. The first code PR passes the CI lint/format, test, locked-installation and package-build jobs.

### 1.2: Greenhouse collection and normalization

Implementation validation includes offline HTTP mocks and fixture replay. A one-request smoke check of Datadog's public Greenhouse board returned 391 normalized postings with a complete count. The shipped watchlist still requires explicit enablement and source review before recurring use. See the access notes in [ARCHITECTURE.md](ARCHITECTURE.md).

Deliver:

- Connector/snapshot protocol and offline fixture adapter.
- Shared HTTP policy with injected clock/sleeper, bounded retries and limits, redacted structured logs, and source failure isolation.
- Greenhouse adapter and Pydantic posting schema with explicitly unknown unsupported fields.
- Fixtures for ordinary, empty, malformed, encoded HTML, missing optional fields, duplicate source IDs, and count-mismatch responses.
- A CLI collection preview that works before persistence exists.

Exit check: mocked transport verifies URL/query construction, mapping, timeouts, 429/Retry-After, selected 5xx retry, non-retryable errors, response limits, and complete/partial/failed classification. No fabricated salary, seniority, publication date, eligibility, or remote policy. One failed board does not suppress a successful board. A manually enabled, opt-in smoke check records a verified official board and the source-access review; offline tests remain the default.

### 1.3: durable storage and deterministic identity

Implemented on `feat/phase-1-3-storage`, based on the pending 1.2 Greenhouse branch. The CLI supports optional persistence and paginated JSON listing. Tests cover restart/replay, exact source IDs, conservative URL aliases, collision records, immutable revisions, source rollback/retry, foreign keys and competing processes. Milestone 1.2 must be merged before reviewing the 1.3-only diff against main.

Local verification on 2026-11-27 passed 107 tests on Windows with CPython 3.12.14, Ruff checks, source/wheel builds and fresh-environment wheel installation. Tests ran in a separate ignored `.venv-storage` environment because Windows blocked the existing virtual-environment launcher. Hosted Windows/Linux CI remains to be checked on the PR.

Deliver:

- SQLAlchemy tables and initial schema version, transactional ingestion, run/source outcomes, and a tested exclusive-writer mechanism.
- Source identity resolution, conservative canonical URL aliases, versioned content fingerprints, immutable revisions, and first/last-seen observations.
- `collect` persistence plus `jobs list` for inspection.

Exit check: two processes run sequentially against the same temporary file database and preserve state across restarts. Replaying the same snapshot creates no duplicate jobs or revisions. Equal titles/descriptions with distinct source identities stay separate. Preserve identifying URL parameters. A -> B -> A changes create three revisions. An injected transaction failure rolls back source changes; retry succeeds. Concurrent collection is refused clearly without corruption. Newer unknown schema versions are rejected.

### 1.4: safe lifecycle and change events

Implemented on `feat/phase-1-4-lifecycle`. Schema migration 2 adds durable events and per-source presence with conservative reconciliation. The CLI exposes paginated change history and run-level change counts. Validation covers reopening without a content change, source failures, rollback, replay, scope/policy changes, depleted boards, disabled sources, multiple aliases, and migration of existing data. Hosted CI results are checked on the milestone PR.

Local verification on 2026-11-27 passed 120 tests, Ruff checks, source/wheel builds and isolated wheel installation using Python 3.12.14 on Windows.

Deliver:

- Field-level changes and idempotent events for new, updated, missing, returned, removed-from-source, and reopened observations.
- Complete-snapshot reconciliation scoped to the same source and coverage, with configurable grace and anomaly safeguards.
- `changes list` and run summaries distinguishing new discoveries from published dates and observed removal from employer-confirmed closure.

Exit check: controlled snapshots and a fake clock cover unchanged, title/description/deadline/pay changes, single omission, two complete omissions over the grace period, reappearance, HTTP failure, partial responses, anomalous empty results, disabled sources, changed scope, and aliases across multiple sources. Failed/partial runs reset the omission streak and never advance removal. No job is removed while another alias confirms presence. Replay produces zero duplicate events, including status-only reappearance.

### 1.5: conservative filters and usable CLI

Deliver:

- Configurable role aliases, ordered location preferences, explicit incompatible-title seniority rules, and an opt-in comparable-pay policy.
- `keep` / `review` / `exclude` decisions with evidence and configuration versions, persisted separately from postings.
- Table and stable JSON output; collection/run identifiers and per-source summaries; consistent documented exit codes.
- Refilter stored jobs after profile/preferences change without modifying source revisions.

Proposed exit codes: `0` for success, `1` for runtime/total collection failure, `2` for invalid invocation/configuration, `3` for partial source success. JSON schema includes an output version; machine output must not be mixed with logs.

Exit check: relevant unlabelled-junior roles and undisclosed pay remain discoverable. Explicit senior titles can be excluded with evidence. Ambiguous role/location and unknown work authorization remain reviewable. Base salary below the target alone does not exclude. Missing FX data prevents fabricated conversion. CLI JSON round-trips and contains official source links when available. Changing filter configuration changes decisions, not job/revision counts.

### 1.6: end-to-end validation and operational handoff

Deliver:

- Offline end-to-end tests spanning config -> connector -> normalization -> storage -> reconciliation -> filters -> CLI.
- Extend the GitHub Actions pipeline established in 1.1 with complete offline end-to-end and regression coverage; retain Windows and Linux checks for the CLI/database path, with no production secrets or live network requirement for tests.
- A small versioned, manually annotated baseline dataset (propose at least 12 synthetic descriptions: relevant, irrelevant, and ambiguous) and a transparent keyword-baseline report.
- Operating instructions for database location, collection/replay, failures, reset of disposable data, and backup/restore. Preserve data by default.
- Measured fixture workload: run latency, peak item count, duplicate events, and failure outcomes, with hardware/context recorded rather than invented service targets.

Exit check: all MVP acceptance criteria below pass; README commands run from a clean environment; one optional authorized live-source smoke run is documented separately. No LLM, OpenAI credentials, automatic applications, external messages, or hosted deployment are needed.

## MVP acceptance criteria

| ID | Required behavior | Verification |
| --- | --- | --- |
| A1 | Install and run CLI on the supported Python baseline | Clean locked install and CLI smoke test |
| A2 | Validate YAML preferences without personal constants in logic | Positive/negative configuration fixtures and source review |
| A3 | Collect at least one verified official Greenhouse board | Opt-in live smoke record plus mocked contract tests |
| A4 | Preserve unsupported data as unknown and retain attributable source evidence | Normalization fixture assertions |
| A5 | Persist across process restarts and replay idempotently | File-backed SQLite integration test |
| A6 | Resolve deterministic identities without merging different vacancies by text | Source-ID, canonical-query, and collision tests |
| A7 | Detect material changes including repeated historical content | Revision/field-diff and A -> B -> A tests |
| A8 | Reconcile absence only from complete, matching, validated source snapshots | Failure, partial, empty-anomaly, scope-change, and grace-period tests |
| A9 | Record reopening and avoid duplicate change events | Lifecycle replay tests; duplicate-event rate exactly zero on fixtures |
| A10 | Preserve ambiguous, unlabelled-junior, and unknown-pay opportunities | Annotated filter cases, with reason/evidence assertions |
| A11 | Isolate source failures and bound network work | Mixed-source integration tests and fake-clock budget tests |
| A12 | Produce useful terminal and versioned JSON output | CLI tests including exit codes and separate log stream |
| A13 | Keep secrets, private profiles, CVs, and runtime data out of tracked files | Ignore-rule checks and manual diff review |
| A14 | Pass required checks offline in CI | Ruff and Pytest on the agreed environment matrix |
| A15 | Publish reproducible baseline measurements and a working backup restore | Dataset/report review and temporary-database restore check |

Notification deduplication is not claimed complete in Phase 1: the acceptance gate covers durable change-event idempotency. Delivery deduplication is tested when notification providers exist. Likewise, Phase 1 provides no semantic matching score or comprehensive eligibility classification.

## Assumptions and validation register

| Question / assumption | Validation or default | Required by |
| --- | --- | --- |
| Approve the proposed Phase 1 dependency set and scope? | Approved by the user on 2026-11-25; record new significant dependency/scope decisions separately | Resolved |
| Python 3.12 works on the user's environment and dependency lock | Verified using uv 0.12.12 and managed CPython 3.12.14. Tool execution required access outside the sandbox; local tests use a project-local temporary directory to avoid shared Windows Temp permissions | Locally resolved; hosted CI pending |
| Which watchlist companies expose a usable Greenhouse board? | Verify an official careers link and token; keep unsupported entries disabled | 1.2 |
| Are intended source access and later commercial reuse permitted? | Review official source conditions; public API existence alone is insufficient | Enablement / Phase 6 |
| Does the response represent the whole requested scope? | Check documented response, count consistency, duplicates and real payload; conservatively classify uncertainty | 1.2/1.4 |
| Are 2 complete omissions over 24 hours suitable? | Proposed configurable default; calibrate against fixture and live observation behavior | 1.4 |
| Can raw evidence be retained locally, and for how long? | Ignore runtime data in Git; propose short raw-payload retention with explicit purge policy before sustained polling | 1.6 / Phase 4 |
| What exact graduation date, work rights, languages and apprenticeship rules apply? | Preserve supplied precision and unknown facts; ask only when a decision depends on them | Phase 2 |
| What fallback pay threshold and currency policy are desired? | Leave fallback disabled until configured; no assumed FX or benchmark access | Pay filtering / Phase 2 |
| Which optional model and inference environment are available? | Validate Ollama hardware/context limits or optional remote account; no remote transmission by default | Phase 2 |
| Where will periodic execution run? | Local manual runs in Phase 1; validate persistent host, disk, network, backup and scheduler before automation | Phase 4 |
| Which alert channels, digest times and timezone are desired? | Configure provider credentials privately; Europe/Paris is the initial scheduling proposal | Phase 4 |

These do not all block the initial package milestone. Keep unvalidated integrations disabled and unknown candidate facts explicit rather than guessing.

Planning validation: the four documentation files passed local-link, UTF-8 decoding, code-fence and whitespace checks using PowerShell. All six Phase 1 milestone headings and 15 acceptance criteria are present. Python-based checks could not start because the resolved `python.exe` was inaccessible; no application tests or live-board smoke tests have run.

Milestone 1.1 supersedes the initial Python limitation: the approved environment was installed with uv, and the local application checks above now pass. No live-board smoke test has run; that belongs to 1.2. No production credentials, private candidate files or runtime database are included.

## Phase 2: structured intelligence

Introduce LangChain provider adapters only after reviewing their dependencies. Separate milestones: (2.1) structured job extraction with mock responses and grounded evidence validation; (2.2) eligibility rules and interpretations; (2.3) nine dimension rubrics, deterministic aggregation, confidence and coverage; (2.4) compensation components/provenance and optional authorized benchmarks; (2.5) versioned analysis caching and token/cost budgets.

Gate: annotated relevant/irrelevant/ambiguous cases; hallucinated evidence rejected; local provider works with no OpenAI key; remote provider remains optional; malformed/model-unavailable/budget-exhausted paths retain deterministic behavior. Matching is reproducible for fixed dimension inputs and never described as hiring probability. CV enrichment is opt-in and cannot silently turn inferred facts into verified skills.

## Phase 3: conditional LangGraph workflow and preparation

Wrap stable services in a single explicit graph. Route only new/materially changed or deliberately invalidated analyses to LLM calls. Skip excluded and explicitly ineligible cases; preserve uncertain cases. Produce personalized plans for 2, 5, 10 and 20 hours with all requested preparation categories and attributable company-specific claims. Add a local notification payload contract containing job details, dates, eligibility, score/coverage, compensation source, strengths, gaps, preparation, priority, and official application link.

Gate: mocked graph end-to-end tests cover every branch, cache invalidation, prompt-injection attempts, LLM failure, and budget exhaustion. Persist before scheduling delivery. No unconstrained tool-using agent or multi-agent layer.

## Phase 4: automation and notifications

Add a transactional outbox, Telegram, email and local delivery implementations, immediate priority alerts and configurable daily/weekly digests. Separate event generation from delivery retries and channel state. Implement scheduled CLI execution with a single-run lock, Docker packaging with a persistent volume, redacted monitoring and backup/restore instructions. Extend CI with delivery automation for versioned Docker/release artifacts and the selected deployment target. Do not select a paid hosting provider implicitly.

Gate: provider mocks verify complete payloads, retry/deduplication behavior, digest boundaries, per-candidate/channel keys, quiet periods if configured, daylight-saving transitions, and crash recovery. Document the unavoidable external-delivery retry window. Validate credentials only through explicit user configuration.

## Phase 5: dashboard and application tracking

Add Streamlit over application services, with exploration, explanations, preferences, local notifications, and manual statuses: discovered, shortlisted, applied, interview scheduled, technical assessment, rejected, offer received, withdrawn, archived. Preserve status history. Capture interview topics, difficulties and preparation outcomes in separate records that can inform plans without modifying verified profile facts.

Gate: service-level tracking tests plus a dashboard smoke check; profile edits invalidate relevant analyses; archive/reject actions do not alter source lifecycle evidence. No automated applications or outbound recruiter contact.

## Phase 6: evaluation and productization

Extend the annotated benchmark with provenance, evidence spans, eligibility labels, and a held-out split; keep synthetic and real-source cases distinguishable. Compare hybrid analysis and the Phase 1 keyword baseline on the same data and policy. Report relevant-detection precision/recall and false-negative rate, eligibility accuracy, evidence grounding, score consistency, duplicate delivery rate, latency and observed LLM cost per job. Define treatment of ambiguous/unknown labels and actual-versus-estimated cost explicitly. Repeat stochastic analysis on fixed inputs to measure score variance; do not promise deterministic model outputs.

Set numerical quality targets only after baseline measurement. Gate paid/hosted expansion on source permissions, observed quality/cost, and a separate SaaS design covering authentication, tenant isolation, PostgreSQL migrations, private documents, quotas, deletion/export, operational security and monitoring. Avoid claiming commercial readiness from a successful personal MVP.
