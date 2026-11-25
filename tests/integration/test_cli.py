"""CLI behavior across actual process boundaries, without credentials or network."""

import json
import os
import subprocess
import sys

import pytest

from career_intelligence.cli import main


def run_cli(*args, cwd):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OPENAI_", "ANTHROPIC_", "TELEGRAM_", "OLLAMA_"))
    }
    env.pop("PYTHONPATH", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "career_intelligence", *args],
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=15,
    )


def test_help_and_version_work_without_config_or_credentials(tmp_path):
    help_result = run_cli("--help", cwd=tmp_path)
    assert help_result.returncode == 0
    assert "config" in help_result.stdout
    result = run_cli("--version", cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "career-intelligence 0.1.0"
    assert not result.stderr
    assert not list(tmp_path.iterdir())


def test_process_validation_from_another_directory(config_files, tmp_path):
    path, _, _ = config_files
    result = run_cli("config", "validate", "--config", str(path), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "event": "configuration_valid",
        "schema_version": 1,
        "company_count": 1,
        "enabled_source_count": 0,
    }
    assert not result.stderr
    assert not (tmp_path / "state").exists()


def test_validation_counts_enabled_sources_without_printing_profile(config_files, capsys):
    path, docs, write = config_files
    company = docs["companies"]["companies"][0]
    company.pop("disabled_reason")
    company["sources"] = [
        {
            "key": "example-source",
            "provider": "greenhouse",
            "board_token": "example",
            "enabled": True,
            "official_careers_url": "https://example.com/careers",
        }
    ]
    write("companies", docs["companies"])
    assert main(["config", "validate", "--config", str(path)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["enabled_source_count"] == 1
    assert "Machine Learning Engineer" not in captured.out
    assert not captured.err


def test_validation_failure_is_json_on_stderr_without_input_values(config_files, tmp_path):
    path, docs, write = config_files
    docs["profile"]["private_typo"] = "SENSITIVE_VALUE"
    write("profile", docs["profile"])
    result = run_cli("config", "validate", "--config", str(path), cwd=tmp_path)
    assert result.returncode == 2
    assert not result.stdout
    error = json.loads(result.stderr)
    assert error["event"] == "configuration_error"
    assert "private_typo" in error["message"]
    assert "SENSITIVE_VALUE" not in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("args", [[], ["collect"], ["config", "validate"]])
def test_invalid_invocations_have_exit_code_two(args, tmp_path):
    result = run_cli(*args, cwd=tmp_path)
    assert result.returncode == 2
    assert not result.stdout
    assert "usage:" in result.stderr


def test_import_has_no_network_or_file_side_effects(tmp_path):
    script = """
import socket
import sqlite3
import yaml
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError('I/O attempted during import')
original_open = Path.open
def read_only_open(self, mode='r', *args, **kwargs):
    if any(flag in mode for flag in 'wax+'):
        forbidden()
    return original_open(self, mode, *args, **kwargs)
socket.socket.connect = forbidden
sqlite3.connect = forbidden
yaml.load = forbidden
Path.open = read_only_open
import career_intelligence
import career_intelligence.cli
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    assert not result.stderr
    assert not list(tmp_path.iterdir())


def test_git_ignores_private_runtime_data_but_keeps_examples(repository_root):
    private_paths = [
        ".env",
        ".env.production",
        "conf/profile.local.yml",
        "conf/private/candidate.yml",
        "cv/candidate.pdf",
        "resumes/cv.docx",
        "data/jobs.sqlite3",
        "jobs.db-wal",
        "logs/run.jsonl",
        "payloads/source.json",
        ".venv/pyvenv.cfg",
        ".uv-cache/state",
    ]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-z", "--stdin"],
        input="\0".join(private_paths).encode("utf-8") + b"\0",
        cwd=repository_root,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.decode("utf-8").rstrip("\0").split("\0")) == set(private_paths)
    for public_path in [".env.example", "conf/profile.example.yml", "uv.lock"]:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", public_path],
            cwd=repository_root,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 1, public_path
