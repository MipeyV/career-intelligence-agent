import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from career_intelligence.collection import CollectionPreview, CollectionSnapshot, Posting
from career_intelligence.config import ReconciliationPolicy
from career_intelligence.storage import (
    LIFECYCLE_TABLES,
    StorageError,
    Store,
    list_changes,
    list_jobs,
    schema,
)

EPOCH = datetime(2026, 11, 27, tzinfo=UTC)


def poll(ids=(1, 2, 3), *, hour=0, status="complete", scope="board", key="board", **updates):
    now = EPOCH + timedelta(hours=hour)
    posts = [
        Posting(
            source_provider="greenhouse",
            source_scope=scope,
            source_post_id=str(i),
            company_key="example",
            company="Example",
            title=f"Role {i}",
            observed_at=now,
            source_url=f"https://example.com/jobs/{i}",
            official_application_url=f"https://example.com/jobs/{i}",
            raw_record={"id": i},
            **updates,
        )
        for i in ids
    ]
    return CollectionPreview(
        mode="fixture",
        snapshots=[
            CollectionSnapshot(
                source_key=key,
                scope_key=f"greenhouse:{scope}",
                started_at=now,
                completed_at=now,
                status=status,
                records=posts,
            )
        ],
    )


def kinds(path):
    return [event["kind"] for event in list_changes(path)]


