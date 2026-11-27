import json
import sqlite3

import pytest

from career_intelligence.backup import copy_database
from career_intelligence.cli import main
from career_intelligence.config import load_configuration
from career_intelligence.storage import StorageError, Store, list_changes, list_jobs


def test_backup_restore_preserves_jobs_events_and_decisions(config_files, repository_root, capsys):
    settings, _, _ = config_files
    fixture = repository_root / "tests/fixtures/greenhouse/initial.json"
    assert (
        main(
            [
                "collect",
                "--config",
                str(settings),
                "--fixture",
                str(fixture),
                "--persist",
            ]
        )
        == 0
    )
    capsys.readouterr()
    database = load_configuration(settings).settings.database_path
    backup = settings.parent / "snapshot.sqlite3"
    restored = settings.parent / "restored.sqlite3"
    assert main(["db", "backup", "--config", str(settings), "--output", str(backup)]) == 0
    assert json.loads(capsys.readouterr().out)["event"] == "database_backed_up"
    assert (
        main(
            [
                "db",
                "restore",
                "--backup",
                str(backup),
                "--destination",
                str(restored),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["schema_version"] == 3
    assert list_jobs(database) == list_jobs(restored)
    assert list_changes(database) == list_changes(restored)
    with sqlite3.connect(database) as original, sqlite3.connect(restored) as recovered:
        assert original.execute("SELECT * FROM filter_decisions").fetchall() == (
            recovered.execute("SELECT * FROM filter_decisions").fetchall()
        )


def test_existing_destination_and_self_copy_are_preserved(tmp_path):
    source = tmp_path / "jobs.sqlite3"
    with Store(source):
        pass
    protected = tmp_path / "protected.txt"
    protected.write_text("Keep this user data", encoding="utf-8")
    for destination in (source, protected):
        with pytest.raises(StorageError, match="new file"):
            copy_database(source, destination)
    assert protected.read_text(encoding="utf-8") == "Keep this user data"


def test_failed_backup_does_not_publish_partial_output(tmp_path):
    source = tmp_path / "corrupt.sqlite3"
    source.write_bytes(b"invalid database content")
    destination = tmp_path / "backup.sqlite3"
    with pytest.raises(StorageError):
        copy_database(source, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".career-backup-*"))


def test_backup_refuses_active_collection_writer(tmp_path):
    source = tmp_path / "jobs.sqlite3"
    destination = tmp_path / "backup.sqlite3"
    with Store(source):
        with pytest.raises(StorageError, match="Another"):
            copy_database(source, destination)
    assert not destination.exists()


def test_old_schema_backup_does_not_upgrade_source(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO schema_version VALUES (1)")
    copy_database(source, tmp_path / "legacy-backup.sqlite3")
    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchall() == [(1,)]
