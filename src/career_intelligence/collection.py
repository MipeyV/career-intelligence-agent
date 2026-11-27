"""Provider-neutral contracts for collection and preview."""

from datetime import date, datetime
from typing import Literal, Protocol

from pydantic import AwareDatetime, BaseModel, Field, JsonValue

from career_intelligence.config import Company, Source


class Compensation(BaseModel):
    minimum: float | None = None
    maximum: float | None = None
    currency: str | None = None
    period: str | None = None
    kind: str | None = None
    provenance: str | None = None
    source_url: str | None = None
    source_date: date | None = None
    confidence: str | None = None


class Posting(BaseModel):
    source_provider: str
    source_scope: str
    source_post_id: str
    company_key: str
    company: str
    title: str
    location: str | None = None
    remote_policy: str | None = None
    employment_type: str | None = None
    seniority: str | None = None
    publication_date: AwareDatetime | date | None = None
    source_updated_at: AwareDatetime | None = None
    observed_at: AwareDatetime
    application_deadline: AwareDatetime | date | None = None
    expected_start_date: AwareDatetime | date | None = None
    description: str | None = None
    responsibilities: list[str] | None = None
    required_qualifications: list[str] | None = None
    preferred_qualifications: list[str] | None = None
    required_experience: str | None = None
    technical_skills: list[str] | None = None
    academic_requirements: list[str] | None = None
    language_requirements: list[str] | None = None
    work_authorization: str | None = None
    compensation: list[Compensation] | None = None
    source_url: str
    official_application_url: str | None = None
    source_status: Literal["listed"] = "listed"
    raw_record: dict[str, JsonValue] = Field(exclude=True, repr=False)


class CollectionIssue(BaseModel):
    code: str
    count: int = 1
    http_status: int | None = None


class CollectionSnapshot(BaseModel):
    source_key: str
    scope_key: str
    started_at: datetime
    completed_at: datetime
    status: Literal["complete", "partial", "failed"]
    expected_total: int | None = None
    observed_total: int | None = None
    skipped_prospects: int = 0
    issues: list[CollectionIssue] = Field(default_factory=list)
    records: list[Posting] = Field(default_factory=list)


class Connector(Protocol):
    async def collect(self, company: Company, source: Source) -> CollectionSnapshot: ...


class CollectionPreview(BaseModel):
    mode: Literal["fixture", "live"]
    schema_version: Literal[1] = 1
    snapshots: list[CollectionSnapshot]

    @property
    def exit_code(self) -> int:
        if all(snapshot.status == "complete" for snapshot in self.snapshots):
            return 0
        return 3 if any(snapshot.status != "failed" for snapshot in self.snapshots) else 1
