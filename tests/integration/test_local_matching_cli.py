import builtins
import json

import pytest

from career_intelligence.cli import main
from career_intelligence.config import load_configuration
from career_intelligence.storage import list_jobs


def prepare(config_files, repository_root, capsys):
    settings, docs, write = config_files
    docs["settings"]["ollama"] = {"model": "example:local"}
    write("settings", docs["settings"])
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
    return args, database, profile_path


def test_default_consent_is_closed_before_model_creation_and_database_is_unchanged(
    config_files, repository_root, capsys, monkeypatch
):
    args, database, profile_path = prepare(config_files, repository_root, capsys)

    def forbidden(*args, **kwargs):
        raise AssertionError("Model must not be constructed")

    monkeypatch.setattr("career_intelligence.matching_proposals.OllamaProvider", forbidden)
    before = [path.read_bytes() for path in (database, profile_path)]
    assert main([*args, "--ollama"]) == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["status"] == "profile_sharing_disabled" and result["provider_attempts"] == 0
    assert result["input_sha256"] is None and result["draft_review"] is None
    assert "Synthetic course project" not in output.out + output.err
    assert [path.read_bytes() for path in (database, profile_path)] == before
    assert main(["jobs", "list", "--config", args[3]]) == 0


@pytest.mark.parametrize("fields", [["skills"], ["skills.99.name"]])
def test_invalid_selection_refuses_cli_call_without_disclosing_values(
    fields, config_files, repository_root, capsys, monkeypatch
):
    args, database, _ = prepare(config_files, repository_root, capsys)
    _, docs, write = config_files
    docs["settings"]["matching_local"] = {"share_profile": True, "profile_fields": fields}
    write("settings", docs["settings"])

    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid selection must not call a model")

    monkeypatch.setattr("career_intelligence.matching_proposals.OllamaProvider", forbidden)
    before = database.read_bytes()
    assert main([*args, "--ollama"]) == 2
    output = capsys.readouterr()
    assert not output.out and json.loads(output.err)["event"] == "configuration_error"
    assert "Synthetic course project" not in output.err and database.read_bytes() == before


def test_missing_extra_fails_safely_and_keeps_discovery_usable(
    config_files, repository_root, capsys, monkeypatch
):
    args, database, _ = prepare(config_files, repository_root, capsys)
    _, docs, write = config_files
    docs["settings"]["matching_local"] = {
        "share_profile": True,
        "profile_fields": ["skills.0.name", "skills.0.evidence.0"],
    }
    write("settings", docs["settings"])
    original = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith(("langchain", "langsmith")):
            raise ImportError("PRIVATE_IMPORT_ERROR")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    before = database.read_bytes()
    assert main([*args, "--ollama"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "provider_not_installed"
    assert "PRIVATE_IMPORT_ERROR" not in output.out + output.err
    assert database.read_bytes() == before
    assert main(["jobs", "list", "--config", args[3]]) == 0


def test_missing_model_configuration_is_refused_before_storage_access(config_files, capsys):
    settings, _, _ = config_files
    assert (
        main(
            [
                "jobs",
                "match",
                "--config",
                str(settings),
                "--job-id",
                "unused",
                "--policy",
                "unused",
                "--ollama",
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert json.loads(output.err)["event"] == "configuration_error"
    assert not load_configuration(settings).settings.database_path.exists()


def test_configured_sharing_does_not_make_fixture_replay_call_a_model(
    config_files, repository_root, capsys, monkeypatch
):
    args, _, _ = prepare(config_files, repository_root, capsys)
    _, docs, write = config_files
    docs["settings"]["matching_local"] = {
        "share_profile": True,
        "profile_fields": ["skills.0.name"],
    }
    write("settings", docs["settings"])

    def forbidden(*args, **kwargs):
        raise AssertionError("Replay must remain local")

    monkeypatch.setattr("career_intelligence.matching_proposals.OllamaProvider", forbidden)
    assert (
        main(
            [
                *args,
                "--response-fixture",
                str(repository_root / "tests/fixtures/matching/proposal.json"),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["provider"] == "fixture" and result["profile_fields"] is None
