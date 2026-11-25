"""Read and normalize the public Greenhouse Job Board API list response."""

import json
import re
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator

from career_intelligence.collection import CollectionIssue, CollectionSnapshot, Posting
from career_intelligence.collection_http import FetchError, HttpPolicy, utc_now
from career_intelligence.config import CollectionLimits, Company, Source

API_ROOT = "https://boards-api.greenhouse.io/v1/boards"
PostId = Annotated[int, Field(strict=True, gt=0)]
BLOCK_TAGS = {
    "article",
    "blockquote",
    "br",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "section",
    "table",
    "td",
    "tr",
    "ul",
}
HIDDEN_TAGS = {"script", "style", "template", "noscript"}
HTML_TAG = re.compile(r"(?i)</?[a-z][\w:-]*(?=[\s/>])")
ESCAPED_TAG = re.compile(r"(?i)&lt;/?[a-z][\w:-]*(?=[\s/&]|\Z)")
DOUBLE_ESCAPED_TAG = re.compile(r"(?i)&amp;lt;/?[a-z][\w:-]*(?=[\s/&]|\Z)")


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in HIDDEN_TAGS:
            self.hidden.append(tag)
        elif tag in BLOCK_TAGS and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(re.sub(r"\s+", " ", data))


def _description_text(html: str | None) -> str | None:
    if html is None:
        return None
    # Decode outer HTML escaping, leaving inline entities to the HTML parser.
    for escaped_tag in (DOUBLE_ESCAPED_TAG, ESCAPED_TAG):
        if HTML_TAG.search(html):
            break
        if escaped_tag.search(html):
            html = unescape(html)
    parser = _PlainText()
    parser.feed(html)
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line) or None


class _Location(BaseModel):
    name: str | None = None


class _GreenhousePost(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    id: PostId
    title: Annotated[str, Field(min_length=1)]
    internal_job_id: PostId | None = None
    absolute_url: str | None = None
    location: _Location | None = None
    content: str | None = None
    updated_at: AwareDatetime | None = None
    first_published: AwareDatetime | date | None = None
    application_deadline: AwareDatetime | date | None = None

    @field_validator("updated_at", "first_published", "application_deadline", mode="before")
    @classmethod
    def parse_iso_date(cls, value: object) -> date | datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                return date.fromisoformat(value)
            if "T" in value:
                return datetime.fromisoformat(value)
        raise ValueError("expected ISO date or timestamp")

    @field_validator("absolute_url")
    @classmethod
    def validate_link(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or re.search(r"[\s\\\x00-\x1f\x7f]", value)
            or parsed.port == 0
        ):
            raise ValueError("expected absolute HTTP(S) link without credentials")
        return value


def _invalid_constant(value: str) -> None:
    raise ValueError("invalid JSON constant")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _posting(raw: dict, company: Company, scope: str, started_at: datetime) -> Posting:
    post = _GreenhousePost.model_validate(raw)
    return Posting(
        source_provider="greenhouse",
        source_scope=scope,
        source_post_id=str(post.id),
        company_key=company.key,
        company=company.name,
        title=post.title,
        location=None if post.location is None else post.location.name,
        source_updated_at=post.updated_at.astimezone(UTC) if post.updated_at else None,
        publication_date=post.first_published,
        application_deadline=post.application_deadline,
        observed_at=started_at,
        description=_description_text(post.content),
        source_url=f"{API_ROOT}/{scope}/jobs/{post.id}",
        official_application_url=post.absolute_url,
        raw_record=raw,
    )


def _normalize(
    payload: bytes,
    company: Company,
    scope: str,
    started_at: datetime,
    max_items: int,
) -> tuple[list[Posting], list[CollectionIssue], int | None, int, int]:
    try:
        data = json.loads(
            payload, parse_constant=_invalid_constant, object_pairs_hook=_unique_json_object
        )
    except (ValueError, RecursionError):
        raise FetchError("invalid_json") from None
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise FetchError("invalid_envelope")
    jobs = data["jobs"]
    meta = data.get("meta")
    total = meta.get("total") if isinstance(meta, dict) else None
    expected = total if type(total) is int and total >= 0 else None
    issues = []
    if expected is None:
        issues.append(CollectionIssue(code="invalid_total"))
    elif expected != len(jobs):
        issues.append(CollectionIssue(code="count_mismatch"))
    if len(jobs) > max_items:
        issues.append(CollectionIssue(code="item_limit", count=len(jobs) - max_items))

    ids = Counter(raw["id"] for raw in jobs if isinstance(raw, dict) and type(raw.get("id")) is int)
    duplicate_count = sum(count for count in ids.values() if count > 1)
    if duplicate_count:
        issues.append(CollectionIssue(code="duplicate_post_id", count=duplicate_count))
    records = []
    invalid = skipped = 0
    for raw in jobs[:max_items]:
        if not isinstance(raw, dict):
            invalid += 1
            continue
        post_id = raw.get("id")
        if type(post_id) is int and ids[post_id] > 1:
            continue
        if "internal_job_id" in raw and raw["internal_job_id"] is None:
            if type(post_id) is int and post_id > 0:
                skipped += 1
            else:
                invalid += 1
            continue
        try:
            records.append(_posting(raw, company, scope, started_at))
        except (ValidationError, RecursionError):
            invalid += 1
    if invalid:
        issues.append(CollectionIssue(code="invalid_post", count=invalid))
    return records, issues, expected, len(jobs), skipped


class GreenhouseConnector:
    def __init__(
        self,
        limits: CollectionLimits,
        *,
        http: HttpPolicy | None = None,
        fixture: Path | None = None,
        now: Callable[[], datetime] = utc_now,
    ):
        if (http is None) == (fixture is None):
            raise ValueError("provide one Greenhouse input: HTTP or fixture")
        self.http = http
        self.fixture = fixture
        self.limits = limits
        self.now = now

    async def collect(self, company: Company, source: Source) -> CollectionSnapshot:
        scope = source.board_token
        if source.provider != "greenhouse" or scope is None:
            raise ValueError("a Greenhouse source requires a board token")
        started = self.now()
        scope_key = f"greenhouse:{scope}"
        try:
            if self.http is not None:
                payload = await self.http.get(
                    f"{API_ROOT}/{scope}/jobs", params={"content": "true"}
                )
            else:
                payload = self._read_fixture()
            records, issues, expected, observed, skipped = _normalize(
                payload, company, scope, started, self.limits.max_items
            )
        except FetchError as exc:
            return CollectionSnapshot(
                source_key=source.key,
                scope_key=scope_key,
                started_at=started,
                completed_at=self.now(),
                status="failed",
                issues=[CollectionIssue(code=exc.code, http_status=exc.status)],
            )
        return CollectionSnapshot(
            source_key=source.key,
            scope_key=scope_key,
            started_at=started,
            completed_at=self.now(),
            status="partial" if issues else "complete",
            expected_total=expected,
            observed_total=observed,
            skipped_prospects=skipped,
            issues=issues,
            records=records,
        )

    def _read_fixture(self) -> bytes:
        assert self.fixture is not None
        try:
            with self.fixture.open("rb") as stream:
                payload = stream.read(self.limits.max_response_bytes + 1)
        except OSError:
            raise FetchError("fixture_unreadable") from None
        if len(payload) > self.limits.max_response_bytes:
            raise FetchError("response_limit")
        return payload
