import asyncio
import json

import httpx
import pytest

pytest.importorskip("langchain_ollama", reason="Optional ollama extra is not installed")

from career_intelligence.cli import main
from career_intelligence.config import ExtractionLimits, OllamaSettings, load_configuration
from career_intelligence.extraction import JobExtraction, extract_revision
from career_intelligence.ollama_provider import OllamaProvider
from career_intelligence.storage import list_jobs


def source():
    return {
        "job_id": "synthetic-id",
        "number": 1,
        "fingerprint": "source-hash",
        "payload": {
            "title": "Research Engineer",
            "location": None,
            "description": "Python required.",
        },
    }


def local_info():
    return {"model_info": {"general.architecture": "example"}, "capabilities": ["completion"]}


def chat_response(content=None, **updates):
    return {
        "model": "example:local",
        "message": {
            "role": "assistant",
            "content": content or json.dumps(dict.fromkeys(JobExtraction.model_fields)),
        },
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 10,
        "eval_count": 5,
        **updates,
    }


class Transport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self.handler = handler
        self.requests = []
        self.closed = False

    async def handle_async_request(self, request):
        self.requests.append(request)
        return await self.handler(request)

    async def aclose(self):
        self.closed = True


def run(handler, *, limits=None, settings=None):
    transport = Transport(handler)
    policy = limits or ExtractionLimits()
    provider = OllamaProvider(settings or OllamaSettings(model="example:local"), policy, transport)
    result = asyncio.run(
        extract_revision(
            source(),
            provider,
            provider_name="ollama",
            model_name="example:local",
            limits=policy,
        )
    )
    return result, transport


def test_real_langchain_adapter_uses_native_schema_without_tracing_credentials_or_tools(
    monkeypatch,
):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "PRIVATE_TRACE_KEY")
    monkeypatch.setenv("OLLAMA_API_KEY", "PRIVATE_OLLAMA_KEY")
    monkeypatch.setenv("OLLAMA_HOST", "https://example.com")
    monkeypatch.setenv("HTTP_PROXY", "http://example.com:1111")
    from langchain_core.tracers.langchain import LangChainTracer
    from langsmith import Client

    def forbidden(*args, **kwargs):
        raise AssertionError("Tracing client must not be created")

    monkeypatch.setattr(LangChainTracer, "__init__", forbidden)
    monkeypatch.setattr(Client, "__init__", forbidden)

    async def handler(request):
        assert request.url.host == "127.0.0.1"
        assert not request.headers.get("authorization")
        if request.url.path == "/api/show":
            assert json.loads(request.content) == {"model": "example:local"}
            return httpx.Response(200, json=local_info())
        body = json.loads(request.content)
        assert body["stream"] is False
        assert body["format"]["additionalProperties"] is False
        assert body["options"]["num_predict"] == 4096
        assert body["options"]["num_ctx"] == 8192
        assert body["options"]["temperature"] == 0
        assert body["keep_alive"] == 0 and body["think"] is False
        assert not body.get("tools")
        assert [item["role"] for item in body["messages"]] == ["system", "user"]
        assert json.loads(body["messages"][1]["content"])["description"] == "Python required."
        assert "PRIVATE_" not in request.content.decode()
        return httpx.Response(200, json=chat_response())

    result, transport = run(handler)
    assert result.status == "complete" and result.model == "example:local"
    assert [r.url.path for r in transport.requests] == ["/api/show", "/api/chat"]
    assert transport.closed


@pytest.mark.parametrize(
    "patch",
    [
        {"done": False},
    ],
)
def test_missing_completion_marker_is_not_accepted(patch):
    async def handler(request):
        return httpx.Response(
            200, json=(local_info() if request.url.path == "/api/show" else chat_response(**patch))
        )

    result, transport = run(handler)
    assert result.status == "invalid_output" and result.extraction is None
    assert transport.closed


@pytest.mark.parametrize("status", [302, 404, 429, 500])
def test_preflight_failure_never_sends_description_or_retries(status):
    async def handler(request):
        return httpx.Response(
            status, headers={"location": "https://example.com"}, text="PRIVATE_ERROR"
        )

    result, transport = run(handler)
    assert result.status == "provider_unavailable" and result.extraction is None
    assert len(transport.requests) == 1 and transport.closed
    assert "PRIVATE_ERROR" not in result.model_dump_json()


@pytest.mark.parametrize(
    "patch",
    [
        {"remote_host": "https://example.com"},
        {"remote_model": "remote"},
        {"capabilities": ["embedding"]},
        {"model_info": {}},
        {"capabilities": "completion"},
    ],
)
def test_cloud_or_unknown_model_metadata_prevents_generation(patch):
    async def handler(request):
        return httpx.Response(200, json={**local_info(), **patch})

    result, transport = run(handler)
    assert result.status == "provider_unavailable" and len(transport.requests) == 1
    assert transport.closed


