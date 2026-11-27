# Local operation and recovery

## Install and validate

Use uv 0.12.12 and Python 3.12. From the repository root, run the following.

```text
uv sync --locked --dev
uv run --locked --offline career-intelligence config validate --config conf/settings.yml
```

The committed profile is synthetic. Copy it to `conf/profile.local.yml`, edit your verified facts, and reference it from an ignored `conf/settings.local.yml`. Make a `conf/companies.local.yml` copy for reviewed source enablement. Relative profile, watchlist and database paths resolve against the settings directory.

The default database path is `data/career-intelligence.sqlite3`. Keep runtime files, backups and private profiles under ignored paths. The current schema version is 3. Persistent collection and refiltering upgrade supported older versions under the writer lock.

## Disposable offline demonstration

Use an ignored settings copy such as `conf/demo.local.yml`. Set `database_path: ../data/demo/jobs.sqlite3` and keep the example profile and watchlist references. Then run the following commands.

```text
uv run --locked --offline career-intelligence collect --config conf/demo.local.yml --fixture tests/fixtures/greenhouse/initial.json --persist
uv run --locked --offline career-intelligence jobs list --config conf/demo.local.yml --format table
uv run --locked --offline career-intelligence changes list --config conf/demo.local.yml
uv run --locked --offline career-intelligence jobs refilter --config conf/demo.local.yml
```

Replaying the fixture preserves job identities and unchanged revisions. A freshly read fixture has new observation timestamps, while exact snapshot replay is also covered by integration tests.

To reset a disposable demonstration, select a fresh path such as `../data/demo/run-2.sqlite3` in the demo settings. This starts a new database while preserving earlier demo files. Keep personal collection in separate local settings.

## Live collection

Enable only sources whose board tokens and intended access conditions have been reviewed. Live collection is an explicit action.

```text
uv run --locked career-intelligence collect --config conf/settings.local.yml --live --source SOURCE_KEY --persist
```

With no source selection, all enabled sources are processed sequentially. The first live smoke check for milestone 1.2 used a single public Datadog API GET and recorded aggregate counts. Source review details are in [ARCHITECTURE.md](../ARCHITECTURE.md). Recheck access conditions before recurring use and before commercial reuse.

## Interpret failures

| Exit code | Meaning and recovery |
| --- | --- |
| 0 | Requested operation completed successfully |
| 1 | Runtime/storage failure or all sources failed. Inspect stderr and retry after addressing the cause |
| 2 | Invalid invocation/configuration. Correct the reported field or command |
| 3 | Partial collection or mixed source outcomes. Inspect each source summary before retry |

JSON results go to stdout and operational diagnostics go to stderr. Collection output includes source status, issue codes and counts. Persistent collection summaries include run ID, source outcomes, change count and discovery-filter counts.

A 404 board, time limit, count mismatch or malformed response is recorded as failed or partial. Those outcomes break omission streaks. A complete empty/depleted response needs a distinct confirming poll before it contributes absences. Removal then follows the configured complete-omission count and grace period. Inspect change events and job availability before acting.

When another writer holds the database lock, wait for that process to finish. The OS releases the lock after process termination. The sidecar file can remain on disk and should stay beside its database. An interrupted run is marked on the next writer startup.

Missing or stale filter decisions require `jobs refilter`. If reads report an unsupported schema, confirm the installed code version and use an appropriate backup. Avoid using older application code against a newer database.

## Backup

Choose a new output name for each snapshot.

```text
uv run --locked --offline career-intelligence db backup --config conf/settings.local.yml --output data/backups/before-upgrade.sqlite3
```

The command takes the normal writer lock, opens the source read-only, validates SQLite integrity and foreign keys, and copies through the SQLite backup API. It validates the copy and publishes to a new destination using an atomic hard link. Source and destination must differ. Existing output files are refused. Use a local writable filesystem with hard-link support, such as the NTFS and Linux filesystems used in tests. [Python SQLite backup API](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup).

## Restore and verify

Restore into a new file and then point a local settings copy at it.

```text
uv run --locked --offline career-intelligence db restore --backup data/backups/before-upgrade.sqlite3 --destination data/recovered.sqlite3
```

Set `database_path: ../data/recovered.sqlite3` in an ignored settings file under `conf/`. Validate configuration, refilter with the intended profile, and inspect jobs and events.

```text
uv run --locked --offline career-intelligence config validate --config conf/recovered.local.yml
uv run --locked --offline career-intelligence jobs refilter --config conf/recovered.local.yml
uv run --locked --offline career-intelligence jobs list --config conf/recovered.local.yml
uv run --locked --offline career-intelligence changes list --config conf/recovered.local.yml
```

Refiltering performs supported schema upgrades when needed. The restored database retains source snapshots, revisions, events, decisions and identity mappings. Tests compare these records across backup and restore. Retain the original and backup until the restored copy has been checked.

## Retention and monitoring

Phase 1 retains all source snapshots, raw evidence, revisions and events until the operator archives the database. No automatic purge is enabled. Observe database size and collection durations, keep timestamped backups outside the live database file, and test restoration periodically. Per-record retention/purge policy and scheduled monitoring are part of later automation work.

The synthetic evaluation report includes observed latency, item counts, replay duplicate events and failure outcomes. Reproduce it with `uv run --locked --offline python scripts/evaluate.py`. Human annotation review and evaluation on real-source held-out cases remain pending.

## Validation

CI runs locked installation, offline tests, lint/format, CLI collection/filter/history commands, the synthetic benchmark, backup/restore checks, package build and installed-wheel validation on Windows and Ubuntu.

On this development Windows host, the shared Pytest Temp directory had restrictive permissions. A disposable project-local directory works with `uv run --locked --offline python -m pytest --basetemp .pytest_cache/local-run`. Pytest clears the chosen base directory, so choose a dedicated test-cache path. The standard CI command uses each runner's temporary directory.
