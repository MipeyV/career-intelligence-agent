import json
from datetime import timedelta

import pytest

from career_intelligence.cli import main
from career_intelligence.collection import CollectionPreview, CollectionSnapshot, Posting
from career_intelligence.config import load_configuration
from career_intelligence.storage import StorageError, Store, get_revision, list_changes, list_jobs


def persisted(config_files, repository_root, capsys):
    settings, _, _ = config_files
    assert (
        main(
            [
                "collect",
                "--config",
                str(settings),
                "--fixture",
                str(repository_root / "tests/fixtures/greenhouse/initial.json"),
                "--persist",
            ]
        )
        == 0
    )
    capsys.readouterr()
    path = load_configuration(settings).settings.database_path
    job = next(row for row in list_jobs(path) if row["payload"]["title"] == "Research Engineer")
    return settings, path, job


def test_offline_cli_extraction_leaves_database_unchanged(config_files, repository_root, capsys):
    settings, path, job = persisted(config_files, repository_root, capsys)
    before = path.read_bytes()
    assert (
        main(
            [
                "jobs",
                "extract",
                "--config",
                str(settings),
                "--job-id",
                job["id"],
                "--response-fixture",
                str(repository_root / "tests/fixtures/extraction/initial-response.json"),
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert not output.err
    result = json.loads(output.out)
    assert result["status"] == "complete" and result["provider"] == "fixture"
    assert result["revision"] == 1 and result["job_id"] == job["id"]
    assert result["extraction"]["responsibilities"]["items"][0]["text"] == "Build models & tools."
    assert result["extraction"]["publication_date"] is None
    assert path.read_bytes() == before


def test_historical_revision_is_selected_explicitly_and_latest_proof_cannot_reuse_old_text(
    config_files,
    repository_root,
    capsys,
):
    settings, path, job = persisted(config_files, repository_root, capsys)
    old = get_revision(path, job["id"])
    post = Posting.model_validate({**old["payload"], "raw_record": {}})
    stamp = post.observed_at + timedelta(hours=1)
    updated = post.model_copy(update={"description": "Updated role.", "observed_at": stamp})
    snapshot = CollectionSnapshot(
        source_key="fixture-board",
        scope_key=f"{post.source_provider}:{post.source_scope}",
        started_at=stamp,
        completed_at=stamp,
        status="complete",
        records=[updated],
    )
    with Store(path) as store:
        store.ingest(CollectionPreview(mode="fixture", snapshots=[snapshot]))
    latest = get_revision(path, job["id"])
    assert latest["number"] == 2 and old["number"] == 1
    assert "raw_record" not in latest
    before = list_changes(path)
    args = [
        "jobs",
        "extract",
        "--config",
        str(settings),
        "--job-id",
        job["id"],
        "--response-fixture",
        str(repository_root / "tests/fixtures/extraction/initial-response.json"),
    ]
    assert main(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["revision"] == 2 and result["status"] == "ungrounded_evidence"
    assert result["extraction"] is None
    assert main([*args, "--revision", "1"]) == 0
    historic = json.loads(capsys.readouterr().out)
    assert historic["revision_fingerprint"] == old["fingerprint"]
    assert historic["input_sha256"] != result["input_sha256"]
    assert list_changes(path) == before


@pytest.mark.parametrize(
    "kind, expected",
    [
        ("missing", "provider_unavailable"),
        ("oversized", "output_limit"),
        ("invalid-utf8", "invalid_output"),
        ("invalid-json", "invalid_output"),
    ],
)
def test_invalid_response_fixtures_are_safe_and_keep_jobs_available(
    kind,
    expected,
    config_files,
    repository_root,
    capsys,
    tmp_path,
):
    settings, path, job = persisted(config_files, repository_root, capsys)
    fixture = tmp_path / "private-response.json"
    if kind == "oversized":
        fixture.write_bytes(b"x" * 131_073)
    elif kind == "invalid-utf8":
        fixture.write_bytes(b"\xff")
    elif kind == "invalid-json":
        fixture.write_text('{"private":"DO_NOT_ECHO"', encoding="utf-8")
    before = path.read_bytes()
    assert (
        main(
            [
                "jobs",
                "extract",
                "--config",
                str(settings),
                "--job-id",
                job["id"],
                "--response-fixture",
                str(fixture),
            ]
        )
        == 1
    )
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == expected
    assert json.loads(output.err) == {"event": "extraction_failed", "status": expected}
    assert "DO_NOT_ECHO" not in output.out + output.err
    assert "private-response" not in output.out + output.err
    assert path.read_bytes() == before
    assert main(["jobs", "list", "--config", str(settings)]) == 0


def test_unknown_job_or_revision_is_refused_without_creating_storage(
    config_files,
    repository_root,
    capsys,
):
    settings, path, job = persisted(config_files, repository_root, capsys)
    for job_id, number in [(job["id"], 0), (job["id"], 99), ("unknown", None)]:
        with pytest.raises(StorageError):
            get_revision(path, job_id, number=number)
    missing = path.parent / "missing.sqlite3"
    with pytest.raises(StorageError):
        get_revision(missing, job["id"])
    assert not missing.exists()
