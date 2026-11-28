from copy import deepcopy
from datetime import date

import pytest
from pydantic import ValidationError

from career_intelligence.config import (
    CandidateProfile,
    ConfigurationError,
    FilterPolicy,
    profile_hash_payload,
)
from career_intelligence.eligibility import (
    ReviewedRequirements,
    assess_eligibility,
    profile_hash,
    review_template,
)
from career_intelligence.filtering import configuration_hash

SOURCE = "Synthetic source evidence for the rule-contract examples."
AS_OF = date(2028, 6, 30)


def candidate(**updates):
    return CandidateProfile.model_validate(
        {
            "schema_version": 1,
            "preferences": {
                "availability": "2028-10",
                "target_roles": ["Research Engineer"],
                "locations": ["Example City"],
                "compensation": {"currency": "EUR"},
            },
            **updates,
        }
    )


def revision():
    return {
        "job_id": "example-job",
        "number": 1,
        "fingerprint": "a" * 64,
        "payload": {"description": SOURCE, "title": "Research Engineer", "location": None},
    }


def rule(kind, **values):
    return {
        "id": kind.replace("_", "-"),
        "kind": kind,
        "source": {
            "text": SOURCE,
            "evidence": {"field": "description", "start": 0, "end": len(SOURCE)},
        },
        **values,
    }


def review(
    profile, rules, *, complete=(), locations=None, coverage="complete", reviewed_on="2026-11-28"
):
    data = review_template(revision(), profile)
    return ReviewedRequirements.model_validate(
        {
            **data,
            "reviewed_by": "synthetic-reviewer",
            "reviewed_on": reviewed_on,
            "coverage": coverage,
            "constraints": rules,
            "candidate_assertions": {
                "complete_sections": list(complete),
                "work_locations": locations,
            },
        }
    )


def assess(profile, rules, **kwargs):
    return assess_eligibility(revision(), profile, review(profile, rules, **kwargs), as_of=AS_OF)


@pytest.mark.parametrize(
    "availability, expected",
    [
        (None, "uncertain"),
        ("2028", "uncertain"),
        ("2028-09", "eligible"),
        ("2028-10", "uncertain"),
        ("2028-10-01", "eligible"),
        ("2028-11", "not_eligible"),
    ],
)
def test_availability_preserves_precision_and_unknowns(availability, expected):
    profile = candidate()
    profile.preferences.availability = availability
    result = assess(profile, [rule("availability_by", cutoff="2028-10-01")])
    assert result["status"] == expected
    assert result["scope"] == "reviewed_constraints_only"


@pytest.mark.parametrize(
    "cutoff, expected",
    [
        ("2028-06-30", "eligible"),
        ("2028-06-29", "not_eligible"),
        ("2028-06", "uncertain"),
        ("2028", "uncertain"),
    ],
)
def test_deadline_uses_explicit_as_of_date_and_partial_date_bounds(cutoff, expected):
    assert assess(candidate(), [rule("application_deadline", cutoff=cutoff)])["status"] == expected


@pytest.mark.parametrize(
    "graduation, expected",
    [
        ("2028-05", "potentially_eligible"),
        ("2028", "uncertain"),
        (None, "uncertain"),
        ("2029-01", "uncertain"),
    ],
)
def test_expected_graduation_never_becomes_verified_completion(graduation, expected):
    profile = candidate(
        education=[{"institution": "Example", "degree": "MSc", "expected_graduation": graduation}]
    )
    assert (
        assess(profile, [rule("graduation_window", earliest="2028-01-01", latest="2028-09-30")])[
            "status"
        ]
        == expected
    )


def episode(**updates):
    return {
        "employer": "Example",
        "role": "Engineer",
        "start": "2026-01-01",
        "end": "2028-01-01",
        "kind": "employment",
        "counts_as_professional_experience": True,
        **updates,
    }


def test_experience_overlaps_are_merged_and_year_comparison_is_bounded():
    profile = candidate(experience=[episode(), episode(employer="Other")])
    result = assess(profile, [rule("minimum_experience", minimum_years=3)], complete=["experience"])
    assert result["status"] == "not_eligible"
    assert result["rules"][0]["details"] == {
        "minimum_days": 730,
        "maximum_days": 731,
        "threshold_days_range": [1095, 1098],
    }
    assert assess(profile, [rule("minimum_experience", minimum_years=1)])["status"] == "eligible"


