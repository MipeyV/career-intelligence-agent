# Career Intelligence Agent

A personal job monitoring and career recommendation system, designed to grow into a maintainable product. Discover official opportunities, retain their history, explain candidate relevance, and support interview preparation. Applications always remain a manual action.

## Current status

**Milestone 1.6 implemented.** The deterministic MVP includes discovery, persistence, lifecycle tracking, explained filtering, backup/restore commands and a reproducible synthetic benchmark. The default watchlist contains 39 companies with live collection disabled. The benchmark's annotations still require independent human review.

Start with [ARCHITECTURE.md](ARCHITECTURE.md) for design decisions and [ROADMAP.md](ROADMAP.md) for independently testable milestones and MVP acceptance criteria. [AGENTS.md](AGENTS.md) defines implementation rules for future contributors.

## Proposed first release

Phase 1 is a deterministic, single-user Python CLI:

1. Load validated YAML settings, company sources, and candidate filtering preferences.
2. Collect public postings from configured Greenhouse boards, with bounded requests and isolated failures.
3. Normalize records into a common Pydantic schema, preserving unknowns and source evidence.
4. Persist postings, revisions, collection outcomes, and change events in SQLite.
5. Detect new, changed, missing, and reappearing postings without treating source failures as closures.
6. Explain conservative role, location, and explicit seniority filters, while keeping uncertain opportunities visible.
7. List opportunities and changes in the terminal or export JSON, with repeatable offline fixture replay.

No LLM, hosted service, API key, or external notification credentials will be needed for Phase 1. Terminal results are the initial local reporting surface. Telegram, email, dashboard delivery, automated scheduling, semantic matching, and application tracking belong to later milestones.

## Installation and commands

