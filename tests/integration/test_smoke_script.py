import asyncio
import json
import runpy

import httpx
import pytest

from career_intelligence.extraction import JobExtraction


@pytest.fixture
def smoke_main(repository_root):
    return runpy.run_path(str(repository_root / "scripts/smoke_ollama.py"))["main"]


def test_live_smoke_requires_opt_in_before_provider_creation(smoke_main, monkeypatch, capsys):
    def forbidden(*args):
        raise AssertionError("Provider must not be created")

    monkeypatch.setitem(smoke_main.__globals__, "OllamaProvider", forbidden)
    with pytest.raises(SystemExit) as error:
        smoke_main(["--model", "example:local"])
    assert error.value.code == 2
    assert "--live" in capsys.readouterr().err


@pytest.mark.parametrize(
    "outcome, passed",
    [
        ("valid", True),
        ("empty", False),
        ("failure", False),
        ("unknown_invented", False),
    ],
)
def test_synthetic_smoke_reports_only_summary_and_preserves_storage(
    outcome,
    passed,
    smoke_main,
    monkeypatch,
    capsys,
):
    calls = []

    class Provider:
        def __init__(self, settings, limits, *, transport):
            assert limits.max_output_tokens == 1024 and settings.num_ctx == 4096

        async def generate(self, request):
            calls.append(request)
            assert json.loads(request.content)["description"] == "Python required."
            if outcome == "failure":
                raise RuntimeError("PRIVATE_PROVIDER_MESSAGE")
            data = dict.fromkeys(JobExtraction.model_fields)
            if outcome == "valid":
                data["technical_skills"] = {
                    "items": [
                        {
                            "text": "Python",
                            "evidence": {"field": "description", "start": 0, "end": 6},
                            "confidence": None,
                        }
                    ],
                    "empty_evidence": None,
                }
            elif outcome == "unknown_invented":
                data["required_experience"] = {
                    "text": "Python",
                    "evidence": {"field": "description", "start": 0, "end": 6},
                    "confidence": None,
                }
            return json.dumps(data)

    monkeypatch.setitem(smoke_main.__globals__, "OllamaProvider", Provider)
    assert smoke_main(["--live", "--model", "example:local"]) == (0 if passed else 1)
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["smoke_passed"] == passed and report["database_unchanged"]
    assert report["provider_attempts"] == len(calls) == 1
    assert "extraction" not in report and "Python required." not in output.out
    assert "PRIVATE_PROVIDER_MESSAGE" not in output.out + output.err


@pytest.mark.parametrize(
    "flags",
    [
        ["--timeout", "121"],
        ["--timeout", "nan"],
        ["--model", "example:cloud"],
        ["--base-url", "https://example.com"],
    ],
)
def test_smoke_rejects_unbounded_or_remote_configuration(smoke_main, flags, monkeypatch):
    def forbidden(*args):
        raise AssertionError("Provider must not be created")

    monkeypatch.setitem(smoke_main.__globals__, "OllamaProvider", forbidden)
    with pytest.raises(SystemExit) as error:
        smoke_main(["--live", "--model", "example:local", *flags])
    assert error.value.code == 2


@pytest.mark.parametrize("fails", [False, True])
def test_http_observer_records_only_safe_route_status_and_timing(smoke_main, capsys, fails):
    observed = smoke_main.__globals__["_ObservedTransport"]()

    async def exercise():
        await observed.inner.aclose()

        def handler(request):
            if fails:
                raise httpx.ConnectError("PRIVATE_ERROR", request=request)
            return httpx.Response(200, json={"secret": "PRIVATE_RESPONSE"})

        observed.inner = httpx.MockTransport(handler)
        request = httpx.Request(
            "POST",
            "http://127.0.0.1:11434/api/show",
            headers={"authorization": "PRIVATE_KEY"},
            content=b"PRIVATE_REQUEST",
        )
        try:
            if fails:
                with pytest.raises(httpx.ConnectError):
                    await observed.handle_async_request(request)
            else:
                response = await observed.handle_async_request(request)
                await response.aclose()
        finally:
            await observed.aclose()

    asyncio.run(exercise())
    output = capsys.readouterr()
    assert not output.out and "PRIVATE_" not in output.err
    assert json.loads(output.err) == {"event": "smoke_http_started", "route": "/api/show"}
    assert observed.requests[0]["status"] == (None if fails else 200)
    assert observed.requests[0]["headers_seconds"] >= 0 and observed.closed
