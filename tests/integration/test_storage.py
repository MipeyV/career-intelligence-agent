import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from career_intelligence.collection import CollectionPreview, CollectionSnapshot, Posting
from career_intelligence.storage import (
    StorageError,
    Store,
    aliases,
    canonical_url,
    collisions,
    jobs,
    list_jobs,
    revisions,
    runs,
    schema,
    source_runs,
)


def sample(title="A", *, post_id="1", scope="board", url=None, day=0):
    seen = datetime(2026, 11, 27, tzinfo=UTC) + timedelta(days=day)
    return Posting(
        source_provider="greenhouse",
        source_scope=scope,
        source_post_id=post_id,
        company_key="example",
        company="Example",
        title=title,
        observed_at=seen,
        source_url=f"https://example.com/{post_id}",
        official_application_url=url,
        raw_record={"id": post_id, "title": title},
    )


def preview(*posts, status="complete", day=0):
    stamp = datetime(2026, 11, 27, tzinfo=UTC) + timedelta(days=day)
    scope = posts[0].source_scope if posts else "board"
    return CollectionPreview(
        mode="fixture",
        snapshots=[
            CollectionSnapshot(
                source_key=scope,
                scope_key=f"greenhouse:{scope}",
                started_at=stamp,
                completed_at=stamp,
                status=status,
                records=list(posts),
            )
        ],
    )


def count(store, table):
    with store.engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(table))


