"""Deterministic, scoped eligibility from locally reviewed source requirements."""

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from career_intelligence.config import (
    CandidateProfile,
    ConfigurationError,
    _ConfigModel,
    _date_bounds,
    _load_model,
    profile_hash_payload,
)
from career_intelligence.extraction import Evidence

ELIGIBILITY_VERSION = 2
Text = Annotated[str, Field(min_length=1, max_length=4096)]
Status = Literal["eligible", "potentially_eligible", "uncertain", "not_eligible"]
Outcome = Literal["met", "potential", "unknown", "contradiction"]


def _key(value: str) -> str:
    return value.strip().casefold()


def _exact_date(value: str) -> date:
    if len(value) != 10:
        raise ValueError("use an exact YYYY-MM-DD date")
    lower, upper = _date_bounds(value)
    if lower != upper:
        raise ValueError("use an exact YYYY-MM-DD date")
    return lower


class SourceQuote(_ConfigModel):
    model_config = ConfigDict(str_strip_whitespace=False)
    text: Annotated[str, Field(min_length=1, max_length=4096, pattern=r"\S")]
    evidence: Evidence


class _Rule(_ConfigModel):
    id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    mandatory: bool = True
    source: SourceQuote


class _Cutoff(_Rule):
    cutoff: Text

    @field_validator("cutoff")
    @classmethod
    def valid_date(cls, value):
        _date_bounds(value)
        return value


class AvailabilityRule(_Cutoff):
    kind: Literal["availability_by"]


class DeadlineRule(_Cutoff):
    kind: Literal["application_deadline"]


class GraduationRule(_Rule):
    kind: Literal["graduation_window"]
    earliest: Text | None = None
    latest: Text | None = None

    @model_validator(mode="after")
    def valid_window(self) -> Self:
        if self.earliest is None and self.latest is None:
            raise ValueError("a graduation window needs at least one bound")
        for value in (self.earliest, self.latest):
            if value is not None:
                _date_bounds(value)
        if self.earliest and self.latest:
            if _date_bounds(self.earliest)[0] > _date_bounds(self.latest)[1]:
                raise ValueError("graduation bounds must be ordered")
        return self


class ExperienceRule(_Rule):
    kind: Literal["minimum_experience"]
    minimum_years: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    apprenticeship_counts: bool | None = None


class DegreeRule(_Rule):
    kind: Literal["degree"]
    accepted_degrees: Annotated[list[Text], Field(min_length=1, max_length=64)]
    completion: Literal["completed", "expected_or_completed"] | None = None
    graduation_earliest: Text | None = None
    graduation_latest: Text | None = None

    @model_validator(mode="after")
    def bound_degree_window(self) -> Self:
        if self.graduation_earliest is not None or self.graduation_latest is not None:
            if self.completion is None:
                raise ValueError("a bound degree window requires an explicit completion policy")
            for value in (self.graduation_earliest, self.graduation_latest):
                if value is not None:
                    _date_bounds(value)
            if self.graduation_earliest and self.graduation_latest:
                if (
                    _date_bounds(self.graduation_earliest)[0]
                    > _date_bounds(self.graduation_latest)[1]
                ):
                    raise ValueError("degree graduation bounds must be ordered")
        return self


class LanguageRule(_Rule):
    kind: Literal["language"]
    language: Text
    minimum_proficiency: Text | None = None
    proficiency_order: Annotated[list[Text], Field(max_length=32)] | None = None

    @model_validator(mode="after")
    def valid_scale(self) -> Self:
        if self.minimum_proficiency is not None:
            order = [_key(value) for value in self.proficiency_order or []]
            if len(set(order)) != len(order) or _key(self.minimum_proficiency) not in order:
                raise ValueError("minimum proficiency needs a unique explicit ordered scale")
        elif self.proficiency_order is not None:
            raise ValueError("a proficiency scale needs its minimum")
        return self


class AuthorizationRule(_Rule):
    kind: Literal["work_authorization"]
    location: Text
    sponsorship: Literal["available", "not_available", "unknown"]


class GeographyRule(_Rule):
    kind: Literal["geography"]
    allowed_locations: Annotated[list[Text], Field(min_length=1, max_length=64)]