@pytest.mark.parametrize(
    "content, updates, expected",
    [
        ('{"technical_skills":', {}, "invalid_output"),
        ('{"compensation":null,"compensation":null}', {}, "invalid_output"),
        (None, {"done_reason": "length"}, "invalid_output"),
        (
            None,
            {
                "message": {
                    "role": "assistant",
                    "content": "{}",
                    "tool_calls": [
                        {"function": {"name": "execute", "arguments": {"cmd": "do nothing"}}}
                    ],
                }
            },
            "invalid_output",
        ),
        (
            json.dumps(
                {
                    **dict.fromkeys(JobExtraction.model_fields),
                    "required_experience": {
                        "text": "Invented requirement",
                        "evidence": {"field": "description", "start": 0, "end": 20},
                        "confidence": None,
                    },
                }
            ),
            {},
            "ungrounded_evidence",
        ),
    ],
)
def test_real_adapter_preserves_bad_json_and_rejects_tool_calls_or_incomplete_generation(
    content,
    updates,
    expected,
):
    async def handler(request):
        return httpx.Response(
            200,
            json=(
                local_info()
                if request.url.path == "/api/show"
                else chat_response(content, **updates)
            ),
        )

    result, transport = run(handler)
    assert result.status == expected and result.extraction is None
    assert len(transport.requests) == 2 and transport.closed


class Oversized(httpx.AsyncByteStream):
    closed = False

    async def __aiter__(self):
        yield b"x" * 600
        yield b"x" * 600
        raise AssertionError("Oversized response was not stopped")

    async def aclose(self):
        self.closed = True


def test_wire_size_cap_stops_stream_and_closes_connections():
    stream = Oversized()

    async def handler(request):
        return httpx.Response(200, stream=stream)

    result, transport = run(
        handler, settings=OllamaSettings(model="example:local", max_response_bytes=1024)
    )
    assert result.status == "output_limit"
    assert stream.closed and transport.closed


@pytest.mark.parametrize(
    "headers, expected",
    [
        ({"content-length": "9999999"}, "output_limit"),
        ({"content-encoding": "gzip"}, "invalid_output"),
    ],
)
def test_response_envelope_is_checked_before_parsing(headers, expected):
    async def handler(request):
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(b"not parsed"))

    result, transport = run(handler)
    assert result.status == expected and transport.closed


@pytest.mark.parametrize(
    "failure, expected",
    [
        (httpx.ConnectError, "provider_unavailable"),
        (httpx.ReadTimeout, "timeout"),
    ],
)
def test_transport_errors_have_safe_status_without_retries(failure, expected):
    async def handler(request):
        raise failure("PRIVATE_ERROR", request=request)

    result, transport = run(handler)
    assert result.status == expected and len(transport.requests) == 1
    assert transport.closed and "PRIVATE_ERROR" not in result.model_dump_json()


def test_duplicate_preflight_keys_cannot_hide_remote_model_metadata():
    async def handler(request):
        return httpx.Response(
            200,
            content=(
                b'{"remote_host":"https://example.com","remote_host":null,'
                b'"model_info":{"general.architecture":"example"},"capabilities":["completion"]}'
            ),
        )

    result, transport = run(handler)
    assert result.status == "provider_unavailable" and len(transport.requests) == 1
    assert transport.closed


def test_generation_redirect_is_not_followed():
    async def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json=local_info())
        return httpx.Response(302, headers={"location": "https://example.com"})

    result, transport = run(handler)
    assert result.status == "provider_unavailable" and len(transport.requests) == 2
    assert transport.closed


def test_timeout_cancels_generation_and_closes_transport():
    cancelled = []

    async def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json=local_info())
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    result, transport = run(handler, limits=ExtractionLimits(timeout_seconds=0.1))
    assert result.status == "timeout" and result.extraction is None
    assert cancelled == [True] and transport.closed


def test_cli_ollama_mode_preserves_database_and_falls_back_to_regular_listing(
    config_files,
    repository_root,
    monkeypatch,
    capsys,
):
    settings, docs, write = config_files
    docs["settings"]["ollama"] = {"model": "example:local"}
    write("settings", docs["settings"])
    assert (
        main(
            [
                "collect",
                "--config",
                str(settings),
                "--fixture",
                str(repository_root / "tests/fixtures/greenhouse/initial.json"),
                "--persist",
            ]
        )
        == 0
    )
    capsys.readouterr()
    database = load_configuration(settings).settings.database_path
    job_id = list_jobs(database)[0]["id"]
    before = database.read_bytes()
    available = [True]

    async def handler(request):
        if not available[0]:
            return httpx.Response(503)
        return httpx.Response(
            200, json=(local_info() if request.url.path == "/api/show" else chat_response())
        )

    monkeypatch.setattr(
        "career_intelligence.cli.OllamaProvider",
        lambda settings, limits: OllamaProvider(settings, limits, Transport(handler)),
    )
    assert main(["jobs", "extract", "--config", str(settings), "--job-id", job_id, "--ollama"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["provider"] == "ollama" and output["model"] == "example:local"
    assert database.read_bytes() == before
    available[0] = False
    assert main(["jobs", "extract", "--config", str(settings), "--job-id", job_id, "--ollama"]) == 1
    failure = capsys.readouterr()
    assert json.loads(failure.out)["status"] == "provider_unavailable"
    assert json.loads(failure.err)["event"] == "extraction_failed"
    assert database.read_bytes() == before
    assert main(["jobs", "list", "--config", str(settings)]) == 0
