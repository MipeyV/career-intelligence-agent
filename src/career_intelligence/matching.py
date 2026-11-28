"""Offline matching contract and deterministic aggregation of locally reviewed ratings."""

import hashlib
import json
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Literal, Self, get_args

from pydantic import Field, field_validator, model_validator

from career_intelligence.config import (
    CandidateProfile,
    ConfigurationError,
    _ConfigModel,
    _load_model,
)
from career_intelligence.config import (
    ProfileFieldReference as ProfileRef,
)
from career_intelligence.eligibility import SourceQuote, _exact_date, profile_hash

MATCHING_VERSION = 1
Dimension = Literal[
    "technical_skills",
    "professional_experience",
    "academics",
    "seniority",
    "domain",
    "location",
    "compensation",
    "career_objectives",
    "entrepreneurial_learning",
]
DIMENSIONS = get_args(Dimension)
Text = Annotated[str, Field(min_length=1, max_length=4096)]
UnitNumber = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Weight = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Notes = Annotated[list[Text], Field(max_length=16)]


class Rubric(_ConfigModel):
    # Index is the reviewed level, 0 through 4. Meanings live in versioned policy.
    anchors: Annotated[list[Text], Field(min_length=5, max_length=5)]

    @field_validator("anchors")
    @classmethod
    def distinct_anchors(cls, values):
        if len({value.casefold() for value in values}) != 5:
            raise ValueError("rubric anchors must be distinct")
        return values


class MatchingPolicy(_ConfigModel):
    schema_version: Literal[1]
    policy_version: Text
    rubric_version: Text
    weights: dict[Dimension, Weight]
    rubrics: dict[Dimension, Rubric]
    minimum_coverage: UnitNumber
    minimum_dimension_confidence: UnitNumber

    @model_validator(mode="after")
    def complete_policy(self) -> Self:
        if set(self.weights) != set(DIMENSIONS) or set(self.rubrics) != set(DIMENSIONS):
            raise ValueError("weights and rubrics must include all nine dimensions")
        if not any(self.weights.values()):
            raise ValueError("at least one dimension must have positive weight")
        return self


class DimensionAssessment(_ConfigModel):
    level: Literal[0, 1, 2, 3, 4]
    confidence: UnitNumber
    rationale: Text
    source_evidence: Annotated[list[SourceQuote], Field(min_length=1, max_length=16)]
    profile_refs: Annotated[list[ProfileRef], Field(min_length=1, max_length=32)]
    strengths: Notes | None = None
    gaps: Notes | None = None
    blockers: Notes | None = None
    transferable_skills: Notes | None = None
    recommendations: Notes | None = None

    @field_validator("level", mode="before")
    @classmethod
    def integer_level(cls, value):
        if type(value) is not int:
            raise ValueError("level must be an integer rubric index")
        return value


class MatchingReview(_ConfigModel):
    schema_version: Literal[1]
    job_id: Text
    revision: Annotated[int, Field(ge=1)]
    revision_fingerprint: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    profile_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    policy_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    reviewed_by: Text
    reviewed_on: Text
    assessments: dict[Dimension, DimensionAssessment | None]

    @model_validator(mode="after")
    def reviewed_snapshot(self) -> Self:
        _exact_date(self.reviewed_on)
        if set(self.assessments) != set(DIMENSIONS):
            raise ValueError("all nine dimensions must be explicit, using null for unknown")
        return self


def _hash(value) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(data.encode()).hexdigest()


def policy_hash(policy: MatchingPolicy) -> str:
    return _hash(policy.model_dump(mode="json"))


def matching_template(revision: dict, profile: CandidateProfile, policy: MatchingPolicy) -> dict:
    return {
        "schema_version": 1,
        "job_id": revision["job_id"],
        "revision": revision["number"],
        "revision_fingerprint": revision["fingerprint"],
        "profile_hash": profile_hash(profile),
        "policy_hash": policy_hash(policy),
        "reviewed_by": None,
        "reviewed_on": None,
        "assessments": dict.fromkeys(DIMENSIONS),
    }


def _load(path, model, label):
    try:
        return _load_model(path, model)
    except ConfigurationError:
        # Validation locations can contain arbitrary untrusted dimension/path keys.
        raise ConfigurationError(
            f"Cannot load matching {label}; check readability, safe YAML, "
            "the 1 MiB limit and the matching schema"
        ) from None


def load_policy(path: Path) -> MatchingPolicy:
    return _load(path, MatchingPolicy, "policy")


def load_matching_review(path: Path) -> MatchingReview:
    return _load(path, MatchingReview, "review")