class AllOfRule(_Rule):
    kind: Literal["all_of"]
    conditions: Annotated[list["Constraint"], Field(min_length=1, max_length=32)]


class AnyOfRule(_Rule):
    kind: Literal["any_of"]
    conditions: Annotated[list["Constraint"], Field(min_length=1, max_length=32)]


Constraint = Annotated[
    AvailabilityRule
    | DeadlineRule
    | GraduationRule
    | ExperienceRule
    | DegreeRule
    | LanguageRule
    | AuthorizationRule
    | GeographyRule
    | AllOfRule
    | AnyOfRule,
    Field(discriminator="kind"),
]
AllOfRule.model_rebuild()
AnyOfRule.model_rebuild()


def _walk(rules):
    stack = [(rule, 1) for rule in reversed(rules)]
    count = 0
    while stack:
        rule, depth = stack.pop()
        count += 1
        if count > 128 or depth > 4:
            raise ValueError("requirements exceed 128 total nodes or four levels")
        yield rule, depth
        children = (
            rule.get("conditions", [])
            if isinstance(rule, dict) and rule.get("kind") in ("all_of", "any_of")
            else rule.conditions
            if isinstance(rule, (AllOfRule, AnyOfRule))
            else []
        )
        if isinstance(children, list):
            stack.extend((child, depth + 1) for child in reversed(children))


class CandidateAssertions(_ConfigModel):
    # Explicit completeness is needed before absence can become a contradiction.
    complete_sections: list[Literal["experience", "education", "languages"]] = Field(
        default_factory=list, max_length=3
    )
    # Attested feasible locations, independent of preferences and legal rights.
    work_locations: Annotated[list[Text], Field(max_length=64)] | None = None


class ReviewedRequirements(_ConfigModel):
    schema_version: Literal[1, 2]
    job_id: Text
    revision: Annotated[int, Field(ge=1)]
    revision_fingerprint: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    profile_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    reviewed_by: Text
    reviewed_on: Text
    coverage: Literal["partial", "complete"]
    candidate_assertions: CandidateAssertions = Field(default_factory=CandidateAssertions)
    constraints: Annotated[list[Constraint], Field(max_length=128)]

    @model_validator(mode="before")
    @classmethod
    def bounded_input_tree(cls, data):
        if isinstance(data, dict) and isinstance(data.get("constraints"), list):
            for _ in _walk(data["constraints"]):
                pass
        return data

    @model_validator(mode="after")
    def consistent_review(self) -> Self:
        _exact_date(self.reviewed_on)
        if not self.reviewed_by.strip():
            raise ValueError("reviewer must be identified")
        nodes = list(_walk(self.constraints))
        if len({rule.id for rule, _ in nodes}) != len(nodes):
            raise ValueError("constraint IDs must be unique")
        for rule, depth in nodes:
            if depth > 1 and not rule.mandatory:
                raise ValueError("only top-level constraints may be preferred")
            if self.schema_version == 1 and (
                isinstance(rule, (AllOfRule, AnyOfRule))
                or isinstance(rule, DegreeRule)
                and rule.completion is not None
            ):
                raise ValueError("compound or bound completion requirements need review schema 2")
        sections = self.candidate_assertions.complete_sections
        if len(set(sections)) != len(sections):
            raise ValueError("complete sections must be unique")
        return self