@pytest.mark.parametrize(
    "credit, policy, expected",
    [
        (True, True, "eligible"),
        (True, None, "uncertain"),
        (True, False, "not_eligible"),
        (None, True, "uncertain"),
        (False, True, "not_eligible"),
    ],
)
def test_apprenticeship_credit_requires_explicit_candidate_and_job_policy(credit, policy, expected):
    profile = candidate(
        experience=[episode(kind="apprenticeship", counts_as_professional_experience=credit)]
    )
    result = assess(
        profile,
        [rule("minimum_experience", minimum_years=1, apprenticeship_counts=policy)],
        complete=["experience"],
    )
    assert result["status"] == expected


def test_missing_end_does_not_prove_ongoing_experience_and_future_work_is_not_credited():
    profile = candidate(experience=[episode(end=None)])
    assert (
        assess(profile, [rule("minimum_experience", minimum_years=1)], complete=["experience"])[
            "status"
        ]
        == "uncertain"
    )
    profile = candidate(experience=[episode(start="2029-01-01", end="2031-01-01")])
    result = assess(profile, [rule("minimum_experience", minimum_years=1)], complete=["experience"])
    assert result["status"] == "not_eligible"
    assert result["rules"][0]["details"]["maximum_days"] == 0


def test_incomplete_experience_cannot_support_a_hard_negative():
    for value in (None, []):
        assert (
            assess(candidate(experience=value), [rule("minimum_experience", minimum_years=2)])[
                "status"
            ]
            == "uncertain"
        )
    assert (
        assess(
            candidate(experience=[]),
            [rule("minimum_experience", minimum_years=2)],
            complete=["experience"],
        )["status"]
        == "not_eligible"
    )


@pytest.mark.parametrize(
    "level, expected",
    [
        ("B2", "eligible"),
        ("C1", "eligible"),
        ("B1", "not_eligible"),
        ("fluent", "uncertain"),
        (None, "uncertain"),
    ],
)
def test_language_levels_use_only_the_reviewed_explicit_scale(level, expected):
    profile = candidate(languages=[{"name": "English", "proficiency": level}])
    result = assess(
        profile,
        [
            rule(
                "language",
                language="English",
                minimum_proficiency="B2",
                proficiency_order=["A1", "A2", "B1", "B2", "C1", "C2"],
            )
        ],
    )
    assert result["status"] == expected


def test_missing_languages_and_conflicting_facts_remain_unknown_without_completeness():
    requirement = rule("language", language="English")
    assert assess(candidate(languages=[]), [requirement])["status"] == "uncertain"
    assert (
        assess(candidate(languages=[]), [requirement], complete=["languages"])["status"]
        == "not_eligible"
    )
    profile = candidate(
        languages=[
            {"name": "English", "proficiency": "B1"},
            {"name": "English", "proficiency": "C1"},
        ]
    )
    requirement.update(minimum_proficiency="B2", proficiency_order=["B1", "B2", "C1"])
    assert assess(profile, [requirement])["status"] == "uncertain"


@pytest.mark.parametrize(
    "education, complete, expected",
    [
        (None, [], "uncertain"),
        ([], [], "uncertain"),
        ([], ["education"], "not_eligible"),
        ([{"institution": "Example", "degree": "MSc"}], [], "potentially_eligible"),
        ([{"institution": "Example", "degree": "Other qualification"}], ["education"], "uncertain"),
    ],
)
def test_degree_equivalence_and_completion_are_not_invented(education, complete, expected):
    result = assess(
        candidate(education=education),
        [rule("degree", accepted_degrees=["MSc"])],
        complete=complete,
    )
    assert result["status"] == expected


@pytest.mark.parametrize(
    "status, sponsorship, expected",
    [
        ("authorized", "unknown", "eligible"),
        ("requires_sponsorship", "not_available", "not_eligible"),
        ("requires_sponsorship", "available", "potentially_eligible"),
        ("requires_sponsorship", "unknown", "uncertain"),
        ("unknown", "not_available", "uncertain"),
    ],
)
def test_authorization_compares_declared_status_without_inventing_legal_rights(
    status, sponsorship, expected
):
    profile = candidate(work_authorizations=[{"location": "Example Region", "status": status}])
    result = assess(
        profile, [rule("work_authorization", location="Example Region", sponsorship=sponsorship)]
    )
    assert result["status"] == expected


def test_authorization_does_not_transfer_between_locations_and_conflicts_are_unknown():
    requirement = rule("work_authorization", location="Example Region", sponsorship="not_available")
    profile = candidate(work_authorizations=[{"location": "Other Region", "status": "authorized"}])
    assert assess(profile, [requirement])["status"] == "uncertain"
    profile = candidate(
        work_authorizations=[
            {"location": "Example Region", "status": "authorized"},
            {"location": "Example Region", "status": "requires_sponsorship"},
        ]
    )
    assert assess(profile, [requirement])["status"] == "uncertain"


