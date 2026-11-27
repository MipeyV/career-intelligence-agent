from datetime import UTC, datetime

import pytest

from career_intelligence.collection import Posting
from career_intelligence.config import CandidateProfile, FilterPolicy
from career_intelligence.filtering import evaluate
from career_intelligence.presentation import job_table, table


@pytest.fixture
def profile():
    return CandidateProfile.model_validate(
        {
            "schema_version": 1,
            "preferences": {
                "target_roles": ["Machine Learning Engineer"],
                "target_seniority": ["Junior", "Graduate"],
                "locations": ["Example City", "Other City", "Remote"],
                "compensation": {"currency": "EUR", "target_annual_total": 100000},
            },
        }
    )


@pytest.fixture
def policy():
    return FilterPolicy.model_validate(
        {
            "role_aliases": {"machine-learning-engineer": ["ML Engineer"]},
            "location_aliases": {"example-city": ["Example City", "Example Region"]},
            "excluded_title_seniority": ["Senior", "Staff", "Head of"],
        }
    )


def post(**values):
    return Posting(
        source_provider="greenhouse",
        source_scope="example",
        source_post_id="1",
        company_key="example",
        company="Example Company",
        observed_at=datetime.now(UTC),
        source_url="https://example.com/jobs/1",
        raw_record={},
        **{"title": "Machine Learning Engineer", "location": "Example City", **values},
    )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Machine Learning Engineer", "keep"),
        ("ML Engineer", "keep"),
        ("Senior Machine Learning Engineer", "exclude"),
        ("Head of ML Engineer", "exclude"),
        ("Junior/Senior ML Engineer", "review"),
        ("ML Engineering Manager", "review"),
        ("Unclear opportunity", "review"),
        ("Small Engineer", "review"),
    ],
)
def test_roles_and_seniority(profile, policy, title, expected):
    decision = evaluate(post(title=title), profile, policy)
    assert decision.decision == expected
    assert decision.config_hash
    assert decision.reasons
    for reason in decision.reasons:
        if reason.field == "title" and reason.evidence is not None:
            assert reason.evidence in title


@pytest.mark.parametrize(
    ("location", "rank", "expected"),
    [
        ("Example Region", 0, "keep"),
        ("Other City", 1, "keep"),
        ("Remote", 2, "keep"),
        (None, None, "review"),
        ("Another place", None, "review"),
    ],
)
def test_location_aliases_and_unknowns(profile, policy, location, rank, expected):
    decision = evaluate(post(location=location), profile, policy)
    assert decision.location_rank == rank
    assert decision.decision == expected


def pay(**values):
    return {
        "kind": "total_compensation",
        "minimum": 110000,
        "maximum": 120000,
        "period": "annual",
        "currency": "EUR",
        "provenance": "employer_disclosed",
        "source_url": "https://example.com/jobs/1",
        "source_date": "2026-11-27",
        **values,
    }


@pytest.mark.parametrize(
    ("compensation", "expected", "code"),
    [
        (None, "review", "compensation_unknown"),
        ([pay()], "keep", "compensation_aligned"),
        (
            [pay(kind="base_salary", minimum=40000, maximum=50000)],
            "review",
            "compensation_not_comparable",
        ),
        ([pay(minimum=40000, maximum=50000)], "exclude", "compensation_below_floor"),
        (
            [pay(currency="USD", minimum=40000, maximum=50000)],
            "review",
            "compensation_not_comparable",
        ),
        (
            [pay(provenance="estimated", minimum=40000, maximum=50000)],
            "review",
            "compensation_not_comparable",
        ),
        ([pay(minimum=90000, maximum=110000)], "review", "compensation_ambiguous"),
        ([pay(minimum=40000, maximum=None)], "review", "compensation_ambiguous"),
        ([pay(source_url=None)], "review", "compensation_not_comparable"),
        ([pay(minimum=120000, maximum=110000)], "review", "compensation_not_comparable"),
        (
            [pay(minimum=40000, maximum=50000), pay(currency="USD")],
            "review",
            "compensation_ambiguous",
        ),
    ],
)
def test_compensation_needs_comparable_disclosed_total(
    profile, policy, compensation, expected, code
):
    policy.comparable_pay_filter_enabled = True
    decision = evaluate(post(compensation=compensation), profile, policy)
    assert decision.decision == expected
    assert decision.reasons[-1].code == code


def test_fallback_threshold_is_explicit(profile, policy):
    policy.comparable_pay_filter_enabled = True
    profile.preferences.compensation.fallback_annual_total = 70000
    result = evaluate(post(compensation=[pay(minimum=75000, maximum=80000)]), profile, policy)
    assert result.decision == "keep"
    assert result.reasons[-1].profile_field.endswith("fallback_annual_total")


@pytest.mark.parametrize("url", ["", "unattributed", "https://user:secret@example.com/pay"])
def test_low_pay_with_invalid_attribution_stays_reviewable(profile, policy, url):
    policy.comparable_pay_filter_enabled = True
    decision = evaluate(
        post(compensation=[pay(minimum=40000, maximum=50000, source_url=url)]),
        profile,
        policy,
    )
    assert decision.decision == "review"


@pytest.mark.parametrize(
    "values",
    [
        {"work_authorization": "Must have permission to work here"},
        {"required_experience": "Minimum three years of industry experience"},
    ],
)
def test_interpreted_eligibility_is_reviewed(profile, policy, values):
    assert evaluate(post(**values), profile, policy).decision == "review"


def test_changed_profile_invalidates_hash_and_preserves_evidence(profile, policy):
    before = evaluate(post(), profile, policy)
    profile.preferences.locations = ["Other City"]
    after = evaluate(post(), profile, policy)
    assert before.config_hash != after.config_hash
    assert before.decision == "keep"
    assert after.decision == "review"


def test_empty_and_control_character_tables():
    assert "ID" in table(["ID"], [])
    assert "\x1b" not in table(["Title"], [["\x1b[31mTitle\nOther"]])
    assert "Decision" in job_table([])
