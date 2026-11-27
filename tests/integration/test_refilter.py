import json

import pytest
from sqlalchemy import func, select

from career_intelligence.cli import main
from career_intelligence.config import load_configuration
from career_intelligence.filtering import configuration_hash
from career_intelligence.storage import (
    StorageError,
    Store,
    filter_decisions,
    jobs,
    list_jobs,
    revisions,
    schema,
)


def test_refilter_updates_decisions_without_touching_source_history(
    config_files,
    repository_root,
    capsys,
):
    path, docs, write = config_files
    fixture = repository_root / "tests/fixtures/greenhouse/initial.json"
    assert main(["collect", "--config", str(path), "--fixture", str(fixture), "--persist"]) == 0
    capsys.readouterr()
    configuration = load_configuration(path)
    db = configuration.settings.database_path
    with Store(db) as store:
        with store.engine.connect() as connection:
            original = connection.execute(select(revisions)).all()
        for _ in range(2):
            summary = store.refilter(configuration.profile, configuration.settings.filters)
            assert summary["job_count"] == 2
        with store.engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(filter_decisions)) == 2

    docs["profile"]["preferences"]["target_roles"] = ["Research Engineer", "Data Scientist"]
    write("profile", docs["profile"])
    configuration = load_configuration(path)
    config_hash = configuration_hash(configuration.profile, configuration.settings.filters)
    with pytest.raises(StorageError, match="stale"):
        list_jobs(db, config_hash=config_hash, decisions={"keep", "review"})
    assert all(row["filter_decision"] is None for row in list_jobs(db, config_hash=config_hash))

    assert main(["jobs", "refilter", "--config", str(path)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["job_count"] == 2
    assert (
        main(["jobs", "list", "--config", str(path), "--decision", "keep", "--format", "json"]) == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert len(output["jobs"]) == 2
    assert output["jobs"][0]["filter_decision"]["config_hash"] == config_hash
    assert main(["jobs", "list", "--config", str(path), "--format", "table"]) == 0
    table = capsys.readouterr().out
    assert "Decision" in table
    assert "https://example.com/careers?gh_jid=101" in table
    with Store(db) as store:
        with store.engine.connect() as connection:
            assert connection.execute(select(revisions)).all() == original
            assert connection.scalar(select(func.count()).select_from(jobs)) == 2
            assert connection.scalar(select(func.count()).select_from(filter_decisions)) == 4


def test_migrate_schema_two(config_files):
    settings, _, _ = config_files
    db = load_configuration(settings).settings.database_path
    with Store(db) as store:
        with store.engine.begin() as connection:
            filter_decisions.drop(connection)
            connection.execute(schema.update().values(version=2))
    with Store(db) as store:
        with store.engine.connect() as connection:
            assert connection.scalar(select(schema.c.version)) == 3
            assert connection.scalar(select(func.count()).select_from(filter_decisions)) == 0


def test_location_order_and_decision_filter_precede_pagination(config_files, capsys):
    path, docs, write = config_files
    docs["profile"]["preferences"]["locations"] = ["Example City", "Other City"]
    docs["settings"]["filters"] = {"excluded_title_seniority": ["Senior"]}
    write("profile", docs["profile"])
    write("settings", docs["settings"])
    fixture = path.parent / "ranked.json"
    fixture.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": 1,
                        "title": "Machine Learning Engineer",
                        "location": {"name": "Other City"},
                    },
                    {
                        "id": 2,
                        "title": "Senior Machine Learning Engineer",
                        "location": {"name": "Example City"},
                    },
                    {
                        "id": 3,
                        "title": "Machine Learning Engineer",
                        "location": {"name": "Example City"},
                    },
                ],
                "meta": {"total": 3},
            }
        ),
        encoding="utf-8",
    )
    assert main(["collect", "--config", str(path), "--fixture", str(fixture), "--persist"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "jobs",
                "list",
                "--config",
                str(path),
                "--decision",
                "keep",
                "--limit",
                "1",
            ]
        )
        == 0
    )
    row = json.loads(capsys.readouterr().out)["jobs"][0]
    assert row["payload"]["source_post_id"] == "3"
    assert row["filter_decision"]["location_rank"] == 0
    assert main(["jobs", "list", "--config", str(path), "--decision", "exclude"]) == 0
    rows = json.loads(capsys.readouterr().out)["jobs"]
    assert [row["payload"]["source_post_id"] for row in rows] == ["2"]