def _hash(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def profile_hash(profile: CandidateProfile) -> str:
    return _hash(profile_hash_payload(profile))


def review_template(revision: dict, profile: CandidateProfile) -> dict:
    return {
        "schema_version": 2,
        "job_id": revision["job_id"],
        "revision": revision["number"],
        "revision_fingerprint": revision["fingerprint"],
        "profile_hash": profile_hash(profile),
        "reviewed_by": None,
        "reviewed_on": None,
        "coverage": "partial",
        "candidate_assertions": {"complete_sections": [], "work_locations": None},
        "constraints": [],
    }


def load_review(path: Path) -> ReviewedRequirements:
    try:
        return _load_model(path, ReviewedRequirements)
    except ConfigurationError:
        # Discriminator validation messages can contain the untrusted tag value.
        raise ConfigurationError(
            "Cannot load requirements review; check readability, the 1 MiB limit, "
            "safe YAML and the review schema"
        ) from None


def _union_days(intervals: list[tuple[int, int]]) -> int:
    merged = []
    for start, end in sorted(intervals):
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return sum(end - start for start, end in merged)


def _combine(outcomes: list[Outcome], *, any_of: bool = False) -> Outcome:
    priority = (
        ("met", "potential", "unknown", "contradiction")
        if any_of
        else ("contradiction", "unknown", "potential", "met")
    )
    return next(value for value in priority if value in outcomes)


def _window_outcome(low, high, earliest, latest):
    lower_min, lower_max = _date_bounds(earliest) if earliest else (date.min, date.min)
    upper_min, upper_max = _date_bounds(latest) if latest else (date.max, date.max)
    if high < lower_min or low > upper_max:
        return "contradiction"
    if low >= lower_max and high <= upper_min:
        return "met"
    return "unknown"


def _degree(profile, rule, assertions, as_of):
    accepted = {_key(value) for value in rule.accepted_degrees}
    alternatives, candidates, refs = [], [], []
    for index, item in enumerate(profile.education or []):
        if _key(item.degree) not in accepted:
            # Unmapped qualifications can still have unresolved equivalence.
            alternatives.append("unknown")
            continue
        refs.append(f"education.{index}")
        actual = None
        if item.completed is True:
            known_on = _exact_date(item.status_as_of)
            actual = (
                (
                    _date_bounds(item.completion_date)[0],
                    min(_date_bounds(item.completion_date)[1], known_on),
                )
                if item.completion_date
                else (date.min, known_on)
            )
        if rule.completion is None:
            outcome = "met" if actual and actual[1] <= as_of else "potential"
            bounds = actual
        elif rule.completion == "completed":
            bounds = actual
            if actual:
                outcome = (
                    "met"
                    if actual[1] <= as_of
                    else "contradiction"
                    if actual[0] > as_of
                    else "unknown"
                )
            elif item.completed is False and _exact_date(item.status_as_of) >= as_of:
                outcome = "contradiction"
            else:
                outcome = "unknown"
        else:
            bounds = actual or (
                _date_bounds(item.expected_graduation) if item.expected_graduation else None
            )
            outcome = (
                "met" if actual and actual[1] <= as_of else "potential" if bounds else "unknown"
            )
        if rule.graduation_earliest or rule.graduation_latest:
            window = (
                _window_outcome(*bounds, rule.graduation_earliest, rule.graduation_latest)
                if bounds
                else "unknown"
            )
            # A forecast outside a window is not verified historical contradiction.
            if window == "contradiction" and actual is None:
                window = "unknown"
            outcome = _combine([outcome, window])
        alternatives.append(outcome)
        candidates.append({"education_index": index, "outcome": outcome})
    complete = "education" in assertions.complete_sections
    if profile.education == [] and complete:
        return "contradiction", "no_declared_degree", ["education"], {}
    if not complete or not alternatives:
        alternatives.append("unknown")
    outcome = _combine(alternatives, any_of=True)
    return outcome, "degree_" + outcome, refs or ["education"], {"candidates": candidates}


def _experience(profile, rule, assertions, as_of):
    definite, possible = [], []
    for episode in profile.experience or []:
        credit = episode.counts_as_professional_experience
        if episode.kind == "apprenticeship":
            if rule.apprenticeship_counts is False:
                credit = False
            elif rule.apprenticeship_counts is None and credit is not False:
                credit = None
        if credit is False:
            continue
        start_low, start_high = _date_bounds(episode.start)
        if episode.end is None:
            end_low = _exact_date(episode.status_as_of) if episode.ongoing is True else start_low
            end_high = (
                min(as_of, _exact_date(episode.status_as_of)) if episode.ongoing is False else as_of
            )
        else:
            end_low, end_high = _date_bounds(episode.end)
            if episode.ongoing is False:
                end_high = min(end_high, _exact_date(episode.status_as_of))
        cap = as_of.toordinal() + 1
        possible.append((start_low.toordinal(), min(cap, end_high.toordinal() + 1)))
        if credit is True and (episode.end is not None or episode.ongoing is True):
            definite.append((start_high.toordinal(), min(as_of, end_low).toordinal()))
    lower = _union_days(definite)
    upper = _union_days(possible) if "experience" in assertions.complete_sections else None
    details = {
        "minimum_days": lower,
        "maximum_days": upper,
        "threshold_days_range": [rule.minimum_years * 365, rule.minimum_years * 366],
    }
    if lower >= rule.minimum_years * 366:
        return "met", "experience_sufficient", ["experience"], details
    if upper is not None and upper < rule.minimum_years * 365:
        return "contradiction", "experience_insufficient", ["experience"], details
    return "unknown", "experience_bounds_uncertain", ["experience"], details


def _evaluate_rule(rule, profile, assertions, as_of):
    if isinstance(rule, DeadlineRule):
        low, high = _date_bounds(rule.cutoff)
        outcome = "met" if as_of <= low else "contradiction" if as_of > high else "unknown"
        return outcome, "deadline_" + outcome, [], {}
    if isinstance(rule, AvailabilityRule):
        value = profile.preferences.availability
        if value is None:
            return "unknown", "availability_unknown", ["preferences.availability"], {}
        low, high = _date_bounds(value)
        cutoff_low, cutoff_high = _date_bounds(rule.cutoff)
        outcome = (
            "met" if high <= cutoff_low else "contradiction" if low > cutoff_high else "unknown"
        )
        return outcome, "availability_" + outcome, ["preferences.availability"], {}
    if isinstance(rule, GraduationRule):
        dates = [education.expected_graduation for education in profile.education or []]
        lower = _date_bounds(rule.earliest)[1] if rule.earliest else date.min
        upper = _date_bounds(rule.latest)[0] if rule.latest else date.max
        fits = any(
            value is not None
            and lower <= _date_bounds(value)[0]
            and _date_bounds(value)[1] <= upper
            for value in dates
        )
        return (
            ("potential" if fits else "unknown"),
            (
                "graduation_expected_in_window"
                if fits
                else "graduation_unverified_or_outside_window"
            ),
            ["education"],
            {},
        )
    if isinstance(rule, ExperienceRule):
        return _experience(profile, rule, assertions, as_of)
    if isinstance(rule, DegreeRule):
        return _degree(profile, rule, assertions, as_of)
    if isinstance(rule, LanguageRule):
        matches = [
            item for item in profile.languages or [] if _key(item.name) == _key(rule.language)
        ]
        if not matches:
            complete = "languages" in assertions.complete_sections
            return (
                ("contradiction" if complete else "unknown"),
                ("language_absent_from_complete_profile" if complete else "language_unknown"),
                ["languages"],
                {},
            )
        if rule.minimum_proficiency is None:
            return "met", "language_declared", ["languages"], {}
        levels = {_key(item.proficiency) if item.proficiency else None for item in matches}
        order = [_key(value) for value in rule.proficiency_order]
        if len(levels) != 1 or None in levels or not levels <= set(order):
            return "unknown", "language_level_unmapped_or_conflicting", ["languages"], {}
        met = order.index(next(iter(levels))) >= order.index(_key(rule.minimum_proficiency))
        return (
            ("met" if met else "contradiction"),
            "language_level_" + ("met" if met else "below_minimum"),
            ["languages"],
            {},
        )
    if isinstance(rule, AuthorizationRule):
        statuses = {
            item.status
            for item in profile.work_authorizations or []
            if _key(item.location) == _key(rule.location)
        }
        if statuses == {"authorized"}:
            return "met", "authorization_declared", ["work_authorizations"], {}
        if statuses == {"requires_sponsorship"}:
            if rule.sponsorship == "not_available":
                return "contradiction", "sponsorship_unavailable", ["work_authorizations"], {}
            if rule.sponsorship == "available":
                return (
                    "potential",
                    "sponsorship_still_requires_confirmation",
                    ["work_authorizations"],
                    {},
                )
        return "unknown", "authorization_unknown_or_conflicting", ["work_authorizations"], {}
    if isinstance(rule, GeographyRule):
        feasible = assertions.work_locations
        if feasible is None:
            return "unknown", "feasible_work_locations_unknown", [], {}
        overlap = {_key(value) for value in feasible} & {
            _key(value) for value in rule.allowed_locations
        }
        return (
            ("met" if overlap else "contradiction"),
            "geography_" + ("met" if overlap else "outside_attested_locations"),
            ["review.candidate_assertions.work_locations"],
            {},
        )
    raise ConfigurationError("Unsupported eligibility constraint")


def _assess_node(rule, revision, profile, assertions, as_of):
    quote = rule.source
    field = revision["payload"].get(quote.evidence.field)
    if (
        not isinstance(field, str)
        or quote.evidence.end > len(field)
        or field[quote.evidence.start : quote.evidence.end] != quote.text
    ):
        raise ConfigurationError("Requirements review contains unsupported source evidence")
    children = None
    if isinstance(rule, (AllOfRule, AnyOfRule)):
        # Validate and evaluate every branch, including alternatives not needed to pass.
        children = [
            _assess_node(child, revision, profile, assertions, as_of) for child in rule.conditions
        ]
        outcome = _combine(
            [child["outcome"] for child in children], any_of=isinstance(rule, AnyOfRule)
        )
        code, refs, details = (
            rule.kind + "_" + outcome,
            sorted({ref for child in children for ref in child["profile_refs"]}),
            {},
        )
    else:
        outcome, code, refs, details = _evaluate_rule(rule, profile, assertions, as_of)
    result = {
        "id": rule.id,
        "kind": rule.kind,
        "mandatory": rule.mandatory,
        "requirement": rule.model_dump(exclude={"id", "kind", "mandatory", "source", "conditions"}),
        "outcome": outcome,
        "code": code,
        "source": quote.model_dump(),
        "profile_refs": refs,
        "details": details,
    }
    if children is not None:
        result["conditions"] = children
    return result


def assess_eligibility(
    revision: dict, profile: CandidateProfile, review: ReviewedRequirements, *, as_of: date
) -> dict:
    """Assess only reviewed constraints, with no model calls or persistence."""
    if type(as_of) is not date:
        raise ConfigurationError("Eligibility requires an exact as-of calendar date")
    if (review.job_id, review.revision, review.revision_fingerprint) != (
        revision["job_id"],
        revision["number"],
        revision["fingerprint"],
    ):
        raise ConfigurationError("Requirements review does not match the selected job revision")
    if review.profile_hash != profile_hash(profile):
        raise ConfigurationError(
            "Requirements review profile hash is stale; review the current profile"
        )
    for section in review.candidate_assertions.complete_sections:
        if getattr(profile, section) is None:
            raise ConfigurationError("An unknown profile section cannot be declared complete")
    reviewed_on = _exact_date(review.reviewed_on)
    for item in [*(profile.education or []), *(profile.experience or [])]:
        if item.status_as_of is not None and _exact_date(item.status_as_of) > reviewed_on:
            raise ConfigurationError("A profile status cannot be attested after the review date")
    results = [
        _assess_node(rule, revision, profile, review.candidate_assertions, as_of)
        for rule in review.constraints
    ]
    mandatory = [item["outcome"] for item in results if item["mandatory"]]
    status: Status
    if "contradiction" in mandatory:
        status = "not_eligible"
    elif not results or review.coverage != "complete" or "unknown" in mandatory:
        status = "uncertain"
    elif any(item["outcome"] != "met" for item in results):
        status = "potentially_eligible"
    else:
        status = "eligible"
    return {
        "schema_version": 2,
        "eligibility_version": ELIGIBILITY_VERSION,
        "scope": "reviewed_constraints_only",
        "status": status,
        "as_of": as_of.isoformat(),
        "job_id": review.job_id,
        "revision": review.revision,
        "revision_fingerprint": review.revision_fingerprint,
        "profile_hash": review.profile_hash,
        "review_hash": _hash(review.model_dump(mode="json")),
        "assessment_hash": _hash(
            {
                "version": ELIGIBILITY_VERSION,
                "review": review.model_dump(mode="json"),
                "as_of": as_of.isoformat(),
            }
        ),
        "coverage": review.coverage,
        "rules": results,
        "unassessed_kinds": sorted(
            {
                "availability_by",
                "application_deadline",
                "graduation_window",
                "minimum_experience",
                "degree",
                "language",
                "work_authorization",
                "geography",
            }
            - {item.kind for item, _ in _walk(review.constraints)}
        ),
    }
