import json

import pytest
import yaml

from career_intelligence.cli import main
from career_intelligence.config import load_configuration
from career_intelligence.storage import list_jobs


def prepare(config_files, repository_root, capsys, tmp_path):
    settings, docs, write = config_files
    docs["profile"].update(skills=[{"name": "Python", "evidence": ["Synthetic course project"]}])
    docs["profile"]["preferences"]["objectives"] = ["Develop technical skills"]
    profile_path = write("profile", docs["profile"])
    fixture = repository_root / "tests/fixtures/greenhouse/matching.json"
    assert main(["collect", "--config", str(settings), "--fixture", str(fixture), "--persist"]) == 0
    capsys.readouterr()
    database = load_configuration(settings).settings.database_path
    job = list_jobs(database)[0]
    policy = yaml.safe_load(
        (repository_root / "conf/matching.example.yml").read_text(encoding="utf-8")
    )
    policy["weights"] = dict.fromkeys(policy["weights"], 0)
    policy["weights"].update(technical_skills=3, career_objectives=1, compensation=1)
    policy["minimum_coverage"] = 0.8
    policy_path = tmp_path / "policy.yml"
    policy_path.write_text(yaml.safe_dump(policy), encoding="utf-8")
    args = [
        "jobs",
        "match",
        "--config",
        str(settings),
        "--job-id",
        job["id"],
        "--policy",
        str(policy_path),
    ]
    assert main([*args, "--template"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["reviewed_by"] is None and document["reviewed_on"] is None
    assert set(document["assessments"].values()) == {None}
    description = job["payload"]["description"]
    quote = {
        "text": description,
        "evidence": {"field": "description", "start": 0, "end": len(description)},
    }
    document.update(reviewed_by="synthetic-reviewer", reviewed_on="2026-11-28")
    document["assessments"].update(
        {
            "technical_skills": {
                "level": 4,
                "confidence": 1,
                "rationale": "Synthetic Python task overlap",
                "source_evidence": [quote],
                "profile_refs": ["skills.0.name", "skills.0.evidence.0"],
                "gaps": [],
            },
            "career_objectives": {
                "level": 2,
                "confidence": 0.5,
                "rationale": "Synthetic partial objective support",
                "source_evidence": [quote],
                "profile_refs": ["preferences.objectives.0"],
            },
        }
    )
    review_path = tmp_path / "matching-review.yml"
    review_path.write_text(json.dumps(document), encoding="utf-8")
    return args, database, profile_path, policy_path, review_path, document


def test_matching_cli_reports_weighted_fit_and_unknown_pay_without_mutating_state(
    config_files, repository_root, capsys, tmp_path
):
    args, database, profile_path, policy_path, review_path, _ = prepare(
        config_files, repository_root, capsys, tmp_path
    )
    before = [path.read_bytes() for path in (database, profile_path, policy_path)]
    results = []
    for _ in range(2):
        assert main([*args, "--review", str(review_path)]) == 0
        output = capsys.readouterr()
        assert not output.err
        report = json.loads(output.out)
        assert report["status"] == "scored" and report["overall_score"] == 87.5
        assert report["coverage"] == 0.8 and report["weighted_confidence"] == 0.875
        assert report["dimensions"]["compensation"]["assessment"] is None
        assert report["dimensions"]["technical_skills"]["assessment"]["gaps"] == []
        assert report["dimensions"]["career_objectives"]["assessment"]["gaps"] is None
        results.append(report)
    assert results[0] == results[1]
    assert [path.read_bytes() for path in (database, profile_path, policy_path)] == before


@pytest.mark.parametrize(
    "failure",
    [
        "profile",
        "policy",
        "revision",
        "quote",
        "dimension",
        "null_fact",
        "duplicate",
        "oversized",
        "unsafe",
        "unreviewed",
        "unknown_omitted",
    ],
)
def test_matching_cli_refuses_stale_or_untrusted_reviews_with_redacted_errors_and_no_writes(
    failure, config_files, repository_root, capsys, tmp_path
):
    args, database, profile_path, policy_path, path, document = prepare(
        config_files, repository_root, capsys, tmp_path
    )
    if failure == "profile":
        data = yaml.safe_load(profile_path.read_text())
        data["skills"][0]["name"] = "PRIVATE_UNSUPPORTED_VALUE"
        profile_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    elif failure == "policy":
        data = yaml.safe_load(policy_path.read_text())
        data["rubrics"]["technical_skills"]["anchors"][0] = "PRIVATE_UNSUPPORTED_VALUE"
        policy_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    elif failure == "revision":
        document["revision"] = 99
    elif failure == "quote":
        document["assessments"]["technical_skills"]["source_evidence"][0]["text"] = (
            "PRIVATE_UNSUPPORTED_VALUE"
        )
    elif failure == "dimension":
        document["assessments"]["PRIVATE_UNSUPPORTED_VALUE"] = None
    elif failure == "null_fact":
        document["assessments"]["technical_skills"]["profile_refs"] = ["work_authorizations"]
    elif failure == "unreviewed":
        document["reviewed_by"] = None
    elif failure == "unknown_omitted":
        del document["assessments"]["compensation"]
    path.write_text(json.dumps(document), encoding="utf-8")
    if failure == "duplicate":
        path.write_text("schema_version: 1\nschema_version: 1\n", encoding="utf-8")
    elif failure == "oversized":
        path.write_bytes(b"x" * 1_048_577)
    elif failure == "unsafe":
        path.write_text("!!python/object:PRIVATE_UNSUPPORTED_VALUE {}", encoding="utf-8")
    before = [item.read_bytes() for item in (database, profile_path, policy_path)]
    assert main([*args, "--review", str(path)]) == 2
    output = capsys.readouterr()
    assert not output.out and "PRIVATE_UNSUPPORTED_VALUE" not in output.err
    assert json.loads(output.err)["event"] == "configuration_error"
    assert [item.read_bytes() for item in (database, profile_path, policy_path)] == before


@pytest.mark.parametrize(
    "content",
    [
        "weights: {PRIVATE_UNSUPPORTED_VALUE: 1}",
        "schema_version: 1\nschema_version: 1\n",
        "x" * 1_048_577,
    ],
)
def test_invalid_matching_policies_fail_safely(
    config_files, repository_root, capsys, tmp_path, content
):
    args, database, _, policy_path, path, _ = prepare(
        config_files, repository_root, capsys, tmp_path
    )
    policy_path.write_text(content, encoding="utf-8")
    before = database.read_bytes()
    assert main([*args, "--review", str(path)]) == 2
    output = capsys.readouterr()
    assert not output.out and "PRIVATE_UNSUPPORTED_VALUE" not in output.err
    assert database.read_bytes() == before


def test_matching_cli_can_review_a_selected_historical_revision_and_refuses_stale_latest(
    config_files, repository_root, capsys, tmp_path
):
    args, _, _, _, path, _ = prepare(config_files, repository_root, capsys, tmp_path)
    fixture = json.loads((repository_root / "tests/fixtures/greenhouse/matching.json").read_text())
    fixture["jobs"][0]["content"] += " New responsibility."
    fixture_path = tmp_path / "changed.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    settings = args[3]
    assert main(["collect", "--config", settings, "--fixture", str(fixture_path), "--persist"]) == 0
    capsys.readouterr()
    assert main([*args, "--review", str(path)]) == 2
    capsys.readouterr()
    assert main([*args, "--review", str(path), "--revision", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == 1
