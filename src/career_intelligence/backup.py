"""Consistent SQLite snapshots and restoration to a new destination."""

import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from career_intelligence.storage import SCHEMA_VERSION, StorageError, _writer_lock


def _check(connection: sqlite3.Connection) -> int:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise StorageError("Database integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise StorageError("Database foreign-key check failed")
    versions = connection.execute("SELECT version FROM schema_version").fetchall()
    if len(versions) != 1 or versions[0][0] not in range(1, SCHEMA_VERSION + 1):
        raise StorageError("Unsupported backup schema version")
    return versions[0][0]


def copy_database(source: Path, destination: Path) -> dict:
    """Copy via SQLite backup and publish atomically without overwriting a file."""
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise StorageError("Source database does not exist")
    if source == destination or destination.exists():
        raise StorageError("Destination must be a new file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _writer_lock(source.with_name(source.name + ".lock")):
            with tempfile.TemporaryDirectory(
                prefix=".career-backup-", dir=destination.parent
            ) as folder:
                temporary = Path(folder) / "snapshot.sqlite3"
                with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
                    version = _check(reader)
                    with closing(sqlite3.connect(temporary)) as writer:
                        reader.backup(writer)
                        _check(writer)
                # All connections are closed before publication, including on Windows.
                # Hard-link creation is atomic and refuses an existing destination.
                os.link(temporary, destination)
    except sqlite3.Error:
        raise StorageError("Cannot read or copy the SQLite database") from None
    except OSError:
        raise StorageError(
            "Cannot publish backup. Use a new destination on a writable filesystem "
            "with hard-link support"
        ) from None
    return {
        "schema_version": version,
        "destination": str(destination),
        "bytes": destination.stat().st_size,
    }
