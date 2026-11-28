"""Validated local configuration. Loading never creates state or makes HTTP requests."""

from __future__ import annotations

import calendar
import ipaddress
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from yaml.events import AliasEvent
from yaml.nodes import MappingNode

Text = Annotated[str, Field(min_length=1)]
Key = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
PositiveNumber = Annotated[float, Field(gt=0, allow_inf_nan=False)]
NonnegativeNumber = Annotated[float, Field(ge=0, allow_inf_nan=False)]
ConfigPath = Annotated[Path, Field(strict=False)]
MAX_CONFIG_BYTES = 1_048_576


class ConfigurationError(ValueError):
    """An actionable configuration error safe to print without input values."""


class _ConfigModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, str_strip_whitespace=True, hide_input_in_errors=True
    )

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def validate_schema_version_type(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value


def _date_bounds(value: str) -> tuple[date, date]:
    """Validate ISO year/month/day precision without inventing an exact date."""
    if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        raise ValueError("use YYYY, YYYY-MM or YYYY-MM-DD")
    parts = [int(part) for part in value.split("-")]
    year = parts[0]
    month = parts[1] if len(parts) >= 2 else 1
    day = parts[2] if len(parts) == 3 else 1
    try:
        lower = date(year, month, day)
        upper_month = month if len(parts) >= 2 else 12
        upper_day = day if len(parts) == 3 else calendar.monthrange(year, upper_month)[1]
        return lower, date(year, upper_month, upper_day)
    except ValueError:
        raise ValueError("invalid calendar date") from None


def _status_date(value: str | None) -> date:
    if value is None or len(value) != 10:
        raise ValueError("a dated status requires YYYY-MM-DD")
    lower, upper = _date_bounds(value)
    if lower != upper:
        raise ValueError("a dated status requires YYYY-MM-DD")
    return lower


class Education(_ConfigModel):
    institution: Text
    degree: Text
    field: Text | None = None
    expected_graduation: Text | None = None
    completed: bool | None = None
    completion_date: Text | None = None
    status_as_of: Text | None = None
    evidence: list[Text] = Field(default_factory=list)

    @field_validator("expected_graduation", "completion_date")
    @classmethod
    def validate_graduation(cls, value: str | None) -> str | None:
        if value is not None:
            _date_bounds(value)
        return value

    @model_validator(mode="after")
    def validate_completion(self) -> Self:
        if self.completed is None:
            if self.completion_date is not None or self.status_as_of is not None:
                raise ValueError("completion metadata needs an explicit completed status")
            return self
        known_on = _status_date(self.status_as_of)
        if not self.evidence:
            raise ValueError("an explicit completion status requires evidence")
        if self.completion_date is not None:
            if self.completed is not True:
                raise ValueError("a completion date requires completed true")
            if _date_bounds(self.completion_date)[0] > known_on:
                raise ValueError("completion cannot follow its status attestation")
        return self


class Experience(_ConfigModel):
    employer: Text
    role: Text
    start: Text
    end: Text | None = None
    kind: Literal["employment", "apprenticeship", "internship", "other"]
    counts_as_professional_experience: bool | None = None
    ongoing: bool | None = None
    status_as_of: Text | None = None
    evidence: list[Text] = Field(default_factory=list)

    @field_validator("start", "end")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if value is not None:
            _date_bounds(value)
        return value

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.end and _date_bounds(self.start)[0] > _date_bounds(self.end)[1]:
            raise ValueError("experience end must not precede start")
        if self.ongoing is None:
            if self.status_as_of is not None:
                raise ValueError("status date requires an explicit ongoing status")
            return self
        known_on = _status_date(self.status_as_of)
        if not self.evidence:
            raise ValueError("an explicit ongoing status requires evidence")
        if _date_bounds(self.start)[0] > known_on:
            raise ValueError("an employment status cannot precede its possible start")
        if self.ongoing and self.end is not None:
            raise ValueError("ongoing employment cannot have an end date")
        if self.end is not None and _date_bounds(self.end)[0] > known_on:
            raise ValueError("ended employment cannot end after its status attestation")
        return self


class Skill(_ConfigModel):
    name: Text
    evidence: list[Text] = Field(default_factory=list)


class Language(_ConfigModel):
    name: Text
    proficiency: Text | None = None
    evidence: list[Text] = Field(default_factory=list)


class WorkAuthorization(_ConfigModel):
    location: Text
    status: Literal["authorized", "requires_sponsorship", "unknown"] = "unknown"
    evidence: list[Text] = Field(default_factory=list)


class Project(_ConfigModel):
    name: Text
    description: Text
    skills: list[Text] = Field(default_factory=list)
    evidence: list[Text] = Field(default_factory=list)


class CompensationPreferences(_ConfigModel):
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    target_annual_total: PositiveNumber | None = None
    fallback_annual_total: PositiveNumber | None = None

    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        if self.fallback_annual_total is not None and (
            self.target_annual_total is None
            or self.fallback_annual_total > self.target_annual_total
        ):
            raise ValueError("fallback requires a target and must not exceed it")
        return self


class CareerPreferences(_ConfigModel):
    availability: Text | None = None
    target_roles: Annotated[list[Text], Field(min_length=1)]
    target_seniority: list[Text] = Field(default_factory=list)
    maximum_experience_years: NonnegativeNumber | None = None
    locations: list[Text] = Field(default_factory=list)
    compensation: CompensationPreferences
    objectives: list[Text] = Field(default_factory=list)

    @field_validator("availability")
    @classmethod
    def validate_availability(cls, value: str | None) -> str | None:
        if value is not None:
            _date_bounds(value)
        return value


class CandidateProfile(_ConfigModel):
    schema_version: Literal[1]
    education: list[Education] | None = None
    experience: list[Experience] | None = None
    skills: list[Skill] | None = None
    languages: list[Language] | None = None
    work_authorizations: list[WorkAuthorization] | None = None
    projects: list[Project] | None = None
    preferences: CareerPreferences


def profile_hash_payload(profile: CandidateProfile) -> dict:
    """Preserve legacy hashes while additive attested facts remain unknown.

    Serialization still exposes nulls. Only this hash view omits the new null fields;
    known statuses, dates and evidence remain part of the canonical profile snapshot.
    """
    payload = profile.model_dump(mode="json")
    for section, fields in (
        ("education", ("completed", "completion_date", "status_as_of")),
        ("experience", ("ongoing", "status_as_of")),
    ):
        for item in payload[section] or []:
            for name in fields:
                if item[name] is None:
                    del item[name]
    return payload


class Source(_ConfigModel):
    key: Key
    provider: Literal[
        "greenhouse",
        "lever",
        "ashby",
        "smartrecruiters",
        "rss",
        "official_careers",
        "authorized_api",
        "web_search",
        "email",
    ]
    board_token: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$")] | None = None
    enabled: bool = False
    disabled_reason: Text | None = None
    official_careers_url: Text | None = None

    @field_validator("official_careers_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = urlsplit(value)
            port = parsed.port
            valid = (
                parsed.scheme == "https"
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and not re.search(r"[\s\\]", value)
                and (port is None or 1 <= port <= 65535)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("use an absolute HTTPS URL without credentials or whitespace")
        return value

    @model_validator(mode="after")
    def validate_activation(self) -> Self:
        if not self.enabled and not self.disabled_reason:
            raise ValueError("disabled sources require a disabled_reason")
        if self.enabled:
            if self.provider != "greenhouse":
                raise ValueError("only Greenhouse sources can be configured as enabled in Phase 1")
            if not self.board_token or not self.official_careers_url:
                raise ValueError("enabled sources require a board_token and official_careers_url")
            if self.disabled_reason:
                raise ValueError("enabled sources must not have a disabled_reason")
        return self


class Company(_ConfigModel):
    key: Key
    name: Text
    category: Literal["big-tech-ai", "quantitative-finance", "banking-insurance-asset-management"]
    sources: list[Source] = Field(default_factory=list)
    disabled_reason: Text | None = None

    @model_validator(mode="after")
    def validate_sources(self) -> Self:
        if not any(source.enabled for source in self.sources) and not self.disabled_reason:
            raise ValueError("companies without enabled sources require a disabled_reason")
        if any(source.enabled for source in self.sources) and self.disabled_reason:
            raise ValueError("companies with enabled sources must not have a disabled_reason")
        return self


class CompanyWatchlist(_ConfigModel):
    schema_version: Literal[1]
    companies: list[Company]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        company_keys = [company.key for company in self.companies]
        source_keys = [source.key for company in self.companies for source in company.sources]
        if len(set(company_keys)) != len(company_keys):
            raise ValueError("company keys must be unique")
        if len(set(source_keys)) != len(source_keys):
            raise ValueError("source keys must be unique across the watchlist")
        scopes = [
            (source.provider, source.board_token)
            for company in self.companies
            for source in company.sources
            if source.enabled
        ]
        if len(set(scopes)) != len(scopes):
            raise ValueError("enabled provider/board scopes must be unique")
        return self


class CollectionLimits(_ConfigModel):
    connect_timeout_seconds: PositiveNumber = 5
    read_timeout_seconds: PositiveNumber = 20
    max_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    max_response_bytes: Annotated[int, Field(ge=1)] = 10 * 1024 * 1024
    max_retry_wait_seconds: NonnegativeNumber = 60
    run_deadline_seconds: PositiveNumber = 300
    min_request_interval_seconds: PositiveNumber = 1
    max_pages: Annotated[int, Field(ge=1)] = 100
    max_items: Annotated[int, Field(ge=1)] = 10000


class ReconciliationPolicy(_ConfigModel):
    complete_omissions_required: Annotated[int, Field(ge=2)] = 2
    minimum_absence_hours: Annotated[float, Field(ge=24, allow_inf_nan=False)] = 24
    depletion_fraction: Annotated[float, Field(gt=0, lt=1, allow_inf_nan=False)] = 0.5


class FilterPolicy(_ConfigModel):
    role_aliases: dict[Text, list[Text]] = Field(default_factory=dict)
    location_aliases: dict[Text, list[Text]] = Field(default_factory=dict)
    excluded_title_seniority: list[Text] = Field(default_factory=list)
    comparable_pay_filter_enabled: bool = False


class ExtractionLimits(_ConfigModel):
    max_span_hints: Annotated[int, Field(ge=0, le=256)] = 64
    max_input_bytes: Annotated[int, Field(ge=1, le=1_048_576)] = 65_536
    max_output_bytes: Annotated[int, Field(ge=1, le=1_048_576)] = 131_072
    max_output_tokens: Annotated[int, Field(ge=1, le=32768)] = 4096
    timeout_seconds: Annotated[float, Field(gt=0, le=300, allow_inf_nan=False)] = 30


class MatchingProposalLimits(ExtractionLimits):
    """Independent bounds for local matching-response replay and its input contract."""


class OllamaSettings(_ConfigModel):
    model: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*"
            r"(?::[A-Za-z0-9][A-Za-z0-9._-]*)?$",
        ),
    ]
    base_url: str = "http://127.0.0.1:11434"
    num_ctx: Annotated[int, Field(ge=512, le=131072)] = 8192
    seed: Annotated[int, Field(ge=0, le=2_147_483_647)] = 0
    max_response_bytes: Annotated[int, Field(ge=1024, le=10_485_760)] = 2_097_152

    @field_validator("model")
    @classmethod
    def local_model_only(cls, value: str) -> str:
        if value.casefold().endswith(("-cloud", ":cloud")):
            raise ValueError("use an installed local model, not a cloud model")
        return value

    @field_validator("base_url")
    @classmethod
    def loopback_endpoint(cls, value: str) -> str:
        try:
            parts = urlsplit(value)
            valid = (
                parts.scheme == "http"
                and ipaddress.ip_address(parts.hostname).is_loopback
                and parts.username is None
                and parts.password is None
                and parts.path in {"", "/"}
                and not parts.query
                and not parts.fragment
                and (parts.port is None or 1 <= parts.port <= 65535)
                and not re.search(r"[\s\\]", value)
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("use an HTTP loopback IP URL without credentials, path or query")
        return value.rstrip("/")


class Settings(_ConfigModel):
    schema_version: Literal[1]
    profile_file: ConfigPath
    companies_file: ConfigPath
    database_path: ConfigPath
    collection: CollectionLimits = Field(default_factory=CollectionLimits)
    reconciliation: ReconciliationPolicy = Field(default_factory=ReconciliationPolicy)
    filters: FilterPolicy = Field(default_factory=FilterPolicy)
    extraction: ExtractionLimits = Field(default_factory=ExtractionLimits)
    matching_proposals: MatchingProposalLimits = Field(default_factory=MatchingProposalLimits)
    ollama: OllamaSettings | None = None

    @field_validator("profile_file", "companies_file", "database_path", mode="before")
    @classmethod
    def validate_path(cls, value: object) -> object:
        if isinstance(value, str) and (not value.strip() or "\x00" in value):
            raise ValueError("file paths must not be blank or contain null characters")
        return value


class _SafeConfigLoader(yaml.SafeLoader):
    """Disallow aliases/duplicate keys; leave ISO date precision as text."""

    yaml_implicit_resolvers = {
        key: [(tag, regex) for tag, regex in resolvers if tag != "tag:yaml.org,2002:timestamp"]
        for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }

    def compose_node(self, parent: object, index: object) -> object:
        if self.check_event(AliasEvent):
            raise yaml.YAMLError("YAML aliases are not supported")
        return super().compose_node(parent, index)

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict:
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise yaml.YAMLError("mapping keys must be unique strings")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _load_model[T: BaseModel](path: Path, model: type[T]) -> T:
    try:
        with path.open("rb") as stream:
            payload = stream.read(MAX_CONFIG_BYTES + 1)
    except OSError:
        raise ConfigurationError(
            f"Cannot read {path}; check that the file exists and is readable"
        ) from None
    if len(payload) > MAX_CONFIG_BYTES:
        raise ConfigurationError(f"Configuration file exceeds the 1 MiB limit: {path}")
    try:
        data = yaml.load(payload.decode("utf-8-sig"), Loader=_SafeConfigLoader)
    except (yaml.YAMLError, UnicodeError, RecursionError):
        raise ConfigurationError(
            f"Invalid YAML in {path}; use UTF-8, unique string keys, no aliases or custom tags"
        ) from None
    try:
        return model.model_validate(data)
    except (ValidationError, RecursionError) as exc:
        if isinstance(exc, ValidationError):
            details = "; ".join(
                f"{'.'.join(map(str, error['loc'])) or 'root'}: {error['msg']}"
                for error in exc.errors(
                    include_input=False, include_context=False, include_url=False
                )
            )
        else:
            details = "configuration nesting exceeds supported limits"
        raise ConfigurationError(f"Invalid configuration in {path}: {details}") from None


@dataclass(frozen=True)
class LoadedConfiguration:
    settings: Settings
    profile: CandidateProfile
    watchlist: CompanyWatchlist


def load_configuration(path: Path | str) -> LoadedConfiguration:
    """Resolve file paths relative to settings, without creating the database."""
    try:
        settings_path = Path(path).resolve()
        settings = _load_model(settings_path, Settings)
        settings = settings.model_copy(
            update={
                key: (settings_path.parent / getattr(settings, key)).resolve()
                for key in ("profile_file", "companies_file", "database_path")
            }
        )
        profile = _load_model(settings.profile_file, CandidateProfile)
        watchlist = _load_model(settings.companies_file, CompanyWatchlist)
    except (OSError, ValueError) as exc:
        if isinstance(exc, ConfigurationError):
            raise
        raise ConfigurationError(
            "Cannot resolve configuration paths; check the settings file"
        ) from None
    return LoadedConfiguration(settings, profile, watchlist)
