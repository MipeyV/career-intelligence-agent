"""Configuration safety, unknown facts, identity and validation contracts."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from career_intelligence.config import (
    MAX_CONFIG_BYTES,
    CandidateProfile,
    CompanyWatchlist,
    ConfigurationError,
    Source,
    load_configuration,
)


def enabled_source(**updates):
    return {
        "key": "example-board",
        "provider": "greenhouse",
        "board_token": "example-board",
        "enabled": True,
        "official_careers_url": "https://example.com/careers",
        **updates,
    }


def test_relative_paths_are_based_on_settings_and_create_no_state(
    config_files, monkeypatch, tmp_path
):
    path, _, _ = config_files
    monkeypatch.chdir(tmp_path)
    loaded = load_configuration(path)
    assert loaded.settings.profile_file == path.parent / "profile.yml"
    assert loaded.settings.database_path == tmp_path / "state" / "jobs.sqlite3"
    assert not (tmp_path / "state").exists()
    assert loaded.profile.languages is None
    assert loaded.profile.education is None
    assert loaded.profile.skills is None
    assert loaded.profile.work_authorizations is None
    assert loaded.profile.preferences.compensation.fallback_annual_total is None


@pytest.mark.parametrize("precision", ["2028", "2028-02", "2028-02-29"])
def test_dates_keep_precision(config_files, precision):
    path, docs, write = config_files
    docs["profile"]["preferences"]["availability"] = precision
    docs["profile"]["education"] = [
        {
            "institution": "Example Institute",
            "degree": "MSc",
            "expected_graduation": precision,
        }
    ]
    write("profile", docs["profile"])
    loaded = load_configuration(path)
    assert loaded.profile.preferences.availability == precision
    assert loaded.profile.education[0].expected_graduation == precision


def test_unquoted_iso_dates_and_unknown_vs_empty_lists(config_files):
    path, _, _ = config_files
    profile = path.parent / "profile.yml"
    profile.write_text(
        "schema_version: 1\nlanguages: []\npreferences:\n  availability: 2028-02-29\n"
        "  target_roles: [Research Engineer]\n  compensation:\n    currency: EUR\n",
        encoding="utf-8",
    )
    loaded = load_configuration(path)
    assert loaded.profile.preferences.availability == "2028-02-29"
    assert loaded.profile.languages == []
    assert loaded.profile.work_authorizations is None


@pytest.mark.parametrize("value", ["2027-02-29", "2028-13", "0000", "2028-1", "tomorrow", 2028])
def test_invalid_or_nonstring_date_is_rejected(config_files, value):
    path, docs, write = config_files
    docs["profile"]["preferences"]["availability"] = value
    write("profile", docs["profile"])
    with pytest.raises(ConfigurationError, match="availability"):
        load_configuration(path)


@pytest.mark.parametrize("document", ["settings", "profile", "companies"])
def test_unknown_fields_rejected_without_echoing_private_values(config_files, document):
    path, docs, write = config_files
    docs[document]["typo"] = "PRIVATE_VALUE_MUST_NOT_APPEAR"
    write(document, docs[document])
    with pytest.raises(ConfigurationError, match="typo") as error:
        load_configuration(path)
    assert "PRIVATE_VALUE_MUST_NOT_APPEAR" not in str(error.value)


@pytest.mark.parametrize(
    "updates",
    [
        {"collection": {"max_attempts": 0}},
        {"collection": {"max_attempts": True}},
        {"collection": {"max_response_bytes": -1}},
        {"collection": {"run_deadline_seconds": float("inf")}},
        {"collection": {"connect_timeout_seconds": float("nan")}},
        {"collection": {"read_timeout_seconds": "20"}},
        {"reconciliation": {"minimum_absence_hours": 0}},
        {"filters": {"comparable_pay_filter_enabled": "false"}},
        {"filters": {"unexpected": True}},
        {"profile_file": ""},
        {"profile_file": 123},
        {"schema_version": 2},
        {"schema_version": True},
        {"schema_version": 1.0},
    ],
)
def test_invalid_settings_fail_before_io(config_files, updates):
    path, docs, write = config_files
    docs["settings"].update(updates)
    write("settings", docs["settings"])
    with pytest.raises(ConfigurationError, match="Invalid configuration"):
        load_configuration(path)


@pytest.mark.parametrize(
    "payload",
    [
        "!!python/object/apply:os.system ['echo unsafe']",
        "schema_version: 1\nschema_version: 1\n",
        "thing: &anchor [1]\nother: *anchor\n",
        "thing: [\nPRIVATE_YAML_CONTENT",
        "? [unhashable, key]\n: value",
        "1: value",
        "\ud800",
    ],
)
def test_unsafe_malformed_yaml_is_rejected_and_redacted(config_files, payload):
    path, _, _ = config_files
    path.write_bytes(payload.encode("utf-8", errors="surrogatepass"))
    with pytest.raises(ConfigurationError, match="Invalid YAML") as error:
        load_configuration(path)
    assert "PRIVATE_YAML_CONTENT" not in str(error.value)


@pytest.mark.parametrize("payload", ["", "null", "[]", "hello"])
def test_nonmapping_config_rejected(config_files, payload):
    path, _, _ = config_files
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Invalid configuration"):
        load_configuration(path)


def test_oversized_configuration_rejected(config_files):
    path, _, _ = config_files
    path.write_bytes(b" " * (MAX_CONFIG_BYTES + 1))
    with pytest.raises(ConfigurationError, match="1 MiB"):
        load_configuration(path)


@pytest.mark.parametrize("missing", ["settings", "profile", "companies"])
def test_missing_files_have_actionable_errors(config_files, missing):
    path, _, _ = config_files
    (path.parent / f"{missing}.yml").unlink()
    with pytest.raises(ConfigurationError, match="Cannot read"):
        load_configuration(path)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/jobs",
        "https://user:secret@example.com/jobs",
        "file:///tmp/jobs",
        "https:///missing-host",
        "https://example.com:invalid",
        "https://example.com/jobs with spaces",
    ],
)
def test_invalid_careers_urls_are_rejected_without_credentials(url):
    with pytest.raises(ValidationError) as error:
        Source.model_validate(enabled_source(official_careers_url=url))
    assert "user:secret" not in str(error.value)


@pytest.mark.parametrize(
    "updates",
    [
        {"provider": "lever"},
        {"board_token": None},
        {"board_token": "../bad"},
        {"official_careers_url": None},
        {"disabled_reason": "Not verified"},
    ],
)
def test_enabled_source_requires_supported_verified_metadata(updates):
    with pytest.raises(ValidationError):
        Source.model_validate(enabled_source(**updates))


def test_disabled_future_provider_is_explicitly_allowed():
    source = Source.model_validate(
        {
            "key": "future-lever",
            "provider": "lever",
            "disabled_reason": "Not implemented",
        }
    )
    assert not source.enabled
    assert source.board_token is None


def test_disabled_source_requires_reason():
    with pytest.raises(ValidationError, match="disabled_reason"):
        Source.model_validate({"key": "example", "provider": "greenhouse"})


@pytest.mark.parametrize("duplicate", ["company", "source", "scope"])
def test_duplicate_identities_are_rejected(config_files, duplicate):
    _, docs, _ = config_files
    first = {
        "key": "first",
        "name": "First",
        "category": "big-tech-ai",
        "sources": [enabled_source()],
    }
    second = {
        "key": "second",
        "name": "Second",
        "category": "big-tech-ai",
        "sources": [enabled_source(key="second-source", board_token="another-board")],
    }
    if duplicate == "company":
        second["key"] = first["key"]
    elif duplicate == "source":
        second["sources"][0]["key"] = first["sources"][0]["key"]
    else:
        second["sources"][0]["board_token"] = first["sources"][0]["board_token"]
    docs["companies"]["companies"] = [first, second]
    with pytest.raises(ValidationError, match="unique"):
        CompanyWatchlist.model_validate(docs["companies"])


def test_experience_date_order_and_apprenticeship_uncertainty(config_files):
    path, docs, write = config_files
    docs["profile"]["experience"] = [
        {
            "employer": "Example",
            "role": "Apprentice",
            "kind": "apprenticeship",
            "start": "2027",
            "end": "2027-01",
        }
    ]
    write("profile", docs["profile"])
    loaded = load_configuration(path)
    assert loaded.profile.experience[0].counts_as_professional_experience is None
    docs["profile"]["experience"][0]["end"] = "2026"
    write("profile", docs["profile"])
    with pytest.raises(ConfigurationError, match="end must not precede"):
        load_configuration(path)


@pytest.mark.parametrize(
    "compensation",
    [
        {"currency": "eur"},
        {"currency": "EUR", "target_annual_total": -1},
        {"currency": "EUR", "fallback_annual_total": 50000},
        {"currency": "EUR", "target_annual_total": 100000, "fallback_annual_total": 120000},
    ],
)
def test_invalid_compensation_policy_rejected(config_files, compensation):
    _, docs, _ = config_files
    docs["profile"]["preferences"]["compensation"] = compensation
    with pytest.raises(ValidationError):
        CandidateProfile.model_validate(docs["profile"])


def test_committed_examples_validate_with_no_enabled_sources(repository_root):
    loaded = load_configuration(repository_root / "conf" / "settings.yml")
    assert len(loaded.watchlist.companies) == 39
    assert not any(source.enabled for c in loaded.watchlist.companies for source in c.sources)
    assert {c.category for c in loaded.watchlist.companies} == {
        "big-tech-ai",
        "quantitative-finance",
        "banking-insurance-asset-management",
    }
    assert all(c.disabled_reason for c in loaded.watchlist.companies)
    assert isinstance(loaded.settings.database_path, Path)