def profile_reference_value(profile_data, ref, *, allow_unknown=False):
    """Resolve a validated dot path without attribute access or container expansion."""
    value = profile_data
    for part in ref.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif (
            isinstance(value, list) and part.isascii() and part.isdigit() and int(part) < len(value)
        ):
            value = value[int(part)]
        else:
            raise ConfigurationError("Matching references an unavailable profile field")
    if (
        value is None
        and not allow_unknown
        or isinstance(value, dict)
        or isinstance(value, list)
        and value != []
    ):
        raise ConfigurationError("Matching references need scalar facts or explicit empty lists")
    return value


def validate_dimension_evidence(assessment, revision, profile_data):
    """Check textual presence for a rating, without treating it as a trusted review."""
    for quote in assessment.source_evidence:
        value = revision["payload"].get(quote.evidence.field)
        if (
            not isinstance(value, str)
            or quote.evidence.end > len(value)
            or value[quote.evidence.start : quote.evidence.end] != quote.text
        ):
            raise ConfigurationError("Matching review contains unsupported source evidence")
    for ref in assessment.profile_refs:
        profile_reference_value(profile_data, ref)


def assess_matching(
    revision: dict, profile: CandidateProfile, policy: MatchingPolicy, review: MatchingReview
) -> dict:
    """Aggregate fixed reviewed dimension inputs, with no inference or state changes."""
    if (review.job_id, review.revision, review.revision_fingerprint) != (
        revision["job_id"],
        revision["number"],
        revision["fingerprint"],
    ):
        raise ConfigurationError("Matching review does not match the selected job revision")
    if review.profile_hash != profile_hash(profile):
        raise ConfigurationError(
            "Matching review profile hash is stale; review the current profile"
        )
    if review.policy_hash != policy_hash(policy):
        raise ConfigurationError("Matching review policy hash is stale; review the current policy")
    reviewed_on = _exact_date(review.reviewed_on)
    for item in [*(profile.education or []), *(profile.experience or [])]:
        if item.status_as_of is not None and _exact_date(item.status_as_of) > reviewed_on:
            raise ConfigurationError(
                "A profile status cannot be attested after the matching review date"
            )
    profile_data = profile.model_dump(mode="json")
    total_weight = sum(Fraction(str(weight)) for weight in policy.weights.values())
    included_weight = Fraction(0)
    weighted_score, weighted_confidence = Fraction(0), Fraction(0)
    results = {}
    for dimension in DIMENSIONS:
        assessment = review.assessments[dimension]
        weight = Fraction(str(policy.weights[dimension]))
        status = "unknown"
        score = None
        if assessment is not None:
            # Validate every assessment, even if confidence/weight excludes it.
            validate_dimension_evidence(assessment, revision, profile_data)
            score = assessment.level * 25
            confidence = Fraction(str(assessment.confidence))
            if weight == 0:
                status = "zero_weight"
            elif confidence < Fraction(str(policy.minimum_dimension_confidence)):
                status = "low_confidence"
            else:
                status = "included"
                included_weight += weight
                weighted_score += weight * score
                weighted_confidence += weight * confidence
        results[dimension] = {
            "status": status,
            "weight": policy.weights[dimension],
            "score": score,
            "rubric_anchor": policy.rubrics[dimension].anchors[assessment.level]
            if assessment
            else None,
            "assessment": assessment.model_dump(mode="json") if assessment else None,
        }
    coverage = included_weight / total_weight
    sufficient = included_weight > 0 and coverage >= Fraction(str(policy.minimum_coverage))
    return {
        "schema_version": 1,
        "matching_version": MATCHING_VERSION,
        "scope": "reviewed_dimensions_only",
        "score_meaning": "configured_fit_not_hiring_probability",
        "status": "scored" if sufficient else "insufficient_coverage",
        "overall_score": round(float(weighted_score / included_weight), 6) if sufficient else None,
        "score_scale": [0, 100],
        "coverage": round(float(coverage), 6),
        "weighted_confidence": round(float(weighted_confidence / included_weight), 6)
        if included_weight
        else None,
        "minimum_coverage": policy.minimum_coverage,
        "minimum_dimension_confidence": policy.minimum_dimension_confidence,
        "job_id": review.job_id,
        "revision": review.revision,
        "revision_fingerprint": review.revision_fingerprint,
        "profile_hash": review.profile_hash,
        "policy_hash": review.policy_hash,
        "policy_version": policy.policy_version,
        "rubric_version": policy.rubric_version,
        "reviewed_by": review.reviewed_by,
        "reviewed_on": review.reviewed_on,
        "review_hash": _hash(review.model_dump(mode="json")),
        "assessment_hash": _hash(
            {"version": MATCHING_VERSION, "review": review.model_dump(mode="json")}
        ),
        "dimensions": results,
        "unassessed_dimensions": [
            key for key, item in results.items() if item["status"] == "unknown"
        ],
    }
