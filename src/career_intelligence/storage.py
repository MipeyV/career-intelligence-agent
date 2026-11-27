"""Versioned SQLite storage with per-source transactions and an OS writer lock."""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    select,
)
from sqlalchemy.exc import SQLAlchemyError

from career_intelligence.collection import CollectionPreview, Posting

metadata = MetaData()
schema = Table("schema_version", metadata, Column("version", Integer, primary_key=True))
runs = Table(
    "collection_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("started_at", String, nullable=False),
    Column("status", String, nullable=False),
)
source_runs = Table(
    "source_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("run_id", ForeignKey("collection_runs.id"), nullable=False),
    Column("snapshot_key", String, nullable=False, unique=True),
    Column("source_key", String, nullable=False),
    Column("scope_key", String, nullable=False),
    Column("status", String, nullable=False),
    Column("details", JSON, nullable=False),
)
jobs = Table(
    "jobs",
    metadata,
    Column("id", String, primary_key=True),
    Column("company_key", String, nullable=False),
    Column("first_seen", String, nullable=False),
    Column("last_seen", String, nullable=False),
    Column("revision", Integer, nullable=False),
)
aliases = Table(
    "source_postings",
    metadata,
    Column("id", String, primary_key=True),
    Column("job_id", ForeignKey("jobs.id"), nullable=False),
    Column("provider", String, nullable=False),
    Column("scope", String, nullable=False),
    Column("post_id", String, nullable=False),
    Column("canonical_url", String, index=True),
    Column("fingerprint", String, nullable=False),
    Column("last_seen", String, nullable=False),
    UniqueConstraint("provider", "scope", "post_id"),
)
revisions = Table(
    "job_revisions",
    metadata,
    Column("job_id", ForeignKey("jobs.id"), primary_key=True),
    Column("number", Integer, primary_key=True),
    Column("fingerprint", String, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("raw_record", JSON, nullable=False),
    Column("source_run_id", ForeignKey("source_runs.id"), nullable=False),
)
collisions = Table(
    "identity_collisions",
    metadata,
    Column("alias_id", ForeignKey("source_postings.id"), primary_key=True),
    Column("candidate_job_ids", JSON, nullable=False),
)


class StorageError(RuntimeError):
    """A safe operational diagnostic without SQL parameters or job content."""


def canonical_url(url: str | None) -> str | None:
    if url is None:
        return None
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"gh_src", "gclid", "fbclid"}
    ]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), "")
    )


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def fingerprint(post: Posting) -> str:
    payload = post.model_dump(
        mode="json",
        exclude={
            "source_provider",
            "source_scope",
            "source_post_id",
            "observed_at",
            "source_updated_at",
            "source_url",
            "source_status",
        },
    )
    payload["official_application_url"] = canonical_url(post.official_application_url)
    for key in (
        "technical_skills",
        "academic_requirements",
        "language_requirements",
        "required_qualifications",
        "preferred_qualifications",
    ):
        if payload[key] is not None:
            payload[key] = sorted(payload[key])
    return _digest({"normalization_version": 1, "payload": payload})


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise StorageError("Collection timestamps must include a timezone")
    return value.astimezone(UTC).isoformat()


