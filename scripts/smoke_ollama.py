"""Opt-in local inference on a fixed synthetic posting and a disposable database."""

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

import httpx
from sqlalchemy.exc import SQLAlchemyError

from career_intelligence.collection import CollectionPreview, CollectionSnapshot, Posting
from career_intelligence.config import ExtractionLimits, OllamaSettings
from career_intelligence.extraction import JobExtraction, extract_revision
from career_intelligence.ollama_provider import OllamaProvider
from career_intelligence.storage import StorageError, Store, get_revision, list_jobs

FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/extraction/smoke-posting.json"


class _ObservedTransport(httpx.AsyncBaseTransport):
    """Record safe route/status timings; the provider still enforces HTTP bounds."""

    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport(retries=0)
        self.requests = []
        self.closed = False

    async def handle_async_request(self, request):
        record = {"route": request.url.path, "status": None}
        self.requests.append(record)
        print(
            json.dumps({"event": "smoke_http_started", "route": request.url.path}), file=sys.stderr
        )
        start = time.perf_counter()
        try:
            response = await self.inner.handle_async_request(request)
            record["status"] = response.status_code
            return response
        finally:
            record["headers_seconds"] = time.perf_counter() - start

    async def aclose(self):
        if not self.closed:
            self.closed = True
            await self.inner.aclose()


async def _invoke(revision, settings, limits, observed):
    try:
        return await extract_revision(
            revision,
            OllamaProvider(settings, limits, transport=observed),
            provider_name="ollama",
            model_name=settings.model,
            limits=limits,
        )
    finally:
        await observed.aclose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        required=True,
        help="Explicitly allow one local inference attempt",
    )
    parser.add_argument("--model", required=True, help="An already installed local model")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=90, help="Seconds, at most 120")
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 120:
        parser.error("--timeout must be positive and at most 120 seconds")
    try:
        settings = OllamaSettings(model=args.model, base_url=args.base_url, num_ctx=4096)
        limits = ExtractionLimits(
            timeout_seconds=args.timeout,
            max_input_bytes=4096,
            max_output_bytes=32768,
            max_output_tokens=1024,
        )
    except ValueError:
        parser.error("Use a valid installed local model and HTTP loopback IP endpoint")
    start = time.perf_counter()
    try:
        with FIXTURE.open("rb") as stream:
            payload = stream.read(4097)
        if len(payload) > 4096:
            raise ValueError("Synthetic fixture exceeds its fixed limit")
        post = Posting.model_validate_json(payload)
        with tempfile.TemporaryDirectory(prefix="career-live-smoke-") as folder:
            database = Path(folder) / "synthetic.sqlite3"
            snapshot = CollectionSnapshot(
                source_key="synthetic",
                scope_key="fixture:local-smoke",
                started_at=post.observed_at,
                completed_at=post.observed_at,
                status="complete",
                records=[post],
            )
            with Store(database) as store:
                store.ingest(CollectionPreview(mode="fixture", snapshots=[snapshot]))
            revision = get_revision(database, list_jobs(database)[0]["id"])
            before = database.read_bytes()
            observed = _ObservedTransport()
            result = asyncio.run(_invoke(revision, settings, limits, observed))
            unchanged = before == database.read_bytes()
    except (OSError, ValueError, StorageError, SQLAlchemyError):
        print(json.dumps({"event": "synthetic_smoke_setup_failed"}))
        return 1
    extraction = result.extraction
    facts = extraction.facts() if extraction is not None else []
    # This single fixture discloses a skill requirement and no other job facts.
    unknown_fields = set(JobExtraction.model_fields) - {
        "technical_skills",
        "required_qualifications",
    }
    unknowns_preserved = extraction is not None and all(
        getattr(extraction, name) is None for name in unknown_fields
    )
    passed = result.status == "complete" and bool(facts) and unknowns_preserved and unchanged
    print(
        json.dumps(
            {
                "report_version": 1,
                "fixture": "synthetic-python-role-v1",
                "model": settings.model,
                "status": result.status,
                "smoke_passed": passed,
                "input_sha256": result.input_sha256,
                "revision_fingerprint": result.revision_fingerprint,
                "prompt_version": result.prompt_version,
                "provider_attempts": result.provider_attempts,
                "http_requests": observed.requests,
                "validated_facts": len(facts),
                "unknowns_preserved": unknowns_preserved,
                "database_unchanged": unchanged,
                "duration_seconds": time.perf_counter() - start,
                "limits": {
                    "timeout_seconds": args.timeout,
                    "num_ctx": settings.num_ctx,
                    "max_output_tokens": limits.max_output_tokens,
                },
            }
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
