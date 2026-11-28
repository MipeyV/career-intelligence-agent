import asyncio
import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from career_intelligence.config import MatchingProposalLimits, load_configuration
from career_intelligence.extraction import FixtureProvider
from career_intelligence.matching import DIMENSIONS, MatchingReview, load_policy
from career_intelligence.matching_proposals import replay_matching_proposal


@pytest.fixture
def context(repository_root):
    profile = load_configuration(repository_root / "conf/settings.yml").profile
    policy = load_policy(repository_root / "conf/matching.example.yml")
    source = json.loads((repository_root / "tests/fixtures/greenhouse/matching.json").read_text())
    revision = {
        "job_id": "synthetic-job",
        "number": 3,
        "fingerprint": "a" * 64,
        "payload": {
            "title": "Engineer",
            "location": None,
            "description": source["jobs"][0]["content"],
            "raw_record": {"credential": "PRIVATE_RAW"},
            "official_application_url": "https://example.com/private",
        },
    }
    path = repository_root / "tests/fixtures/matching/proposal.json"
    return revision, profile, policy, path


def replay(context, *, limits=None, path=None):
    revision, profile, policy, fixture = context
    return asyncio.run(
        replay_matching_proposal(
            revision, profile, policy, path or fixture, limits=limits or MatchingProposalLimits()
        )
    )


def test_supported_proposal_is_bound_but_cannot_be_used_as_an_approved_review(context):
    revision, profile, policy, path = context
    before = deepcopy((revision, profile.model_dump(), policy.model_dump()))
    result = replay(context)
    assert result.status == "complete" and result.fixture_reads == 1
    assert result.provider == "fixture" and result.review_required is True
    assert result.prompt_version == "matching-proposal-v2"
    assert result.schema_version == result.proposal_version == 2
    assert result.job_id == revision["job_id"] and result.revision == 3
    assert result.response_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    draft = result.draft_review
    assert draft["reviewed_by"] is None and draft["reviewed_on"] is None
    assert draft["revision_fingerprint"] == revision["fingerprint"]
    assert (
        draft["profile_hash"] == result.profile_hash and draft["policy_hash"] == result.policy_hash
    )
    assert draft["assessments"]["compensation"] is None
    assert draft["assessments"]["technical_skills"]["gaps"] is None
    assert draft["assessments"]["technical_skills"]["transferable_skills"] == []
    assert "overall_score" not in result.model_dump()
    for data in (draft, result.model_dump()):
        with pytest.raises(ValidationError):
            MatchingReview.model_validate(data)
    assert before == (revision, profile.model_dump(), policy.model_dump())
    assert replay(context) == result


def test_request_contains_only_selected_job_fields_and_validated_local_profile_in_memory(
    context, monkeypatch
):
    requests = []
    original = FixtureProvider.generate

    async def capture(self, request):
        requests.append(request)
        return await original(self, request)

    monkeypatch.setattr(FixtureProvider, "generate", capture)
    result = replay(context)
    assert result.status == "complete" and len(requests) == 1
    request = requests[0]
    content = json.loads(request.content)
    assert set(content) == {"posting", "profile", "rubrics", "rubric_version", "span_hints"}
    assert set(content["posting"]) == {"title", "location", "description"}
    assert content["profile"] == context[1].model_dump(mode="json")
    assert "PRIVATE_RAW" not in request.content and "https://" not in request.content
    assert "overall_score" not in request.response_schema_json
    assert "reviewed_by" not in request.response_schema_json
    assert request.max_output_tokens == 4096
    for hint in content["span_hints"]:
        assert content["posting"][hint["field"]][hint["start"] : hint["end"]] == hint["text"]
    assert "Synthetic course project" not in result.model_dump_json()


@pytest.mark.parametrize("change", ["profile", "rubric", "source", "hints", "tokens"])
def test_input_hash_covers_profile_rubrics_source_and_request_options(context, change):
    original = replay(context)
    limits = MatchingProposalLimits()
    if change == "profile":
        context[1].preferences.objectives.append("Additional synthetic objective")
    elif change == "rubric":
        context[2].rubrics["academics"].anchors[0] += " Updated."
    elif change == "source":
        context[0]["payload"]["description"] += " More source text."
    elif change == "hints":
        limits = MatchingProposalLimits(max_span_hints=0)
    else:
        limits = MatchingProposalLimits(max_output_tokens=128)
    changed = replay(context, limits=limits)
    assert changed.status == "complete" and changed.input_sha256 != original.input_sha256


@pytest.mark.parametrize("component", ["source", "profile", "rubric"])
def test_full_input_budget_includes_candidate_policy_and_hints_before_reading_a_response(
    context, component
):
    if component == "source":
        context[0]["payload"]["description"] += "x" * 65_536
    elif component == "profile":
        context[1].preferences.objectives.append("x" * 65_536)
    else:
        for rubric in context[2].rubrics.values():
            rubric.anchors[0] += "x" * 4000
    result = replay(
        context,
        path=context[3].parent / "DO_NOT_READ.json",
        limits=MatchingProposalLimits(max_input_bytes=25_000),
    )
    assert result.status == "input_limit" and result.fixture_reads == 0
    assert result.draft_review is None and result.response_sha256 is None


