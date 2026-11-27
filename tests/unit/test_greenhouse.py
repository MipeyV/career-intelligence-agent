import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from career_intelligence.collection_http import HttpPolicy
from career_intelligence.config import CollectionLimits, Company
from career_intelligence.greenhouse import GreenhouseConnector


@pytest.fixture
def company():
    return Company.model_validate(
        {
            "key": "example",
            "name": "Example Company",
            "category": "big-tech-ai",
            "sources": [
                {
                    "key": "example-board",
                    "enabled": True,
                    "provider": "greenhouse",
                    "board_token": "example",
                    "official_careers_url": "https://example.com/jobs",
                }
            ],
        }
    )


@pytest.mark.parametrize(
    ("filename", "status", "count", "codes"),
    [
        ("initial", "complete", 2, set()),
        ("empty", "complete", 0, set()),
        ("malformed", "failed", 0, {"invalid_json"}),
        ("partial", "partial", 1, {"count_mismatch", "duplicate_post_id", "invalid_post"}),
        ("missing", "failed", 0, {"fixture_unreadable"}),
    ],
)
def test_fixture_snapshots(repository_root, company, filename, status, count, codes):
    connector = GreenhouseConnector(
        CollectionLimits(),
        fixture=repository_root / "tests/fixtures/greenhouse" / f"{filename}.json",
    )
    snapshot = asyncio.run(connector.collect(company, company.sources[0]))
    assert snapshot.status == status
    assert len(snapshot.records) == count
    assert {issue.code for issue in snapshot.issues} == codes
    if filename == "initial":
        assert snapshot.observed_total == snapshot.expected_total == 3
        assert snapshot.skipped_prospects == 1
        first, second = snapshot.records
        assert first.source_post_id == "101"
        assert first.source_scope == "example"
        assert first.description == "Build models & tools.\nEvaluate results."
        assert first.location == "Example City"
        assert first.official_application_url == "https://example.com/careers?gh_jid=101"
        assert first.source_updated_at == datetime(2026, 4, 4, 16, tzinfo=UTC)
        assert first.raw_record["internal_job_id"] == 202
        assert first.publication_date is first.compensation is first.seniority is None
        assert second.description is second.location is second.official_application_url is None
        assert "raw_record" not in snapshot.model_dump_json()


class FakeClock:
    elapsed = 0.0
    today = datetime(2026, 4, 4, tzinfo=UTC)

    def now(self):
        return self.today + timedelta(seconds=self.elapsed)

    def monotonic(self):
        return self.elapsed

    async def sleep(self, seconds):
        self.elapsed += seconds


def collect_mock(company, handler, **limit_overrides):
    limits = CollectionLimits(**limit_overrides)
    clock = FakeClock()

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            policy = HttpPolicy(
                client,
                limits,
                monotonic=clock.monotonic,
                now=clock.now,
                sleep=clock.sleep,
                jitter=lambda: 0,
            )
            connector = GreenhouseConnector(limits, http=policy, now=clock.now)
            return await connector.collect(company, company.sources[0])

    return asyncio.run(run()), clock


@pytest.mark.parametrize(
    "retry_after",
    [
        "3",
        format_datetime(FakeClock.today + timedelta(seconds=3), usegmt=True),
    ],
)
def test_http_requests_retry_and_normalize(company, retry_after):
    attempts = []

    def handler(request):
        assert request.method == "GET"
        assert (
            str(request.url)
            == "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true"
        )
        assert "authorization" not in request.headers
        assert request.extensions["timeout"]["connect"] == 5
        assert request.extensions["timeout"]["read"] == 20
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": retry_after})
        if len(attempts) == 2:
            raise httpx.ReadTimeout("sensitive detail", request=request)
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 1,
                        "title": "  Example title  ",
                        "first_published": "2026-04-04",
                        "application_deadline": "2026-06-04T09:00:00Z",
                        "content": (
                            "<p>One &lt;hr&gt;</p><script>hide</script><p>Two <b>bold</b></p>"
                        ),
                    }
                ],
                "meta": {"total": 1},
            },
        )

    snapshot, clock = collect_mock(company, handler)
    assert len(attempts) == 3
    assert clock.elapsed == 5
    assert snapshot.status == "complete"
    post = snapshot.records[0]
    assert post.title == "Example title"
    assert post.description == "One <hr>\nTwo bold"
    assert post.publication_date.isoformat() == "2026-04-04"
    assert post.application_deadline == datetime(2026, 6, 4, 9, tzinfo=UTC)
    assert post.source_updated_at is None


@pytest.mark.parametrize(
    ("status", "retry_after", "attempts", "issue"),
    [
        (503, "", 3, "http_status"),
        (404, "", 1, "http_status"),
        (302, "", 1, "http_status"),
        (429, "120", 1, "retry_deferred"),
    ],
)
def test_http_failures_have_bounded_attempts(company, status, retry_after, attempts, issue):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            headers={"Retry-After": retry_after, "Location": "https://example.com/elsewhere"},
        )

    snapshot, _ = collect_mock(company, handler)
    assert len(requests) == attempts
    assert snapshot.status == "failed"
    assert snapshot.issues[0].code == issue
    assert snapshot.issues[0].http_status == status


def test_request_has_hard_deadline(company):
    async def slow_response(request):
        await asyncio.sleep(1)
        pytest.fail("response ran beyond the deadline")

    snapshot, _ = collect_mock(company, slow_response, run_deadline_seconds=0.01)
    assert snapshot.status == "failed"
    assert snapshot.issues[0].code == "run_deadline"


class ChunkedResponse(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"x" * 10
        yield b"x" * 10
        pytest.fail("body was read beyond the size limit")


def test_streamed_response_is_bounded(company):
    snapshot, _ = collect_mock(
        company,
        lambda request: httpx.Response(200, stream=ChunkedResponse()),
        max_response_bytes=15,
    )
    assert snapshot.status == "failed"
    assert snapshot.issues[0].code == "response_limit"


@pytest.mark.parametrize(
    ("content", "issue"),
    [
        (b'{"jobs":{}}', "invalid_envelope"),
        (b'{"jobs":[],"jobs":[],"meta":{"total":0}}', "invalid_json"),
        (b'{"jobs":[],"meta":{"total":true}}', "invalid_total"),
        (b'{"jobs":[{"id":1,"title":"A"},{"id":2,"title":"B"}],"meta":{"total":2}}', "item_limit"),
    ],
)
def test_suspicious_payloads_never_complete(company, content, issue):
    snapshot, _ = collect_mock(
        company, lambda request: httpx.Response(200, content=content), max_items=1
    )
    assert snapshot.status != "complete"
    assert snapshot.issues[0].code == issue
    if issue == "item_limit":
        assert len(snapshot.records) == 1