def test_restart_replay_and_historical_content(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    first = preview(sample())
    with Store(path) as store:
        store.ingest(first)
    with Store(path) as store:
        store.ingest(first)
        assert count(store, jobs) == count(store, revisions) == 1
        store.ingest(preview(sample("B", day=1), day=1))
        store.ingest(preview(sample("A", day=2), day=2))
        assert count(store, revisions) == 3
        store.ingest(first)
        assert count(store, revisions) == 3
    row = list_jobs(path)[0]
    assert row["payload"]["title"] == "A"
    assert row["revision"] == 3
    assert row["first_seen"].startswith("2026-11-27")
    assert row["last_seen"].startswith("2026-11-29")


def test_identity_before_url_and_conservative_collisions(tmp_path):
    url = "https://EXAMPLE.com/careers?gh_jid=1&utm_source=x#section"
    assert canonical_url(url) == "https://example.com/careers?gh_jid=1"
    with Store(tmp_path / "jobs.sqlite3") as store:
        store.ingest(preview(sample(url=url), sample(post_id="2", url=url)))
        assert count(store, jobs) == 2
        assert count(store, collisions) == 1
        store.ingest(preview(sample(post_id="3", url="https://example.com/careers?gh_jid=3")))
        assert count(store, jobs) == 3
        # URL aliases can connect different source scopes with consistent company identity.
        store.ingest(preview(sample(scope="second", url="https://example.com/careers?gh_jid=3")))
        assert count(store, jobs) == 3
        assert count(store, aliases) == 4


def test_metadata_only_changes_and_old_snapshots_do_not_create_revision(tmp_path):
    with Store(tmp_path / "jobs.sqlite3") as store:
        store.ingest(preview(sample(day=1), day=1))
        post = sample(day=2).model_copy(update={"source_updated_at": datetime.now(UTC)})
        store.ingest(preview(post, day=2))
        store.ingest(preview(sample("Older", day=0)))
        assert count(store, revisions) == 1
        assert list_jobs(store.path)[0]["payload"]["title"] == "A"


def test_source_transaction_rolls_back_and_retry_recovers(tmp_path, monkeypatch):
    path = tmp_path / "jobs.sqlite3"
    batch = preview(sample(), sample(post_id="2"))
    with Store(path) as store:
        original = store._save_post

        def fail_second(connection, post, source_run_id):
            original(connection, post, source_run_id)
            if post.source_post_id == "2":
                raise StorageError("Injected failure")

        with monkeypatch.context() as patch:
            patch.setattr(store, "_save_post", fail_second)
            for _ in range(2):
                assert store.ingest(batch)["status"] == "failed"
                assert count(store, jobs) == count(store, revisions) == 0
        store.ingest(batch)
        assert count(store, jobs) == count(store, revisions) == 2
        assert count(store, source_runs) == 1


def test_failed_and_partial_sources_keep_existing_jobs(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        store.ingest(preview(sample()))
        store.ingest(preview(status="failed", day=1))
        store.ingest(preview(sample(post_id="2", day=2), status="partial", day=2))
    assert len(list_jobs(path)) == 2


def test_schema_version_refused_and_lock_released(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        with store.engine.begin() as connection:
            connection.execute(schema.update().values(version=999))
    for _ in range(2):
        with pytest.raises(StorageError, match="schema version"):
            with Store(path):
                pass
    with pytest.raises(StorageError, match="schema version"):
        list_jobs(path)


def test_lock_excludes_other_process_and_releases_after_exit(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    script = """
import sys
from pathlib import Path
from career_intelligence.storage import Store, StorageError
try:
    with Store(Path(sys.argv[1])):
        pass
except StorageError:
    raise SystemExit(7)
"""
    with Store(path):
        result = subprocess.run([sys.executable, "-c", script, str(path)], timeout=15)
        assert result.returncode == 7
    result = subprocess.run([sys.executable, "-c", script, str(path)], timeout=15)
    assert result.returncode == 0


def test_cli_persists_across_processes(config_files, repository_root, tmp_path):
    settings, _, _ = config_files
    fixture = repository_root / "tests/fixtures/greenhouse/initial.json"
    for _ in range(2):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "career_intelligence",
                "collect",
                "--config",
                str(settings),
                "--fixture",
                str(fixture),
                "--persist",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "career_intelligence",
            "jobs",
            "list",
            "--config",
            str(settings),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)["jobs"]) == 2


def test_missing_read_does_not_create_database(tmp_path):
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(StorageError, match="does not exist"):
        list_jobs(path)
    assert not path.exists()


def test_aliases_with_same_facts_share_revision_and_retain_source_evidence(tmp_path):
    url = "https://example.com/careers?gh_jid=1"
    with Store(tmp_path / "jobs.sqlite3") as store:
        store.ingest(preview(sample(url=url)))
        store.ingest(preview(sample(scope="second", url=url)))
        assert count(store, jobs) == count(store, revisions) == 1
        assert count(store, aliases) == 2
        with store.engine.connect() as connection:
            records = connection.scalars(select(source_runs.c.details)).all()
        assert len(records) == 2
        assert records[1]["records"][0]["raw"]["id"] == "1"


def test_failure_of_one_source_does_not_rollback_another(tmp_path):
    bad = preview(sample(), sample()).snapshots[0]
    good = preview(sample(scope="good")).snapshots[0]
    with Store(tmp_path / "jobs.sqlite3") as store:
        saved = store.ingest(CollectionPreview(mode="fixture", snapshots=[bad, good]))
        assert [source["status"] for source in saved["sources"]] == ["storage_failed", "complete"]
        assert count(store, jobs) == count(store, revisions) == 1


def test_foreign_keys_are_enforced(tmp_path):
    from sqlalchemy.exc import IntegrityError

    with Store(tmp_path / "jobs.sqlite3") as store:
        with pytest.raises(IntegrityError), store.engine.begin() as connection:
            connection.execute(
                aliases.insert().values(
                    id="bad",
                    job_id="absent",
                    provider="example",
                    scope="example",
                    post_id="1",
                    fingerprint="hash",
                    last_seen="2026-11-27",
                )
            )


def test_interrupted_run_is_marked_on_next_writer_startup(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        with store.engine.begin() as connection:
            connection.execute(
                runs.insert().values(
                    id="interrupted-test",
                    started_at="2026-11-27T00:00:00+00:00",
                    status="running",
                )
            )
    with Store(path) as store:
        with store.engine.connect() as connection:
            assert connection.scalar(select(runs.c.status)) == "interrupted"