def test_removal_reopening_and_replay(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        first_run = store.ingest(poll())["run_id"]
        store.ingest(poll((2, 3), hour=1))
        store.ingest(poll((2, 3), hour=2))
        assert kinds(path).count("missing") == 1
        assert "removed_from_source" not in kinds(path)
        removal = poll((2, 3), hour=25)
        store.ingest(removal)
        store.ingest(removal)
        assert kinds(path).count("removed_from_source") == 1
        returning = poll(hour=26)
        store.ingest(returning)
        store.ingest(returning)
        assert kinds(path).count("reopened") == 1
        assert all(job["availability"] == "listed" for job in list_jobs(path))
        assert len(list_changes(path, since_run=first_run)) == 3
        with pytest.raises(StorageError, match="Unknown"):
            list_changes(path, since_run="absent")


@pytest.mark.parametrize("failure", ["partial", "failed"])
def test_failure_breaks_streak(tmp_path, failure):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        store.ingest(poll())
        store.ingest(poll((2, 3), hour=1))
        store.ingest(poll((), hour=25, status=failure))
        store.ingest(poll((2, 3), hour=26))
        assert "removed_from_source" not in kinds(path)
        store.ingest(poll((2, 3), hour=50))
        assert kinds(path).count("removed_from_source") == 1


def test_empty_anomaly_needs_another_independent_poll(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        store.ingest(poll())
        empty = poll((), hour=1)
        store.ingest(empty)
        store.ingest(empty)
        assert kinds(path) == ["new"] * 3
        store.ingest(poll((), hour=25))
        assert kinds(path).count("missing") == 3
        assert "removed_from_source" not in kinds(path)
        store.ingest(poll((), hour=49))
        assert kinds(path).count("removed_from_source") == 3


def test_changed_scope_and_out_of_order_polls_do_not_remove(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        store.ingest(poll(hour=10))
        store.ingest(poll((), hour=11, scope="other"))
        store.ingest(poll((), hour=35, scope="other"))
        store.ingest(poll((), hour=2))
        assert kinds(path) == ["new"] * 3
        assert all(job["availability"] == "listed" for job in list_jobs(path))
        store.ingest(poll((2, 3), hour=12))
        store.ingest(poll(hour=11, description="Old observation"))
        assert "updated" not in kinds(path)
        store.ingest(poll(hour=13))
        assert kinds(path)[-1] == "returned"


def test_other_alias_keeps_opportunity_available(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        store.ingest(poll())
        store.ingest(poll(hour=1, scope="second", key="second"))
        store.ingest(poll((2, 3), hour=2))
        store.ingest(poll((2, 3), hour=26))
        assert "removed_from_source" in kinds(path)
        assert len(list_jobs(path)) == 3
        assert all(job["availability"] == "listed" for job in list_jobs(path))


def test_fields_and_unchanged_polls(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        store.ingest(poll((1,)))
        store.ingest(poll((1,), hour=1))
        store.ingest(
            poll(
                (1,),
                hour=2,
                description="Changed",
                application_deadline=EPOCH.date(),
                required_experience="PhD",
                compensation=[{"minimum": 100000, "currency": "EUR"}],
            )
        )
        events = list_changes(path)
        assert [event["kind"] for event in events] == ["new", "updated"]
        assert set(events[-1]["fields"]) == {
            "description",
            "application_deadline",
            "required_experience",
            "compensation",
        }
        assert events[-1]["fields"]["description"] == {"before": None, "after": "Changed"}


def test_policy_change_resets_omission_evidence(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        store.ingest(poll())
        store.ingest(poll((2, 3), hour=1))
        policy = ReconciliationPolicy(complete_omissions_required=3)
        store.ingest(poll((2, 3), hour=25), policy)
        store.ingest(poll((2, 3), hour=26), policy)
        assert "removed_from_source" not in kinds(path)
        store.ingest(poll((2, 3), hour=50), policy)
        store.ingest(poll((2, 3), hour=51), policy)
        assert kinds(path).count("removed_from_source") == 1


def test_migrate_version_one_preserves_jobs(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        store.ingest(poll())
        with store.engine.begin() as connection:
            for table in reversed(LIFECYCLE_TABLES):
                table.drop(connection)
            connection.execute(schema.update().values(version=1))
    with Store(path):
        pass
    assert len(list_jobs(path)) == 3
    assert list_changes(path) == []
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (2,)


def test_source_failure_rolls_back_events_and_presence(tmp_path, monkeypatch):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        store.ingest(poll())
        original = store._reconcile

        def fail_after_reconcile(connection, snapshot, source_run_id, policy):
            original(connection, snapshot, source_run_id, policy)
            if snapshot.status == "complete":
                raise StorageError("Injected transaction failure")

        with monkeypatch.context() as patch:
            patch.setattr(store, "_reconcile", fail_after_reconcile)
            assert store.ingest(poll((2, 3), hour=1))["status"] == "failed"
        assert kinds(path) == ["new"] * 3
        assert all(row["availability"] == "listed" for row in list_jobs(path))
        store.ingest(poll((2, 3), hour=25))
        assert "removed_from_source" not in kinds(path)
        store.ingest(poll((2, 3), hour=49))
        assert kinds(path).count("removed_from_source") == 1


def test_cli_changes_since_run(config_files, repository_root, capsys):
    import json

    from career_intelligence.cli import main

    settings, _, _ = config_files
    fixture = repository_root / "tests/fixtures/greenhouse/initial.json"
    assert main(["collect", "--config", str(settings), "--fixture", str(fixture), "--persist"]) == 0
    output = capsys.readouterr()
    saved = json.loads(output.err)
    assert saved["change_count"] == 2
    assert main(["changes", "list", "--config", str(settings)]) == 0
    events = json.loads(capsys.readouterr().out)["changes"]
    assert [event["kind"] for event in events] == ["new", "new"]
    assert (
        main(
            [
                "changes",
                "list",
                "--config",
                str(settings),
                "--since-run",
                saved["run_id"],
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["changes"] == []


def test_depleted_response_confirmation_and_disabled_source(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        store.ingest(poll())
        store.ingest(poll((3,), hour=1))
        assert "missing" not in kinds(path)
        store.ingest(poll((3,), hour=2))
        assert kinds(path).count("missing") == 2
        # Disabled sources produce no snapshot and therefore no absence evidence.
        store.ingest(CollectionPreview(mode="fixture", snapshots=[]))
        assert "removed_from_source" not in kinds(path)
        store.ingest(poll((3,), hour=26))
        assert kinds(path).count("removed_from_source") == 2


def test_source_key_change_does_not_reuse_previous_omissions(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        store.ingest(poll())
        store.ingest(poll((2, 3), hour=1))
        store.ingest(poll((2, 3), hour=25, key="renamed"))
        store.ingest(poll((2, 3), hour=49, key="renamed"))
        assert "removed_from_source" not in kinds(path)
