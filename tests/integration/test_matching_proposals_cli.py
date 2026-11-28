import json

import pytest

from career_intelligence.cli import main
from career_intelligence.config import load_configuration
from career_intelligence.storage import list_jobs


def prepare(config_files, repository_root, capsys):
    settings, docs, write = config_files
    docs["profile"]["skills"] = [{"name": "Python", "evidence": ["Synthetic course project"]}]
    profile_path = write("profile", docs["profile"])
    assert (
        main(
            [
                "collect",
                "--config",
                str(settings),
                "--fixture",
                str(repository_root / "tests/fixtures/greenhouse/matching.json"),
                "--persist",
            ]
        )
        == 0
    )
    capsys.readouterr()
    database = load_configuration(settings).settings.database_path
    job = list_jobs(database)[0]
    args = [
        "jobs",
        "match",
        "--config",
        str(settings),
        "--job-id",
        job["id"],
        "--policy",
        str(repository_root / "conf/matching.example.yml"),
    ]
    path = repository_root / "tests/fixtures/matching/proposal.json"
    return args, database, profile_path, path


def test_proposal_cli_stays_read_only_unreviewed_and_separate_from_scoring(
    config_files, repository_root, capsys, tmp_path
):
    args, database, profile_path, path = prepare(config_files, repository_root, capsys)
    before = [item.read_bytes() for item in (database, profile_path)]
    reports = []
    for _ in range(2):
        assert main([*args, "--response-fixture", str(path)]) == 0
        output = capsys.readouterr()
        assert not output.err
        result = json.loads(output.out)
        assert result["status"] == "complete" and result["review_required"]
        assert result["provider"] == "fixture" and result["fixture_reads"] == 1
        assert "overall_score" not in result
        assert result["draft_review"]["reviewed_by"] is None
        assert result["draft_review"]["reviewed_on"] is None
        reports.append(result)
    assert reports[0] == reports[1]
    for data in (reports[0], reports[0]["draft_review"]):
        unreviewed = tmp_path / "unreviewed.yml"
        unreviewed.write_text(json.dumps(data), encoding="utf-8")
        assert main([*args, "--review", str(unreviewed)]) == 2
        output = capsys.readouterr()
        assert not output.out and json.loads(output.err)["event"] == "configuration_error"
    # Synthetic test annotation only; adding metadata does not authenticate human review.
    reviewed = reports[0]["draft_review"]
    reviewed.update(reviewed_by="synthetic-reviewer", reviewed_on="2026-11-28")
    path = tmp_path / "reviewed.yml"
    path.write_text(json.dumps(reviewed), encoding="utf-8")
    assert main([*args, "--review", str(path)]) == 0
    scored = json.loads(capsys.readouterr().out)
    assert scored["status"] == "insufficient_coverage" and scored["overall_score"] is None
    assert scored["dimensions"]["technical_skills"]["score"] == 75
    assert [item.read_bytes() for item in (database, profile_path)] == before


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("quote", "ungrounded_evidence"),
        ("null", "ungrounded_evidence"),
        ("reviewer", "invalid_output"),
        ("duplicate", "invalid_output"),
        ("oversized", "output_limit"),
        ("missing", "fixture_unavailable"),
        ("input_limit", "input_limit"),
    ],
)
def test_cli_rejects_entire_failed_proposal_safely_and_leaves_discovery_available(
    failure, expected, config_files, repository_root, capsys, tmp_path
):
    args, database, profile_path, original_path = prepare(config_files, repository_root, capsys)
    data = json.loads(original_path.read_text())
    if failure == "quote":
        data["assessments"]["technical_skills"]["source_evidence"][0]["text"] = (
            "PRIVATE_UNSUPPORTED_VALUE"
        )
    elif failure == "null":
        data["assessments"]["technical_skills"]["profile_refs"] = ["work_authorizations"]
    elif failure == "reviewer":
        data["reviewed_by"] = "PRIVATE_UNSUPPORTED_VALUE"
    path = tmp_path / "PRIVATE_RESPONSE_PATH.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    if failure == "duplicate":
        path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    elif failure == "oversized":
        path.write_bytes(b"x" * 131_073)
    elif failure in ("input_limit", "missing"):
        path = tmp_path / "DO_NOT_READ.json"
    if failure == "input_limit":
        settings, docs, write = config_files
        docs["settings"]["matching_proposals"] = {"max_input_bytes": 1}
        write("settings", docs["settings"])
    before = [item.read_bytes() for item in (database, profile_path)]
    assert main([*args, "--response-fixture", str(path)]) == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["status"] == expected and result["draft_review"] is None
    assert result["fixture_reads"] == (0 if failure == "input_limit" else 1)
    assert json.loads(output.err) == {"event": "matching_proposal_failed", "status": expected}
    assert "PRIVATE_UNSUPPORTED_VALUE" not in output.out + output.err
    assert "PRIVATE_RESPONSE_PATH" not in output.out + output.err
    assert [item.read_bytes() for item in (database, profile_path)] == before
    assert main(["jobs", "list", "--config", args[3]]) == 0
    assert json.loads(capsys.readouterr().out)["jobs"]


def test_matching_replay_limits_are_independent_of_job_extraction_limits(
    config_files, repository_root, capsys
):
    args, _, _, path = prepare(config_files, repository_root, capsys)
    _, docs, write = config_files
    docs["settings"]["extraction"] = {"max_input_bytes": 1, "max_output_bytes": 1}
    write("settings", docs["settings"])
    assert main([*args, "--response-fixture", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "complete"


def test_historical_proposals_bind_the_selected_revision_and_keep_the_profile_local(
    config_files, repository_root, capsys, tmp_path, monkeypatch
):
    args, database, profile_path, path = prepare(config_files, repository_root, capsys)

    # Any accidental use of the available local model adapter must fail this replay.
    def forbidden(*args, **kwargs):
        raise AssertionError("Replay must not instantiate Ollama")

    monkeypatch.setattr("career_intelligence.cli.OllamaProvider", forbidden)
    source = json.loads((repository_root / "tests/fixtures/greenhouse/matching.json").read_text())
    source["jobs"][0]["content"] = "Responsibilities changed completely."
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(source), encoding="utf-8")
    assert main(["collect", "--config", args[3], "--fixture", str(changed), "--persist"]) == 0
    capsys.readouterr()
    before = [item.read_bytes() for item in (database, profile_path)]
    assert main([*args, "--response-fixture", str(path)]) == 1
    capsys.readouterr()
    assert main([*args, "--response-fixture", str(path), "--revision", "1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["revision"] == result["draft_review"]["revision"] == 1
    assert [item.read_bytes() for item in (database, profile_path)] == before


@pytest.mark.parametrize("switch", ["--review", "--template", "--ollama"])
def test_proposal_mode_cannot_be_combined_with_scoring_or_live_inference(
    switch, config_files, repository_root, capsys
):
    args, _, _, path = prepare(config_files, repository_root, capsys)
    suffix = [switch, "unused"] if switch == "--review" else [switch]
    with pytest.raises(SystemExit) as error:
        main([*args, "--response-fixture", str(path), *suffix])
    assert error.value.code == 2
