"""Offline tests: any attempt to open a network connection fails immediately."""

import socket
from pathlib import Path

import pytest
import yaml


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network access is forbidden in offline tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def config_files(tmp_path):
    directory = tmp_path / "configuration"
    directory.mkdir()
    profile = {
        "schema_version": 1,
        "preferences": {
            "availability": "2028-10",
            "target_roles": ["Machine Learning Engineer"],
            "compensation": {"currency": "EUR", "target_annual_total": 100000},
        },
    }
    companies = {
        "schema_version": 1,
        "companies": [
            {
                "key": "example-company",
                "name": "Example Company",
                "category": "big-tech-ai",
                "disabled_reason": "Not yet verified",
            }
        ],
    }
    settings = {
        "schema_version": 1,
        "profile_file": "profile.yml",
        "companies_file": "companies.yml",
        "database_path": "../state/jobs.sqlite3",
    }
    documents = {"settings": settings, "profile": profile, "companies": companies}

    def write(name, data):
        path = directory / f"{name}.yml"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return path

    for name, data in documents.items():
        write(name, data)
    return directory / "settings.yml", documents, write


@pytest.fixture
def repository_root():
    return Path(__file__).resolve().parents[1]
