import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from career_intelligence.config import (
    ConfigurationError,
    MatchingLocalSettings,
    MatchingProposalLimits,
    OllamaSettings,
    load_configuration,
)
from career_intelligence.extraction import ProviderNotInstalledError
from career_intelligence.matching import load_policy, policy_hash
from career_intelligence.matching_proposals import propose_matching_local


@pytest.fixture
def context(repository_root):
    loaded = load_configuration(repository_root / "conf/settings.yml")
    policy = load_policy(repository_root / "conf/matching.example.yml")
    revision = {
        "job_id": "synthetic",
        "number": 1,
        "fingerprint": "a" * 64,
        "payload": {
            "description": "Build Python services",
            "title": "Engineer",
            "location": None,
            "raw_record": {"token": "PRIVATE_RAW"},
        },
    }
    response = (repository_root / "tests/fixtures/matching/proposal.json").read_text()
    return revision, loaded.profile, policy, response


def sharing(**updates):
    return MatchingLocalSettings(
        share_profile=True, profile_fields=["skills.0.name", "skills.0.evidence.0"], **updates
    )


def run(context, *, permission=None, limits=None, transport=None, settings=None):
    return asyncio.run(
        propose_matching_local(
            *context[:3],
            settings=settings or OllamaSettings(model="example:local"),
            sharing=permission or sharing(),
            limits=limits or MatchingProposalLimits(),
            transport=transport,
        )
    )


def test_default_settings_never_construct_or_call_a_model_and_disclose_no_input(
    context, monkeypatch
):
    def forbidden(*args, **kwargs):
        raise AssertionError("Model must not be constructed without consent")

    monkeypatch.setattr("career_intelligence.matching_proposals.OllamaProvider", forbidden)
    result = run(context, permission=MatchingLocalSettings())
    assert result.status == "profile_sharing_disabled"
    assert result.provider_attempts == result.fixture_reads == 0
    assert result.input_sha256 is None and result.draft_review is None
    assert "Synthetic course project" not in result.model_dump_json()


@pytest.mark.parametrize(
    "values",
    [
        {"share_profile": "true"},
        {"share_profile": True},
        {"share_profile": True, "profile_fields": []},
        {"profile_fields": ["skills", "skills"]},
        {"profile_fields": ["__class__"]},
        {"profile_fields": ["skills.00.name"]},
        {"profile_fields": ["skills"] * 129},
    ],
)
def test_sharing_is_explicit_bounded_and_validated(values):
    with pytest.raises(ValidationError):
        MatchingLocalSettings(**values)


@pytest.mark.parametrize(
    "refs", [["skills"], ["preferences"], ["skills.99.name"], ["languages.0.name"]]
)
def test_unavailable_or_container_allowlists_fail_before_any_provider_request(
    context, monkeypatch, refs
):
    def forbidden(*args, **kwargs):
        raise AssertionError("Field validation must precede model construction")

    monkeypatch.setattr("career_intelligence.matching_proposals.OllamaProvider", forbidden)
    with pytest.raises(ConfigurationError):
        run(context, permission=MatchingLocalSettings(share_profile=True, profile_fields=refs))


def test_selected_scalar_values_only_are_visible_to_the_provider_and_snapshot_is_stable(
    context, monkeypatch
):
    captured = []
    original_policy_hash = policy_hash(context[2])
    original_profile = context[1].model_dump()

    class Provider:
        def __init__(self, *args):
            pass

        async def generate(self, request):
            captured.append(request)
            # Simulate caller mutation while a slow model call is outstanding.
            context[1].skills[0].name = "PRIVATE_MUTATION"
            context[2].rubrics["academics"].anchors[0] += " Updated."
            context[0]["payload"]["description"] = "PRIVATE_MUTATION"
            return context[3]

    monkeypatch.setattr("career_intelligence.matching_proposals.OllamaProvider", Provider)
    result = run(context)
    assert result.status == "complete" and result.policy_hash == original_policy_hash
    content = json.loads(captured[0].content)
    assert content["profile_facts"] == {
        "skills.0.name": original_profile["skills"][0]["name"],
        "skills.0.evidence.0": original_profile["skills"][0]["evidence"][0],
    }
    assert "profile" not in content and "preferences" not in content
    assert "PRIVATE_" not in captured[0].content
    assert result.profile_fields == ["skills.0.evidence.0", "skills.0.name"]
    assert result.draft_review["reviewed_by"] is None and result.draft_review["reviewed_on"] is None