Use **Python 3.12** and **uv 0.12.12**. The first supported Python range is deliberately limited to 3.12 until additional versions are tested. Install uv through the [official installation instructions](https://docs.astral.sh/uv/getting-started/installation/). The exact uv version is required by `pyproject.toml`; `.python-version` selects Python 3.12.

From the repository root (PowerShell or a POSIX shell):

```text
uv sync --locked --dev
uv run --locked --offline career-intelligence --help
uv run --locked --offline career-intelligence --version
uv run --locked --offline career-intelligence config validate --config conf/settings.yml
```

Initial synchronization downloads dependencies and may provision Python if needed. Once installed, the CLI works offline and needs no API keys. Alternatively use `uv run --locked --offline python -m career_intelligence ...` with the same arguments.

Successful validation writes one JSON record to stdout:

```json
{"event": "configuration_valid", "schema_version": 1, "company_count": 39, "enabled_source_count": 0}
```

Validation errors return code `2` and a JSON diagnostic on stderr, without private input values or YAML excerpts. Invalid command syntax also returns `2` with argparse usage text. Validation does not create the database or connect to sources.

## Dependencies

Runtime and development versions are pinned with hashes in `uv.lock`. Use `uv sync --locked` rather than resolving versions on each installation.

| Purpose | Proposal |
| --- | --- |
| Data and configuration validation | Pydantic |
| YAML parsing | PyYAML, using safe loading |
| HTTP transport | HTTPX |
| Persistence | SQLAlchemy with Python's SQLite driver |
| CLI, retry logic, text extraction, logging | Python standard library |
| Development checks | Pytest and Ruff |
| Package build backend | uv_build, matching the pinned uv toolchain |

The user approved this Phase 1 dependency set on 2026-11-25. No Typer, retry framework, vector database, queue, web server, or migration framework is necessary for the first milestone. LangChain provider adapters are planned for Phase 2 and LangGraph orchestration for Phase 3, with their dependencies reviewed at those boundaries.

## Configuration and privacy

The repository includes:

- `conf/profile.example.yml`: synthetic candidate data documenting the profile contract.
- `conf/profile.local.yml`: intended ignored path for your private copy; not included in the repository.
- `conf/companies.yml`: extensible company watchlist, source identifiers, and enabled flags. Companies without validated connectors remain disabled with a reason.
- `conf/settings.yml`: database path, collection limits, filter policy, and source reconciliation policy.
- `conf/scoring.yml`: planned for Phase 2; not present yet.
- `.env.example`: variable names and placeholders only; credentials are read from the environment.

Profile configuration supports education and graduation dates, availability, employment episodes and apprenticeship status, skills and evidence, projects, target roles, ordered locations, compensation target and fallback, and explicitly known languages and work rights. Omitted candidate fact lists remain null; an explicitly empty list remains empty. No personal details belong in application logic or committed test fixtures.

Copy `conf/profile.example.yml` to `conf/profile.local.yml`, replace the synthetic facts, then set `profile_file: profile.local.yml` in your local settings copy (for example `conf/settings.local.yml`, also ignored). Relative profile, company and database paths are resolved against the settings file's directory, independent of the working directory. `.env.example` documents future variables only: this milestone does not read `.env` files.

Dates accept `YYYY`, `YYYY-MM` and `YYYY-MM-DD` text and preserve precision; quote a year such as `"2028"` so YAML does not treat it as an integer. Unknown facts use null. The loader rejects unknown fields, duplicate keys, aliases, custom YAML tags, invalid dates/limits and files larger than 1 MiB. It accepts UTF-8 with or without a byte-order mark.

Companies can be added without a connector when `disabled_reason` explains why collection is disabled. An enabled source requires a unique key, provider `greenhouse`, a board token and an attributable HTTPS careers URL. Other provider names can be documented as disabled future integrations.

## Preview job collection

Run a synthetic Greenhouse response through the normalizer offline:

```text
uv run --locked --offline career-intelligence collect --config conf/settings.yml --fixture tests/fixtures/greenhouse/initial.json
```

For a live preview, use a local settings and watchlist copy. After reviewing the official board and its applicable access conditions, add an enabled source under its company in `companies.local.yml` and remove that company's `disabled_reason`. Point `settings.local.yml` at the local watchlist. Select a single enabled source by its configured key:

```text
uv run --locked career-intelligence collect --config conf/settings.local.yml --live --source SOURCE_KEY
```

Omitting `--source` processes every enabled source sequentially. The connector uses the public Greenhouse list endpoint with full descriptions, a run deadline, rate limits and bounded retries. It preserves available source facts and emits JSON null for unknown facts. Publication time and source update time have separate fields. [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html#list-jobs).

The preview writes normalized records and a status per source to stdout. Structured issue summaries go to stderr. A complete preview exits with `0`. A partial preview or a mix of successful and failed sources exits with `3`. An entirely failed preview exits with `1`. Invalid arguments or configuration exit with `2`. The preview keeps evidence in memory and creates no database.

## Persist and inspect opportunities

Add `--persist` to collection to save observations to `database_path` from the settings file. The flag works with both fixture replay and live sources.

```text
uv run --locked --offline career-intelligence collect --config conf/settings.yml --fixture tests/fixtures/greenhouse/initial.json --persist
uv run --locked --offline career-intelligence jobs list --config conf/settings.yml --limit 100 --offset 0
```

The job list contains stable internal IDs, first/last observation times, revision numbers and the latest normalized payload. Raw source records are retained in the database for audit. Source identifiers resolve repeated postings, and conservative URL aliases connect postings across sources when company identity agrees. Identifying URL query parameters are preserved. Content changes create immutable revisions, including a return to previously observed content.

Each source commits in its own transaction. A source storage failure rolls back its observations and is recorded separately while other sources continue. Storage errors exit with `1`. A writer lock spans collection and persistence. Another writer fails with a diagnostic, and the operating system releases the lock after a process exits. Keep the persistent lock sidecar beside its database.

Keep private databases and backups under the ignored `data/` directory. An unsupported or unrecognized schema is refused. Read-only listing requires an existing initialized database. Phase 1 retains all source evidence and history until the operator archives the database.

## Backup, restore and evaluation

```text
uv run --locked --offline career-intelligence db backup --config conf/settings.yml --output data/backups/snapshot.sqlite3
uv run --locked --offline career-intelligence db restore --backup data/backups/snapshot.sqlite3 --destination data/recovered.sqlite3
uv run --locked --offline python scripts/evaluate.py
```

Backup and restore validate database integrity and foreign keys, then publish to a new destination. Existing output files are refused. Atomic publication requires a filesystem with hard-link support. See the [operating guide](docs/OPERATIONS.md) for settings, source failures, disposable replay, backup checks, restoration and retention.

The [synthetic benchmark](evaluation/README.md) contains 16 curated descriptions and a keyword baseline. Its committed report records measured quality, latency, replay outcomes and backup/restore equality. Ambiguous cases have a separate accounting. The offline end-to-end test exercises API transport, normalization, storage, lifecycle, filtering, CLI and restore with controlled responses.

## Inspect changes and availability

```text
uv run --locked --offline career-intelligence changes list --config conf/settings.yml
uv run --locked --offline career-intelligence changes list --config conf/settings.yml --since-run RUN_ID --limit 100 --offset 0
```

Events include `new`, `updated`, `missing`, `returned`, `removed_from_source` and `reopened`. Updates include before/after values for changed fields. Every event links to the job and source collection run. `--since-run` returns events from subsequent runs and rejects unknown IDs. Collection summaries include `change_count`.

The default policy requires two consecutive complete omissions spanning at least 24 hours before recording removal from a source. Failed or partial snapshots reset omission counters. An empty or strongly depleted board first needs a distinct later complete poll with the same posting IDs. The configurable depletion threshold is 50 percent. Only confirmed observations from the same source key and scope can contribute absences.

Jobs remain listed while another alias confirms presence. Reappearance restores listed availability and records its transition even when the description is unchanged. Older snapshots cannot overwrite newer reconciliation evidence. The current availability appears separately from the historical revision payload.

The first persistent collection upgrades a version 1 database to version 2 transactionally. Existing postings and evidence are preserved. Existing aliases acquire availability evidence when observed again, and historical revisions receive no backfilled events. Back up existing databases before upgrading.

## Filter and review opportunities

```text
uv run --locked --offline career-intelligence jobs refilter --config conf/settings.yml
uv run --locked --offline career-intelligence jobs list --config conf/settings.yml --decision keep,review --format table
uv run --locked --offline career-intelligence changes list --config conf/settings.yml --format table
```

Persistent collection evaluates current stored jobs using the linked profile and filter policy. Role aliases, location aliases and excluded title seniorities are configured under `filters` in settings. Roles, ordered location preferences and compensation targets come from the profile. Text matching uses complete word sequences and preserves matched evidence.

`keep` identifies opportunities matching the discovery preferences. `review` preserves ambiguous roles or locations and requirements needing interpretation. `exclude` requires a configured incompatible title seniority or an explicitly comparable employer-disclosed total compensation range below the configured floor. A missing junior label never causes exclusion. These decisions support discovery. Comprehensive eligibility and semantic matching follow in Phase 2.

The optional compensation filter uses the fallback threshold when present, otherwise the target threshold. Comparison requires `kind: total_compensation`, `period: annual`, the profile currency, `provenance: employer_disclosed`, a source URL and a source date. Undisclosed pay, base pay alone, estimates, different currencies and ambiguous ranges remain for review when pay filtering is enabled.

JSON includes the decision, configuration hash, filter version, location rank and evidence-bearing reason codes. Table output includes decisions, reason codes and official application links. Location rank follows profile preference order. Decision selection happens before pagination. JSON remains the default format.

Changing the profile or policy creates a different configuration hash. Existing decisions remain auditable. Jobs without a decision for the current revision/configuration show as pending. Selecting decisions requires `jobs refilter` when any current decisions are missing or stale, so unseen jobs are surfaced explicitly.

Schema migration 3 adds the decision table while preserving existing jobs, revisions and events. Persistent collection or `jobs refilter` upgrades an existing schema 1 or 2 database under the writer lock. Back up an existing database before upgrading.

## Verification

Run the same checks used by CI:

```text
uv run --locked --offline python -m ruff check .
uv run --locked --offline python -m ruff format --check .
uv run --locked --offline python -m pytest
uv build --no-build-isolation --offline
uv run --locked --offline python scripts/check_distribution.py
```

Tests use synthetic data and temporary files, reject attempted network connections, and cover configuration validation, date precision, unknown facts, source identity, output redaction, CLI exit codes, Git ignore rules and imports without application-state creation. The distribution check installs the built wheel and locked runtime dependencies in a fresh temporary environment and exercises it outside the repository. Keep only the current wheel in `dist/` for that check.

If the shared Windows temporary folder has restrictive permissions, run Pytest with `--basetemp .pytest_cache/local-run` using a disposable project-local directory. This was needed for local verification on the development machine.

The GitHub Actions workflow also exercises the fixture preview through the installed CLI. A live API check is separate from the offline test suite.

## Long-term scope

The roadmap retains the full product direction: additional ATS and authorized discovery sources; evidence-backed eligibility; independent semantic dimensions and deterministic weighted scores; compensation provenance; 2/5/10/20-hour preparation plans; notifications and digests; application tracking and interview feedback; benchmark evaluation; and eventual SaaS tenancy and deployment design. Unknown pay and ambiguous eligibility remain visible throughout.

## Contribution and CI/CD workflow

The initial documentation baseline was the authorized direct push to `main`. Every subsequent increment uses a dedicated branch and a pull request targeting `main`, starting with `feat/phase-1-1-bootstrap`. Keep one independently testable milestone per PR, and describe the resulting behavior, checks run, and remaining limitations.

`.github/workflows/ci.yml` checks PRs to `main` and pushes to `main`: locked installation, Ruff lint/format, Pytest on Windows and Linux, package build and isolated wheel installation. Actions are pinned to verified commit SHAs with read-only repository permissions. The workflow is defined here; its observed run results are reported on the PR. Configuring required branch checks is a separate repository setting; this change does not claim to enable branch protection.

Phase 1's deterministic implementation milestones are complete. Independent annotation review and a broader real-source held-out benchmark remain pending. Phase 2 introduces structured intelligence after dependency/provider review. Delivery automation follows in Phase 4 with Docker and a separately chosen release target.