@pytest.mark.parametrize(
    "locations, expected",
    [
        (None, "uncertain"),
        ([], "not_eligible"),
        (["Example City"], "eligible"),
        (["Other City"], "not_eligible"),
    ],
)
def test_geography_never_uses_location_preferences_as_attested_feasibility(locations, expected):
    result = assess(
        candidate(), [rule("geography", allowed_locations=["Example City"])], locations=locations
    )
    assert result["status"] == expected


def test_aggregation_preserves_partial_coverage_and_preferred_gaps():
    requirement = rule("application_deadline", cutoff="2028-06-01", mandatory=False)
    assert assess(candidate(), [requirement])["status"] == "potentially_eligible"
    assert assess(candidate(), [requirement], coverage="partial")["status"] == "uncertain"
    assert assess(candidate(), [])["status"] == "uncertain"
    assert (
        assess(candidate(), [rule("application_deadline", cutoff="2029")], coverage="partial")[
            "status"
        ]
        == "uncertain"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(job_id="different-job"),
        lambda r: r.update(revision=2),
        lambda r: r.update(revision_fingerprint="b" * 64),
        lambda r: r.update(profile_hash="b" * 64),
        lambda r: r["constraints"][0]["source"].update(text="Unsupported quote"),
    ],
)
def test_stale_identity_profile_or_unsupported_evidence_is_refused(mutation):
    profile = candidate()
    data = review(profile, [rule("application_deadline", cutoff="2029")]).model_dump()
    mutation(data)
    with pytest.raises(ConfigurationError):
        assess_eligibility(
            revision(), profile, ReviewedRequirements.model_validate(data), as_of=AS_OF
        )


def test_unknown_sections_cannot_be_marked_complete_and_bad_review_shapes_are_rejected():
    profile = candidate()
    with pytest.raises(ConfigurationError):
        assess(profile, [], complete=["experience"])
    data = review(profile, [rule("application_deadline", cutoff="2029")]).model_dump()
    data["constraints"] *= 2
    with pytest.raises(ValidationError):
        ReviewedRequirements.model_validate(data)
    data = review_template(revision(), profile)
    with pytest.raises(ValidationError):
        ReviewedRequirements.model_validate(data)


def test_explicit_contradiction_overrides_unknowns_but_preferred_gap_does_not():
    unknown = rule("work_authorization", location="Example Region", sponsorship="unknown")
    expired = rule("application_deadline", cutoff="2028-06-01")
    assert assess(candidate(), [unknown, expired], coverage="partial")["status"] == "not_eligible"
    expired["mandatory"] = False
    assert assess(candidate(), [unknown, expired])["status"] == "uncertain"


def test_partial_experience_dates_produce_a_range_and_can_be_unknown():
    profile = candidate(experience=[episode(start="2027", end="2028")])
    result = assess(profile, [rule("minimum_experience", minimum_years=1)], complete=["experience"])
    assert result["status"] == "uncertain"
    bounds = result["rules"][0]["details"]
    assert bounds["minimum_days"] < 365 and bounds["maximum_days"] > 366


def test_assessment_hash_includes_as_of_and_source_quote_whitespace_is_preserved():
    profile = candidate()
    stored = revision()
    stored["payload"]["description"] = "  Deadline.\n"
    normalized = rule("application_deadline", cutoff="2028-06-30")
    normalized["source"] = {
        "text": "  Deadline.\n",
        "evidence": {
            "field": "description",
            "start": 0,
            "end": len("  Deadline.\n"),
        },
    }
    document = review(profile, [normalized])
    first = assess_eligibility(stored, profile, document, as_of=AS_OF)
    later = assess_eligibility(stored, profile, document, as_of=date(2028, 7, 1))
    assert first["rules"][0]["source"]["text"] == "  Deadline.\n"
    assert first["assessment_hash"] != later["assessment_hash"]
    assert first["review_hash"] == later["review_hash"]


@pytest.mark.parametrize(
    "values",
    [
        {"minimum_proficiency": "B2", "proficiency_order": ["B1", "b1", "B2"]},
        {"minimum_proficiency": "B2", "proficiency_order": ["B1", "C1"]},
    ],
)
def test_ambiguous_or_incomplete_proficiency_scales_are_refused(values):
    with pytest.raises(ValidationError):
        review(candidate(), [rule("language", language="English", **values)])


def group(kind, *conditions, **values):
    tree = rule(kind, conditions=[deepcopy(child) for child in conditions], **values)

    def name(node, prefix):
        node["id"] = prefix
        for index, child in enumerate(node.get("conditions", [])):
            name(child, f"{prefix}-{index}")

    name(tree, "root")
    return tree