@pytest.mark.parametrize("use_unshared", [False, True])
def test_even_known_but_unshared_profile_paths_cannot_support_a_proposal(
    context, monkeypatch, use_unshared
):
    response = json.loads(context[3])
    if use_unshared:
        response["assessments"]["technical_skills"]["profile_refs"] = ["preferences.target_roles.0"]

    class Provider:
        def __init__(self, *args):
            pass

        async def generate(self, request):
            return json.dumps(response)

    monkeypatch.setattr("career_intelligence.matching_proposals.OllamaProvider", Provider)
    result = run(context)
    assert result.status == ("ungrounded_evidence" if use_unshared else "complete")
    if use_unshared:
        assert result.draft_review is None


def test_selected_nulls_and_empty_lists_preserve_unknowns_but_null_cannot_support_a_rating(
    context, monkeypatch
):
    context[1].projects = []
    captured = []
    response = {
        "schema_version": 1,
        "assessments": dict.fromkeys(json.loads(context[3])["assessments"]),
    }

    class Provider:
        def __init__(self, *args):
            pass

        async def generate(self, request):
            captured.append(json.loads(request.content))
            return json.dumps(response)

    monkeypatch.setattr("career_intelligence.matching_proposals.OllamaProvider", Provider)
    permission = MatchingLocalSettings(share_profile=True, profile_fields=["languages", "projects"])
    assert run(context, permission=permission).status == "complete"
    assert captured[0]["profile_facts"] == {"languages": None, "projects": []}
    response["assessments"]["technical_skills"] = json.loads(context[3])["assessments"][
        "technical_skills"
    ]
    response["assessments"]["technical_skills"]["profile_refs"] = ["languages"]
    assert run(context, permission=permission).status == "ungrounded_evidence"


def test_budget_prevents_preflight_and_disclosure_even_with_consent(context):
    async def forbidden(request):
        raise AssertionError("Budget must stop all requests")

    result = run(
        context,
        limits=MatchingProposalLimits(max_input_bytes=1),
        transport=httpx.MockTransport(forbidden),
    )
    assert result.status == "input_limit" and result.provider_attempts == 0


def test_missing_optional_extra_is_safe_and_preserves_ordinary_services(context, monkeypatch):
    class Missing:
        def __init__(self, *args):
            pass

        async def generate(self, request):
            raise ProviderNotInstalledError

    monkeypatch.setattr("career_intelligence.matching_proposals.OllamaProvider", Missing)
    result = run(context)
    assert result.status == "provider_not_installed" and result.draft_review is None


@pytest.mark.parametrize(
    "setting,value", [("base_url", "https://example.com"), ("model", "test:cloud")]
)
def test_mutated_local_settings_cannot_bypass_endpoint_validation(context, setting, value):
    config = OllamaSettings(model="example:local").model_copy(update={setting: value})
    with pytest.raises(ConfigurationError):
        asyncio.run(
            propose_matching_local(
                *context[:3], settings=config, sharing=sharing(), limits=MatchingProposalLimits()
            )
        )


def test_unshared_facts_do_not_enter_request_hash_but_keep_profile_binding_stale(
    context, monkeypatch
):
    class Provider:
        def __init__(self, *args):
            pass

        async def generate(self, request):
            return context[3]

    monkeypatch.setattr("career_intelligence.matching_proposals.OllamaProvider", Provider)
    original = run(context)
    context[1].preferences.objectives.append("PRIVATE_UNSHARED_OBJECTIVE")
    changed = run(context)
    assert changed.input_sha256 == original.input_sha256
    assert changed.profile_hash != original.profile_hash
    assert "PRIVATE_UNSHARED_OBJECTIVE" not in changed.model_dump_json()
    reseeded = run(context, settings=OllamaSettings(model="example:local", seed=1))
    assert reseeded.input_sha256 == changed.input_sha256
    assert reseeded.inference_config_sha256 != changed.inference_config_sha256