@contextmanager
def _writer_lock(path: Path):
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise StorageError("Another collection writer is using this database") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _engine(path: Path, *, readonly: bool = False):
    def connect():
        connection = sqlite3.connect(
            path.as_uri() + "?mode=ro" if readonly else str(path),
            uri=readonly,
            timeout=0,
            isolation_level=None,
        )
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    engine = create_engine("sqlite://", creator=connect)

    @event.listens_for(engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    return engine


def _check_schema(connection):
    if list(connection.scalars(select(schema.c.version))) != [1]:
        raise StorageError("Unsupported database schema version")


class Store:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.engine = None

    def __enter__(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.lock = _writer_lock(self.path.with_name(self.path.name + ".lock"))
            self.lock.__enter__()
        except OSError:
            raise StorageError("Cannot acquire database writer lock") from None
        try:
            self.engine = _engine(self.path)
            with self.engine.begin() as connection:
                tables = inspect(connection).get_table_names()
                if tables:
                    if "schema_version" not in tables:
                        raise StorageError("Existing database has no recognized schema version")
                    _check_schema(connection)
                    if not set(metadata.tables).issubset(tables):
                        raise StorageError("Database schema is incomplete")
                else:
                    # Initial numbered migration, applied only to an empty database.
                    metadata.create_all(connection)
                    connection.execute(schema.insert().values(version=1))
                connection.execute(
                    runs.update().where(runs.c.status == "running").values(status="interrupted")
                )
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        if self.engine is not None:
            self.engine.dispose()
        self.lock.__exit__(*args)

    def ingest(self, preview: CollectionPreview) -> dict:
        run_id = str(uuid4())
        with self.engine.begin() as connection:
            connection.execute(
                runs.insert().values(
                    id=run_id,
                    started_at=datetime.now(UTC).isoformat(),
                    status="running",
                )
            )
        results = []
        for snapshot in preview.snapshots:
            details = snapshot.model_dump(mode="json")
            details["records"] = [
                {"post": post.model_dump(mode="json"), "raw": post.raw_record}
                for post in snapshot.records
            ]
            snapshot_key = _digest(
                {
                    "details": details,
                }
            )
            source_run_id = str(uuid4())
            try:
                with self.engine.begin() as connection:
                    previous = (
                        connection.execute(
                            select(source_runs).where(source_runs.c.snapshot_key == snapshot_key)
                        )
                        .mappings()
                        .first()
                    )
                    if previous and previous["status"] != "storage_failed":
                        results.append({"source_key": snapshot.source_key, "status": "replayed"})
                        continue
                    if previous:
                        connection.execute(
                            source_runs.delete().where(source_runs.c.id == previous["id"])
                        )
                    connection.execute(
                        source_runs.insert().values(
                            id=source_run_id,
                            run_id=run_id,
                            snapshot_key=snapshot_key,
                            source_key=snapshot.source_key,
                            scope_key=snapshot.scope_key,
                            status=snapshot.status,
                            details=details,
                        )
                    )
                    if snapshot.status == "failed" and snapshot.records:
                        raise StorageError("Failed snapshots cannot contain positive observations")
                    identities = set()
                    for post in snapshot.records:
                        identity = (post.source_provider, post.source_scope, post.source_post_id)
                        if identity in identities or snapshot.scope_key != (
                            f"{post.source_provider}:{post.source_scope}"
                        ):
                            raise StorageError("Inconsistent source snapshot identity")
                        identities.add(identity)
                        self._save_post(connection, post, source_run_id)
                results.append({"source_key": snapshot.source_key, "status": snapshot.status})
            except (SQLAlchemyError, StorageError):
                with self.engine.begin() as connection:
                    connection.execute(
                        source_runs.delete().where(
                            (source_runs.c.snapshot_key == snapshot_key)
                            & (source_runs.c.status == "storage_failed")
                        )
                    )
                    connection.execute(
                        source_runs.insert().values(
                            id=source_run_id,
                            run_id=run_id,
                            snapshot_key=snapshot_key,
                            source_key=snapshot.source_key,
                            scope_key=snapshot.scope_key,
                            status="storage_failed",
                            details={"code": "source_transaction_failed"},
                        )
                    )
                results.append({"source_key": snapshot.source_key, "status": "storage_failed"})
        status = "failed" if any(r["status"] == "storage_failed" for r in results) else "finished"
        with self.engine.begin() as connection:
            connection.execute(runs.update().where(runs.c.id == run_id).values(status=status))
        return {"run_id": run_id, "status": status, "sources": results}

    def _save_post(self, connection, post: Posting, source_run_id: str):
        identity = (
            (aliases.c.provider == post.source_provider)
            & (aliases.c.scope == post.source_scope)
            & (aliases.c.post_id == post.source_post_id)
        )
        alias = connection.execute(select(aliases).where(identity)).mappings().first()
        seen = _timestamp(post.observed_at)
        digest = fingerprint(post)
        url = canonical_url(post.official_application_url)
        candidate_ids = set()
        if alias:
            job = (
                connection.execute(select(jobs).where(jobs.c.id == alias["job_id"]))
                .mappings()
                .one()
            )
            if job["company_key"] != post.company_key:
                raise StorageError("Source identity changed company")
            if seen < alias["last_seen"]:
                return  # An out-of-order observation cannot overwrite more recent facts.
        else:
            candidates = []
            if url:
                candidates = (
                    connection.execute(
                        select(aliases, jobs.c.company_key)
                        .join(jobs)
                        .where(aliases.c.canonical_url == url)
                    )
                    .mappings()
                    .all()
                )
            candidate_ids = {row["job_id"] for row in candidates}
            compatible = (
                candidates
                and len(candidate_ids) == 1
                and all(
                    row["company_key"] == post.company_key
                    and (row["provider"], row["scope"]) != (post.source_provider, post.source_scope)
                    for row in candidates
                )
            )
            job_id = next(iter(candidate_ids)) if compatible else str(uuid4())
            if not compatible:
                connection.execute(
                    jobs.insert().values(
                        id=job_id,
                        company_key=post.company_key,
                        first_seen=seen,
                        last_seen=seen,
                        revision=0,
                    )
                )
            job = connection.execute(select(jobs).where(jobs.c.id == job_id)).mappings().one()
            alias = {
                "id": str(uuid4()),
                "job_id": job_id,
                "provider": post.source_provider,
                "scope": post.source_scope,
                "post_id": post.source_post_id,
                "canonical_url": url,
                "fingerprint": digest,
                "last_seen": seen,
            }
            connection.execute(aliases.insert().values(**alias))
            if candidate_ids and not compatible:
                connection.execute(
                    collisions.insert().values(
                        alias_id=alias["id"],
                        candidate_job_ids=sorted(candidate_ids),
                    )
                )
            alias = dict(alias, fingerprint=None)
        current_digest = connection.scalar(
            select(revisions.c.fingerprint).where(
                (revisions.c.job_id == job["id"]) & (revisions.c.number == job["revision"])
            )
        )
        if alias["fingerprint"] != digest and current_digest != digest and seen >= job["last_seen"]:
            number = job["revision"] + 1
            connection.execute(
                revisions.insert().values(
                    job_id=job["id"],
                    number=number,
                    fingerprint=digest,
                    payload=post.model_dump(mode="json"),
                    raw_record=post.raw_record,
                    source_run_id=source_run_id,
                )
            )
            connection.execute(jobs.update().where(jobs.c.id == job["id"]).values(revision=number))
        connection.execute(
            jobs.update()
            .where(jobs.c.id == job["id"])
            .values(
                first_seen=min(job["first_seen"], seen),
                last_seen=max(job["last_seen"], seen),
            )
        )
        connection.execute(
            aliases.update()
            .where(aliases.c.id == alias["id"])
            .values(
                fingerprint=digest,
                last_seen=seen,
                canonical_url=url,
            )
        )


def list_jobs(path: Path, *, limit: int = 100, offset: int = 0) -> list[dict]:
    if limit < 1 or offset < 0:
        raise StorageError("Job limit must be positive and offset nonnegative")
    if not path.is_file():
        raise StorageError("Database does not exist. Run collect with --persist first")
    engine = _engine(path.resolve(), readonly=True)
    try:
        with engine.connect() as connection:
            _check_schema(connection)
            statement = (
                select(jobs, revisions.c.payload)
                .join(
                    revisions,
                    (jobs.c.id == revisions.c.job_id) & (jobs.c.revision == revisions.c.number),
                )
                .order_by(jobs.c.first_seen, jobs.c.id)
                .limit(limit)
                .offset(offset)
            )
            return [dict(row) for row in connection.execute(statement).mappings()]
    finally:
        engine.dispose()