@pytest.mark.parametrize(
    "kind, table",
    [
        (
            "all_of",
            [
                ["met", "potential", "unknown", "contradiction"],
                ["potential", "potential", "unknown", "contradiction"],
                ["unknown", "unknown", "unknown", "contradiction"],
                ["contradiction", "contradiction", "contradiction", "contradiction"],
            ],
        ),
        (
            "any_of",
            [
                ["met", "met", "met", "met"],
                ["met", "potential", "potential", "potential"],
                ["met", "potential", "unknown", "unknown"],
                ["met", "potential", "unknown", "contradiction"],
            ],
        ),
    ],
)
@pytest.mark.parametrize("left,right", [(a, b) for a in range(4) for b in range(4)])
def test_four_valued_compound_logic_does_not_reject_a_passing_alternative(kind, table, left, right):
    leaves = [
        rule("availability_by", cutoff="2029"),
        rule("degree", accepted_degrees=["MSc"]),
        rule("work_authorization", location="Example Region", sponsorship="unknown"),
        rule("application_deadline", cutoff="2028-01-01"),
    ]
    profile = candidate(education=[{"institution": "Example", "degree": "MSc"}])
    report = assess(profile, [group(kind, leaves[left], leaves[right])])
    expected = table[left][right]
    assert report["rules"][0]["outcome"] == expected
    assert (
        report["status"]
        == {
            "met": "eligible",
            "potential": "potentially_eligible",
            "unknown": "uncertain",
            "contradiction": "not_eligible",
        }[expected]
    )
    assert report["schema_version"] == report["eligibility_version"] == 2
    assert len(report["rules"][0]["conditions"]) == 2


def test_nested_groups_report_leaf_coverage_and_validate_even_unused_branches():
    profile = candidate()
    tree = group(
        "all_of",
        group(
            "any_of",
            rule("application_deadline", cutoff="2028-01-01"),
            rule("availability_by", cutoff="2029"),
        ),
    )
    report = assess(profile, [tree])
    assert report["status"] == "eligible"
    assert "application_deadline" not in report["unassessed_kinds"]
    assert "availability_by" not in report["unassessed_kinds"]
    tree["conditions"][0]["conditions"][0]["source"]["text"] = "Unsupported quote"
    with pytest.raises(ConfigurationError):
        assess(profile, [tree])


def test_preferred_group_cannot_reject_and_preferred_children_are_refused():
    tree = group("any_of", rule("application_deadline", cutoff="2028-01-01"), mandatory=False)
    assert assess(candidate(), [tree])["status"] == "potentially_eligible"
    tree["conditions"][0]["mandatory"] = False
    with pytest.raises(ValidationError):
        review(candidate(), [tree])


@pytest.mark.parametrize("failure", ["depth", "nodes", "children", "empty", "duplicate", "v1"])
def test_review_tree_limits_and_version_boundaries(failure):
    leaf = rule("availability_by", cutoff="2029")
    tree = group("all_of", leaf)
    if failure == "depth":
        for _ in range(3):
            tree = group("all_of", tree)
    elif failure == "nodes":
        tree = group("all_of", *[group("all_of", *[leaf] * 4) for _ in range(32)])
    elif failure == "children":
        tree = group("all_of", *[leaf] * 33)
    elif failure == "empty":
        tree["conditions"] = []
    elif failure == "duplicate":
        tree["conditions"][0]["id"] = tree["id"]
    document = {
        **review_template(revision(), candidate()),
        "reviewed_by": "synthetic-reviewer",
        "reviewed_on": "2026-11-28",
        "constraints": [tree],
    }
    if failure == "v1":
        document["schema_version"] = 1
    with pytest.raises(ValidationError):
        ReviewedRequirements.model_validate(document)


def test_flat_version_one_reviews_remain_accepted():
    profile = candidate()
    data = review(profile, [rule("availability_by", cutoff="2029")]).model_dump()
    data["schema_version"] = 1
    result = assess_eligibility(
        revision(), profile, ReviewedRequirements.model_validate(data), as_of=AS_OF
    )
    assert result["status"] == "eligible"


def completed_education(**updates):
    return {
        "institution": "Example",
        "degree": "MSc",
        "completed": True,
        "completion_date": "2028-05",
        "status_as_of": "2028-06-30",
        "evidence": ["synthetic diploma record"],
        **updates,
    }


