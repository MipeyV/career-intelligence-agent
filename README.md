# Career Intelligence Agent

A personal job monitoring and career recommendation system, designed to grow into a maintainable product. Discover official opportunities, retain their history, explain candidate relevance, and support interview preparation. Applications always remain a manual action.

## Current status

**Planning baseline approved; implementation pending.** Repository inspection on 2026-11-25 found an empty repository with no commits, source code, dependency manifest, tests, or existing project instructions. The user approved the Phase 1 plan and dependency set, and authorized publishing this initial documentation baseline on `main`. There is no executable application yet, and no dependencies have been installed.

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

## Proposed dependency set

Target Python 3.12 initially, with a locked and tested dependency set chosen during milestone 1.1.

| Purpose | Proposal |
| --- | --- |
| Data and configuration validation | Pydantic |
| YAML parsing | PyYAML, using safe loading |
| HTTP transport | HTTPX |
| Persistence | SQLAlchemy with Python's SQLite driver |
| CLI, retry logic, text extraction, logging | Python standard library |
| Development checks | Pytest and Ruff |

The user approved this Phase 1 dependency set on 2026-11-25. No Typer, retry framework, vector database, queue, web server, or migration framework is necessary for the first milestone. LangChain provider adapters are planned for Phase 2 and LangGraph orchestration for Phase 3, with their dependencies reviewed at those boundaries.

## Configuration and privacy

The implementation will introduce these files; they do not exist yet:

- `conf/profile.example.yml`: synthetic candidate data documenting the profile contract.
- `conf/profile.local.yml`: ignored, private candidate profile, optionally populated from the supplied brief with the user's agreement.
- `conf/companies.yml`: extensible company watchlist, source identifiers, and enabled flags. Companies without validated connectors remain disabled with a reason.
- `conf/settings.yml`: database path, collection limits, filter policy, and source reconciliation policy.
- `conf/scoring.yml`: introduced in Phase 2 with versioned dimension weights.
- `.env.example`: variable names and placeholders only; credentials are read from the environment.

Profile configuration will support education and graduation dates, availability, employment episodes and apprenticeship status, skills and evidence, target roles, ordered locations, compensation target and fallback, and explicitly known languages and work rights. An unknown authorization or language level stays unknown. No personal details belong in application logic or committed test fixtures.

The supplied October 2027 availability and approximately EUR 90,000 annual total-compensation objective are user configuration requirements, not hardcoded rules. A graduation year alone must not become an invented exact graduation date. The fallback compensation threshold requires a user choice; unknown salary never causes automatic rejection.

## Intended commands

The following illustrate the proposed interface and **are not runnable yet**:

```text
career-intelligence config validate --config conf/settings.yml
career-intelligence collect --config conf/settings.yml
career-intelligence collect --config conf/settings.yml --fixture tests/fixtures/greenhouse/initial.json
career-intelligence jobs list --decision keep,review --format table
career-intelligence changes list --since-run <run-id> --format json
```

The collection command will report source successes, failures, item counts, revision counts, and duration. Normal output goes to stdout; structured operational logs go to stderr. A live-source smoke test will be optional and separate from offline CI.

## Long-term scope

The roadmap retains the full product direction: additional ATS and authorized discovery sources; evidence-backed eligibility; independent semantic dimensions and deterministic weighted scores; compensation provenance; 2/5/10/20-hour preparation plans; notifications and digests; application tracking and interview feedback; benchmark evaluation; and eventual SaaS tenancy and deployment design. Unknown pay and ambiguous eligibility remain visible throughout.

## Contribution and CI/CD workflow

This initial documentation baseline is the authorized direct push to `main`. Every subsequent increment uses a dedicated branch and a pull request targeting `main`, starting with `feat/phase-1-1-bootstrap`. Keep one independently testable milestone per PR, and describe the resulting behavior, checks run, and remaining limitations.

Introduce GitHub Actions with the first executable milestone: locked installation, Ruff lint/format checks, Pytest on Windows and Linux, and package-build validation. Consolidate and extend these checks as functionality grows. Delivery automation follows in Phase 4 with Docker and a separately chosen release/deployment target; no deployment pipeline exists yet.

The next implementation step is milestone **1.1** on its own branch. The Phase 1 scope and dependencies are approved. This baseline contains documentation only.
