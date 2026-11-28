"""Conservative, evidence-bearing discovery decisions independent of model inference."""

import hashlib
import json
import math
import re
import unicodedata
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from career_intelligence.collection import Posting
from career_intelligence.config import CandidateProfile, FilterPolicy, profile_hash_payload

FILTER_VERSION = 1
Decision = Literal["keep", "review", "exclude"]


class Reason(BaseModel):
    code: str
    field: str
    evidence: str | None = None
    profile_field: str | None = None
    preference: str | None = None


class FilterDecision(BaseModel):
    decision: Decision
    config_hash: str
    filter_version: int = FILTER_VERSION
    location_rank: int | None = None
    reasons: list[Reason] = Field(default_factory=list)


def configuration_hash(profile: CandidateProfile, policy: FilterPolicy) -> str:
    data = {
        "profile": profile_hash_payload(profile),
        "policy": policy.model_dump(mode="json"),
        "filter_version": FILTER_VERSION,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _tokens(text: str) -> list[str]:
    return [
        unicodedata.normalize("NFKC", match.group()).casefold()
        for match in re.finditer(r"\w+", text)
    ]


def _match(text: str, phrase: str) -> str | None:
    words = list(re.finditer(r"\w+", text))
    target = _tokens(phrase)
    if not target:
        return None
    tokens = _tokens(text)
    for start in range(len(tokens) - len(target) + 1):
        if tokens[start : start + len(target)] == target:
            return text[words[start].start() : words[start + len(target) - 1].end()]
    return None


def _aliases(target: str, groups: dict[str, list[str]]) -> list[str]:
    terms = [target]
    for name, aliases in groups.items():
        values = [name, *aliases]
        if any(_match(target, alias) or _tokens(alias) == _tokens(target) for alias in values):
            terms.extend(values)
    return terms


def _pay(post: Posting, profile: CandidateProfile) -> tuple[Decision, Reason]:
    preferences = profile.preferences.compensation
    floor = preferences.fallback_annual_total or preferences.target_annual_total
    profile_field = (
        "preferences.compensation.fallback_annual_total"
        if preferences.fallback_annual_total is not None
        else "preferences.compensation.target_annual_total"
    )
    reason = Reason(code="compensation_unknown", field="compensation", profile_field=profile_field)
    if floor is None:
        reason.code = "compensation_threshold_unknown"
        return "review", reason
    reason.preference = f"{floor:g} {preferences.currency} annual total compensation"
    if not post.compensation:
        return "review", reason
    comparable = []
    uncertain = False
    for component in post.compensation:
        if component.kind != "total_compensation":
            continue
        try:
            source = urlsplit(component.source_url or "")
            source_valid = (
                source.scheme in {"http", "https"}
                and bool(source.hostname)
                and source.username is None
                and source.password is None
                and not re.search(r"[\s\\\x00-\x1f\x7f]", component.source_url or "")
            )
        except ValueError:
            source_valid = False
        amounts = [value for value in (component.minimum, component.maximum) if value is not None]
        valid = (
            component.provenance == "employer_disclosed"
            and component.currency == preferences.currency
            and component.period == "annual"
            and source_valid
            and component.source_date is not None
            and bool(amounts)
            and all(math.isfinite(value) and value > 0 for value in amounts)
            and (
                component.minimum is None
                or component.maximum is None
                or component.minimum <= component.maximum
            )
        )
        if not valid:
            uncertain = True
            continue
        comparable.append(component)
    reason.evidence = json.dumps(
        [component.model_dump(mode="json") for component in post.compensation]
    )
    if any(
        component.minimum is not None and component.minimum >= floor for component in comparable
    ):
        reason.code = "compensation_aligned"
        return "keep", reason
    if (
        comparable
        and not uncertain
        and all(
            component.maximum is not None and component.maximum < floor for component in comparable
        )
    ):
        reason.code = "compensation_below_floor"
        return "exclude", reason
    reason.code = "compensation_not_comparable" if not comparable else "compensation_ambiguous"
    return "review", reason


def evaluate(post: Posting, profile: CandidateProfile, policy: FilterPolicy) -> FilterDecision:
    reasons = []
    verdicts: list[Decision] = []
    matched_role = False
    for index, role in enumerate(profile.preferences.target_roles):
        for term in _aliases(role, policy.role_aliases):
            evidence = _match(post.title, term)
            if evidence:
                matched_role = True
                reasons.append(
                    Reason(
                        code="role_match",
                        field="title",
                        evidence=evidence,
                        profile_field=f"preferences.target_roles.{index}",
                        preference=role,
                    )
                )
                break
        if matched_role:
            break
    if not matched_role:
        verdicts.append("review")
        reasons.append(Reason(code="role_uncertain", field="title", evidence=post.title))

    seniority = next(
        (
            evidence
            for term in policy.excluded_title_seniority
            if (evidence := _match(post.title, term))
        ),
        None,
    )
    if seniority:
        desired = any(_match(post.title, term) for term in profile.preferences.target_seniority)
        verdicts.append("review" if desired else "exclude")
        reasons.append(
            Reason(
                code="seniority_ambiguous" if desired else "seniority_excluded",
                field="title",
                evidence=seniority,
                profile_field="preferences.target_seniority",
            )
        )

    rank = None
    for index, location in enumerate(profile.preferences.locations):
        for term in _aliases(location, policy.location_aliases):
            evidence = _match(post.location or "", term)
            if evidence:
                rank = index
                reasons.append(
                    Reason(
                        code="location_match",
                        field="location",
                        evidence=evidence,
                        profile_field=f"preferences.locations.{index}",
                        preference=location,
                    )
                )
                break
        if rank is not None:
            break
    if rank is None and profile.preferences.locations:
        verdicts.append("review")
        reasons.append(Reason(code="location_uncertain", field="location", evidence=post.location))
    if post.work_authorization is not None:
        verdicts.append("review")
        reasons.append(
            Reason(
                code="work_authorization_requires_review",
                field="work_authorization",
                evidence=post.work_authorization,
                profile_field="work_authorizations",
            )
        )
    if post.required_experience is not None:
        verdicts.append("review")
        reasons.append(
            Reason(
                code="experience_requires_review",
                field="required_experience",
                evidence=post.required_experience,
                profile_field="experience",
            )
        )
    if policy.comparable_pay_filter_enabled:
        verdict, reason = _pay(post, profile)
        verdicts.append(verdict)
        reasons.append(reason)
    else:
        reasons.append(
            Reason(
                code="compensation_filter_disabled",
                field="compensation",
                profile_field="preferences.compensation",
            )
        )
    decision = "exclude" if "exclude" in verdicts else "review" if "review" in verdicts else "keep"
    return FilterDecision(
        decision=decision,
        config_hash=configuration_hash(profile, policy),
        location_rank=rank,
        reasons=reasons,
    )