@pytest.mark.parametrize(
    "mutation", ["quote", "field", "offset", "missing_fact", "null_fact", "container"]
)
def test_any_unsupported_dimension_discards_the_whole_proposal(context, tmp_path, mutation):
    data = json.loads(context[3].read_text())
    good = data["assessments"]["technical_skills"]
    bad = deepcopy(good)
    if mutation == "quote":
        bad["source_evidence"][0]["text"] = "PRIVATE_UNSUPPORTED_VALUE"
    elif mutation == "field":
        bad["source_evidence"][0]["evidence"]["field"] = "location"
    elif mutation == "offset":
        bad["source_evidence"][0]["evidence"]["start"] = 1
    else:
        bad["profile_refs"] = [
            {
                "missing_fact": "skills.99.name",
                "null_fact": "languages",
                "container": "preferences",
            }[mutation]
        ]
    data["assessments"]["academics"] = bad
    path = tmp_path / "unsupported.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = replay(context, path=path)
    assert result.status == "ungrounded_evidence" and result.draft_review is None
    assert "PRIVATE_UNSUPPORTED_VALUE" not in result.model_dump_json()


@pytest.mark.parametrize(
    "failure",
    [
        "reviewer",
        "date",
        "global_score",
        "level_bool",
        "missing_dimension",
        "unknown_dimension",
        "nonfinite",
        "duplicate",
        "fences",
        "root_list",
        "truncated",
        "deep",
        "invalid_utf8",
        "surrogate",
    ],
)
def test_malformed_or_self_approved_responses_are_rejected_without_repair(
    context, tmp_path, failure
):
    data = json.loads(context[3].read_text())
    if failure == "reviewer":
        data["reviewed_by"] = "PRIVATE_UNSUPPORTED_VALUE"
    elif failure == "date":
        data["reviewed_on"] = "2026-11-28"
    elif failure == "global_score":
        data["overall_score"] = 100
    elif failure == "level_bool":
        data["assessments"]["technical_skills"]["level"] = True
    elif failure == "missing_dimension":
        del data["assessments"]["compensation"]
    elif failure == "unknown_dimension":
        data["assessments"]["PRIVATE_UNSUPPORTED_VALUE"] = None
    raw = json.dumps(data).encode()
    replacements = {
        "nonfinite": b'{"schema_version": 1, "assessments": NaN}',
        "duplicate": b'{"schema_version": 1, "schema_version": 1}',
        "fences": b"```json\n{}\n```",
        "root_list": b"[]",
        "truncated": b"{",
        "deep": b"[" * 1100 + b"0" + b"]" * 1100,
        "invalid_utf8": b"\xff",
        "surrogate": b'{"schema_version": 1, "assessments": {"\\ud800": null}}',
    }
    path = tmp_path / "bad.json"
    path.write_bytes(replacements.get(failure, raw))
    result = replay(context, path=path)
    assert result.status == "invalid_output" and result.draft_review is None
    assert "PRIVATE_UNSUPPORTED_VALUE" not in result.model_dump_json()


def test_fixture_bytes_are_bounded_and_missing_files_do_not_expose_paths(context, tmp_path):
    result = replay(context, limits=MatchingProposalLimits(max_output_bytes=1))
    assert result.status == "output_limit" and result.draft_review is None
    result = replay(context, path=tmp_path / "PRIVATE_PATH.json")
    assert result.status == "fixture_unavailable"
    assert "PRIVATE_PATH" not in result.model_dump_json()


def test_unknown_all_dimensions_remains_an_unreviewed_draft(context, tmp_path):
    data = {"schema_version": 1, "assessments": dict.fromkeys(DIMENSIONS)}
    path = tmp_path / "unknown.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = replay(context, path=path)
    assert result.status == "complete" and result.draft_review["assessments"] == data["assessments"]
    assert result.review_required


def test_timeout_cancels_the_cooperative_fixture_attempt_without_retry(context, monkeypatch):
    calls, cancelled = [], []

    async def slow(self, request):
        calls.append(request)
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)

    monkeypatch.setattr(FixtureProvider, "generate", slow)
    result = replay(context, limits=MatchingProposalLimits(timeout_seconds=0.01))
    assert result.status == "timeout" and result.fixture_reads == 1
    assert len(calls) == 1 and cancelled == [True]


def test_outer_cancellation_is_propagated_and_never_reported_as_a_fixture_failure(
    context, monkeypatch
):
    async def cancelled(self, request):
        raise asyncio.CancelledError

    monkeypatch.setattr(FixtureProvider, "generate", cancelled)
    with pytest.raises(asyncio.CancelledError):
        replay(context)


@pytest.mark.parametrize(
    "response,expected",
    [(None, "invalid_output"), ("\ud800", "invalid_output"), ("x" * 1024, "output_limit")],
)
def test_adapter_boundary_rechecks_type_encoding_and_size(context, monkeypatch, response, expected):
    async def bad(self, request):
        return response

    monkeypatch.setattr(FixtureProvider, "generate", bad)
    result = replay(context, limits=MatchingProposalLimits(max_output_bytes=512))
    assert result.status == expected and result.draft_review is None


def test_adapter_errors_never_echo_private_diagnostics(context, monkeypatch):
    async def failed(self, request):
        raise RuntimeError("PRIVATE_TOKEN and candidate data")

    monkeypatch.setattr(FixtureProvider, "generate", failed)
    result = replay(context)
    assert (
        result.status == "fixture_unavailable" and "PRIVATE_TOKEN" not in result.model_dump_json()
    )


@pytest.mark.parametrize(
    "limits",
    [
        {"max_input_bytes": 0},
        {"max_output_bytes": 0},
        {"max_span_hints": 257},
        {"timeout_seconds": float("nan")},
    ],
)
def test_replay_limits_are_validated_independently(limits):
    with pytest.raises(ValidationError):
        MatchingProposalLimits(**limits)
