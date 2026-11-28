import json

import pytest

from career_intelligence.cli import main
from career_intelligence.config import load_configuration
from career_intelligence.storage import list_jobs


def prepare(config_files, repository_root, capsys, tmp_path):
    settings, _, _ = config_files
    assert (
        main(
            [
                "collect",
                "--config",
                str(settings),
                "--fixture",
                str(repository_root / "tests/fixtures/greenhouse/eligibility.json"),
                "--persist",
            ]
        )
        == 0
    )
    capsys.readouterr()
    database = load_configuration(settings).settings.database_path
    job = next(row for row in list_jobs(database) if row["payload"]["title"] == "Research Engineer")
    args = ["jobs", "eligibility", "--config", str(settings), "--job-id", job["id"]]
    assert main([*args, "--template"]) == 0
    template = json.loads(capsys.readouterr().out)
    assert template["reviewed_by"] is None and template["coverage"] == "partial"
    text = "Apply by 2028-06-30."
    review = {
        **template,
        "reviewed_by": "synthetic-reviewer",
        "reviewed_on": "2026-11-28",
        "coverage": "complete",
        "constraints": [
            {
                "id": "deadline",
                "kind": "application_deadline",
                "cutoff": "2028-06-30",
                "source": {
                    "text": text,
                    "evidence": {"field": "description", "start": 0, "end": len(text)},
                },
            }
        ],
    }
    path = tmp_path / "review.yml"
    path.write_text(json.dumps(review), encoding="utf-8")
    return args, database, path, review


def test_reviewed_assessment_is_read_only_deterministic_and_does_not_change_filtering(
    config_files,
    repository_root,
    capsys,
    tmp_path,
):
    args, database, path, _ = prepare(config_files, repository_root, capsys, tmp_path)
    before = database.read_bytes()
    for _ in range(2):
        assert main([*args, "--requirements", str(path), "--as-of", "2028-07-01"]) == 0
        output = capsys.readouterr()
        assert not output.err
        report = json.loads(output.out)
        assert report["status"] == "not_eligible" and report["scope"] == "reviewed_constraints_only"
        assert report["rules"][0]["requirement"] == {"cutoff": "2028-06-30"}
        if _ == 0:
            first = report
        else:
            assert report == first
    assert database.read_bytes() == before
    assert main(["jobs", "list", *args[2:4]]) == 0


@pytest.mark.parametrize(
    "failure", ["stale", "quote", "profile", "extra", "duplicate", "oversized", "kind"]
)
def test_invalid_or_stale_reviews_are_redacted_and_do_not_write_storage(
    failure,
    config_files,
    repository_root,
    capsys,
    tmp_path,
):
    args, database, path, review = prepare(config_files, repository_root, capsys, tmp_path)
    if failure == "stale":
        review["revision"] = 99
    elif failure == "quote":
        review["constraints"][0]["source"]["text"] = "PRIVATE_UNSUPPORTED_VALUE"
    elif failure == "profile":
        review["profile_hash"] = "b" * 64
    elif failure == "extra":
        review["extra"] = "PRIVATE_UNSUPPORTED_VALUE"
    elif failure == "kind":
        review["constraints"][0]["kind"] = "PRIVATE_UNSUPPORTED_VALUE"
    path.write_text(json.dumps(review), encoding="utf-8")
    if failure == "duplicate":
        path.write_text("schema_version: 1\nschema_version: 1\n", encoding="utf-8")
    elif failure == "oversized":
        path.write_bytes(b"x" * 1_048_577)
    before = database.read_bytes()
    assert main([*args, "--requirements", str(path), "--as-of", "2028-07-01"]) == 2
    output = capsys.readouterr()
    assert not output.out and "PRIVATE_UNSUPPORTED_VALUE" not in output.err
    assert json.loads(output.err)["event"] == "configuration_error"
    assert database.read_bytes() == before


@pytest.mark.parametrize("as_of", [None, "2028", "2028-02-30", "20280701"])
def test_assessment_requires_an_explicit_exact_date(as_of, config_files):
    settings, _, _ = config_files
    args = [
        "jobs",
        "eligibility",
        "--config",
        str(settings),
        "--job-id",
        "x",
        "--requirements",
        "unused",
    ]
    if as_of is not None:
        args += ["--as-of", as_of]
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
