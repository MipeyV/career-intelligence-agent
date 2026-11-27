import asyncio
import builtins
import json

import pytest
from pydantic import ValidationError

from career_intelligence.cli import main
from career_intelligence.config import ExtractionLimits, OllamaSettings, load_configuration
from career_intelligence.extraction import ProviderNotInstalledError, ProviderRequest
from career_intelligence.ollama_provider import OllamaProvider


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://localhost:11434",
        "http://192.168.1.1:11434",
        "http://127.0.0.1.evil.test",
        "http://user:secret@127.0.0.1",
        "http://127.0.0.1/api",
        "http://127.0.0.1?token=secret",
        "http://127.0.0.1#x",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://127.0.0.1\\evil",
    ],
)
def test_only_plain_loopback_ip_endpoints_are_accepted(url):
    with pytest.raises(ValidationError):
        OllamaSettings(model="example:local", base_url=url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434/", "http://[::1]:11434"])
def test_supported_loopback_endpoints(url):
    assert OllamaSettings(model="example:local", base_url=url).base_url == url.rstrip("/")


@pytest.mark.parametrize(
    "name", ["example:cloud", "example-cloud", "http://host/model", "a\nb", ""]
)
def test_model_names_cannot_enable_cloud_or_include_arbitrary_text(name):
    with pytest.raises(ValidationError):
        OllamaSettings(model=name)


def test_ollama_is_unconfigured_by_default_and_requires_explicit_local_model(config_files):
    settings, _, _ = config_files
    assert load_configuration(settings).settings.ollama is None


def test_ollama_requires_configuration_before_reading_or_creating_storage(config_files, capsys):
    settings, _, _ = config_files
    assert main(["jobs", "extract", "--config", str(settings), "--job-id", "x", "--ollama"]) == 2
    output = capsys.readouterr()
    assert json.loads(output.err)["event"] == "configuration_error"
    assert not load_configuration(settings).settings.database_path.exists()


def test_missing_extra_does_not_require_imports_during_startup(monkeypatch):
    original_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith(("langchain", "langsmith")):
            raise ImportError("optional packages unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    provider = OllamaProvider(OllamaSettings(model="example:local"), ExtractionLimits())
    with pytest.raises(ProviderNotInstalledError):
        asyncio.run(provider.generate(ProviderRequest("system", "{}", "{}", 10)))


def test_extraction_mode_is_explicit_and_mutually_exclusive(config_files, capsys):
    settings, _, _ = config_files
    args = ["jobs", "extract", "--config", str(settings), "--job-id", "example"]
    for flags in ([], ["--ollama", "--response-fixture", "unused.json"]):
        with pytest.raises(SystemExit) as error:
            main([*args, *flags])
        assert error.value.code == 2
        capsys.readouterr()


def test_invalid_ollama_config_redacts_values(config_files, capsys):
    settings, docs, write = config_files
    docs["settings"]["ollama"] = {"model": "example:local", "base_url": "http://SECRET@bad"}
    write("settings", docs["settings"])
    assert main(["config", "validate", "--config", str(settings)]) == 2
    output = capsys.readouterr()
    assert not output.out and "SECRET" not in output.err
    assert json.loads(output.err)["event"] == "configuration_error"
