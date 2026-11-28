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
    "failure",
    ["stale", "quote", "profile", "extra", "duplicate", "oversized", "kind", "nested_kind"],
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
    elif failure == "nested_kind":
        child = {**review["constraints"][0], "kind": ["PRIVATE_UNSUPPORTED_VALUE"]}
        review["constraints"] = [
            {"id": "group", "kind": "any_of", "source": child["source"], "conditions": [child]}
        ]
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


def test_compound_cli_accepts_experience_alternative_to_uncompleted_degree_without_writes(
    config_files,
    repository_root,
    capsys,
    tmp_path,
):
    settings, docs, write = config_files
    docs["profile"]["education"] = [
        {
            "institution": "Example",
            "degree": "MSc",
            "completed": False,
            "status_as_of": "2026-11-28",
            "evidence": ["synthetic enrollment record"],
        }
    ]
    docs["profile"]["experience"] = [
        {
            "employer": "Example",
            "role": "Engineer",
            "start": "2022-01-01",
            "end": "2026-01-01",
            "kind": "employment",
            "counts_as_professional_experience": True,
        }
    ]
    write("profile", docs["profile"])
    assert (
        main(
            [
                "collect",
                "--config",
                str(settings),
                "--fixture",
                str(repository_root / "tests/fixtures/greenhouse/eligibility_alternatives.json"),
                "--persist",
            ]
        )
        == 0
    )
    capsys.readouterr()
    database = load_configuration(settings).settings.database_path
    job = list_jobs(database)[0]
    args = ["jobs", "eligibility", "--config", str(settings), "--job-id", job["id"]]
    assert main([*args, "--template"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["schema_version"] == 2
    description = job["payload"]["description"]

    def quote(text):
        start = description.index(text)
        return {
            "text": text,
            "evidence": {"field": "description", "start": start, "end": start + len(text)},
        }

    document.update(
        {
            "reviewed_by": "synthetic-reviewer",
            "reviewed_on": "2026-11-28",
            "coverage": "complete",
            "candidate_assertions": {
                "complete_sections": ["education", "experience"],
                "work_locations": None,
            },
            "constraints": [
                {
                    "id": "education-or-experience",
                    "kind": "any_of",
                    "source": quote(description),
                    "conditions": [
                        {
                            "id": "degree",
                            "kind": "degree",
                            "source": quote("A completed MSc degree"),
                            "accepted_degrees": ["MSc"],
                            "completion": "completed",
                        },
                        {
                            "id": "experience",
                            "kind": "minimum_experience",
                            "minimum_years": 2,
                            "source": quote("at least 2 years of professional experience"),
                        },
                    ],
                }
            ],
        }
    )
    path = tmp_path / "compound-review.yml"
    path.write_text(json.dumps(document), encoding="utf-8")
    before = database.read_bytes()
    assert main([*args, "--requirements", str(path), "--as-of", "2026-11-28"]) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["status"] == "eligible" and result["schema_version"] == 2
    children = result["rules"][0]["conditions"]
    assert [child["outcome"] for child in children] == ["contradiction", "met"]
    assert not output.err and database.read_bytes() == before