@pytest.mark.parametrize(
    "facts, expected",
    [
        ({}, "eligible"),
        ({"completion_date": None}, "eligible"),
        ({"completed": False, "completion_date": None}, "not_eligible"),
        ({"completed": False, "completion_date": None, "status_as_of": "2028-01-01"}, "uncertain"),
        ({"completed": None, "completion_date": None, "status_as_of": None}, "uncertain"),
    ],
)
def test_completed_degree_uses_dated_attestations_without_inventing_completion(facts, expected):
    profile = candidate(education=[completed_education(**facts)])
    report = assess(
        profile,
        [rule("degree", accepted_degrees=["MSc"], completion="completed")],
        complete=["education"],
        reviewed_on="2028-06-30",
    )
    assert report["status"] == expected


def test_degree_window_and_completion_must_hold_for_the_same_education_entry():
    profile = candidate(
        education=[
            completed_education(completion_date="2027-05"),
            completed_education(
                completed=False, completion_date=None, expected_graduation="2028-05"
            ),
        ]
    )
    requirement = rule(
        "degree",
        accepted_degrees=["MSc"],
        completion="completed",
        graduation_earliest="2028-01-01",
        graduation_latest="2028-09-30",
    )
    report = assess(profile, [requirement], complete=["education"], reviewed_on="2028-06-30")
    assert report["status"] == "not_eligible"
    profile = candidate(education=[*profile.model_dump()["education"], completed_education()])
    assert (
        assess(profile, [requirement], complete=["education"], reviewed_on="2028-06-30")["status"]
        == "eligible"
    )


def test_projected_degree_outside_window_is_unknown_and_incomplete_education_is_not_rejected():
    profile = candidate(
        education=[{"institution": "Example", "degree": "MSc", "expected_graduation": "2029"}]
    )
    requirement = rule(
        "degree",
        accepted_degrees=["MSc"],
        completion="expected_or_completed",
        graduation_latest="2028-09-30",
    )
    assert assess(profile, [requirement], complete=["education"])["status"] == "uncertain"
    profile = candidate(education=[completed_education(completed=False, completion_date=None)])
    requirement = rule("degree", accepted_degrees=["MSc"], completion="completed")
    assert assess(profile, [requirement], reviewed_on="2028-06-30")["status"] == "uncertain"


def test_ongoing_credit_stops_at_attestation_and_ended_unknown_date_bounds_remain_uncertain():
    profile = candidate(
        experience=[
            episode(
                end=None,
                ongoing=True,
                status_as_of="2028-01-01",
                evidence=["synthetic contract status"],
            )
        ]
    )
    requirement = rule("minimum_experience", minimum_years=2.2)
    result = assess(profile, [requirement], complete=["experience"], reviewed_on="2028-06-30")
    assert result["status"] == "uncertain"
    assert result["rules"][0]["details"]["minimum_days"] == 730
    assert (
        assess(profile, [rule("minimum_experience", minimum_years=1)], reviewed_on="2028-06-30")[
            "status"
        ]
        == "eligible"
    )
    profile = candidate(
        experience=[
            episode(
                end=None,
                ongoing=False,
                status_as_of="2028-01-01",
                evidence=["synthetic ended status"],
            )
        ]
    )
    result = assess(profile, [requirement], complete=["experience"], reviewed_on="2028-06-30")
    assert result["rules"][0]["details"]["minimum_days"] == 0
    assert result["rules"][0]["details"]["maximum_days"] == 731


def test_future_status_attestation_is_not_valid_for_an_earlier_review():
    profile = candidate(education=[completed_education()])
    with pytest.raises(ConfigurationError):
        assess(profile, [rule("degree", accepted_degrees=["MSc"])])


def test_unknown_additive_facts_preserve_frozen_legacy_profile_hashes_and_known_facts_change_them():
    profile = candidate(
        education=[{"institution": "Example", "degree": "MSc", "expected_graduation": "2028"}],
        experience=[episode(end=None)],
    )
    assert profile.model_dump()["education"][0]["completed"] is None
    assert "completed" not in profile_hash_payload(profile)["education"][0]
    assert (
        profile_hash(profile) == "0293d1dbd7ec1487fc75b86cc19feda90c0d9788d67561400d80ca509258c462"
    )
    assert (
        configuration_hash(profile, FilterPolicy())
        == "3faf8acec580ffad274e2c3052757103b2a61a79654898c9c02b366946bceeb2"
    )
    updated = candidate(
        education=[completed_education(expected_graduation="2028")], experience=[episode(end=None)]
    )
    assert profile_hash(updated) != profile_hash(profile)
    assert configuration_hash(updated, FilterPolicy()) != configuration_hash(
        profile, FilterPolicy()
    )
