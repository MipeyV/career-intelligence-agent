import asyncio
import json

import httpx
import pytest

pytest.importorskip("langchain_ollama", reason="Optional ollama extra is not installed")

from career_intelligence.cli import main
from career_intelligence.config import (
    MatchingLocalSettings,
    MatchingProposalLimits,
    OllamaSettings,
    load_configuration,
)
from career_intelligence.matching import load_policy
from career_intelligence.matching_proposals import propose_matching_local
from career_intelligence.ollama_provider import OllamaProvider
from career_intelligence.storage import list_jobs


@pytest.fixture
def context(repository_root):
    loaded = load_configuration(repository_root / "conf/settings.yml")
    policy = load_policy(repository_root / "conf/matching.example.yml")
    posting = json.loads((repository_root / "tests/fixtures/greenhouse/matching.json").read_text())[
        "jobs"
    ][0]
    revision = {
        "job_id": "synthetic",
        "number": 1,
        "fingerprint": "a" * 64,
        "payload": {
            "title": posting["title"],
            "description": posting["content"],
            "location": None,
            "raw_record": {"secret": "PRIVATE_RAW"},
        },
    }
    return (
        revision,
        loaded.profile,
        policy,
        (repository_root / "tests/fixtures/matching/proposal.json").read_text(),
    )


def local_info():
    return {"model_info": {"general.architecture": "synthetic"}, "capabilities": ["completion"]}


def chat(content, **updates):
    return {
        "model": "example:local",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
        **updates,
    }


class Transport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self.handler, self.requests, self.closed = handler, [], False

    async def handle_async_request(self, request):
        self.requests.append(request)
        return await self.handler(request)

    async def aclose(self):
        self.closed = True


def run(context, handler, *, limits=None, settings=None):
    transport = Transport(handler)
    result = asyncio.run(
        propose_matching_local(
            *context[:3],
            settings=settings or OllamaSettings(model="example:local"),
            sharing=MatchingLocalSettings(
                share_profile=True, profile_fields=["skills.0.name", "skills.0.evidence.0"]
            ),
            limits=limits or MatchingProposalLimits(),
            transport=transport,
        )
    )
    return result, transport


def test_real_pinned_sdk_receives_only_selected_facts_without_credentials_tracing_or_tools(
    context, monkeypatch
):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "PRIVATE_TRACE")
    monkeypatch.setenv("OLLAMA_API_KEY", "PRIVATE_KEY")
    monkeypatch.setenv("OLLAMA_HOST", "https://example.com")
    monkeypatch.setenv("HTTP_PROXY", "http://example.com:1111")
    from langchain_core.tracers.langchain import LangChainTracer
    from langsmith import Client

    def forbidden(*args, **kwargs):
        raise AssertionError("No tracing client is authorized")

    monkeypatch.setattr(LangChainTracer, "__init__", forbidden)
    monkeypatch.setattr(Client, "__init__", forbidden)

    async def handler(request):
        assert request.url.host == "127.0.0.1"
        assert not request.headers.get("authorization")
        if request.url.path == "/api/show":
            assert json.loads(request.content) == {"model": "example:local"}
            return httpx.Response(200, json=local_info())
        body = json.loads(request.content)
        content = json.loads(body["messages"][1]["content"])
        assert content["profile_facts"] == {
            "skills.0.name": "Python",
            "skills.0.evidence.0": "Synthetic course project",
        }
        assert "profile" not in content and "preferences" not in content
        assert "PRIVATE_" not in request.content.decode()
        assert not body.get("tools") and body["stream"] is False
        assert body["format"]["additionalProperties"] is False
        assert body["options"]["num_predict"] == 4096 and body["keep_alive"] == 0
        assert body["think"] is False
        return httpx.Response(200, json=chat(context[3]))

    result, transport = run(context, handler)
    assert result.status == "complete" and result.model == "example:local"
    assert result.provider_attempts == 1 and result.fixture_reads == 0
    assert result.draft_review["reviewed_by"] is None and result.review_required
    assert [r.url.path for r in transport.requests] == [
        "/api/show",
        "/api/chat",
    ] and transport.closed


@pytest.mark.parametrize(
    "failure", ["redirect", "rate_limit", "server_error", "remote", "not_completion", "duplicate"]
)
def test_preflight_refusal_never_receives_profile_facts_and_never_retries(context, failure):
    async def handler(request):
        assert json.loads(request.content) == {"model": "example:local"}
        if failure in ("redirect", "rate_limit", "server_error"):
            return httpx.Response(
                {"redirect": 302, "rate_limit": 429, "server_error": 503}[failure],
                headers={"location": "https://example.com"},
                text="PRIVATE_ERROR",
            )
        if failure == "duplicate":
            return httpx.Response(
                200, content=b'{"remote_host":"https://example.com","remote_host":null}'
            )
        info = local_info()
        info.update(
            {"remote_host": "https://example.com"}
            if failure == "remote"
            else {"capabilities": ["embedding"]}
        )
        return httpx.Response(200, json=info)

    result, transport = run(context, handler)
    assert (
        result.status == "provider_unavailable"
        and len(transport.requests) == 1
        and transport.closed
    )
    assert result.draft_review is None and "PRIVATE_ERROR" not in result.model_dump_json()


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("bad_json", "invalid_output"),
        ("approval", "invalid_output"),
        ("unshared", "ungrounded_evidence"),
        ("quote", "ungrounded_evidence"),
        ("length", "invalid_output"),
        ("redirect", "provider_unavailable"),
        ("tool", "invalid_output"),
    ],
)
def test_generation_cannot_self_approve_use_unshared_facts_or_repair_invalid_responses(
    context, failure, expected
):
    async def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json=local_info())
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://example.com"})
        data = json.loads(context[3])
        if failure == "approval":
            data["reviewed_by"] = "Invented reviewer"
        elif failure == "unshared":
            data["assessments"]["technical_skills"]["profile_refs"] = ["preferences.target_roles.0"]
        elif failure == "quote":
            data["assessments"]["technical_skills"]["source_evidence"][0]["text"] = "Invented quote"
        content = "{" if failure == "bad_json" else json.dumps(data)
        body = chat(content)
        if failure == "length":
            body["done_reason"] = "length"
        elif failure == "tool":
            body["message"]["tool_calls"] = [{"function": {"name": "execute", "arguments": {}}}]
        return httpx.Response(200, json=body)

    result, transport = run(context, handler)
    assert result.status == expected and result.draft_review is None
    assert len(transport.requests) == 2 and transport.closed


def test_http_size_bounds_apply_before_the_model_response_is_parsed(context):
    async def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json=local_info())
        return httpx.Response(200, headers={"content-length": "9999999"}, content=b"not parsed")

    result, transport = run(
        context, handler, settings=OllamaSettings(model="example:local", max_response_bytes=1024)
    )
    assert result.status == "output_limit" and transport.closed


def test_timeout_cancels_generation_and_closes_the_shared_transport(context):
    cancelled = []

    async def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json=local_info())
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    result, transport = run(context, handler, limits=MatchingProposalLimits(timeout_seconds=0.1))
    assert result.status == "timeout" and cancelled == [True] and transport.closed


@pytest.mark.parametrize("bad", [False, True])
def test_pinned_sdk_async_and_sync_clients_close_after_success_or_invalid_output(
    context, monkeypatch, bad
):
    from ollama import AsyncClient, Client

    closed = []
    original_async, original_sync = AsyncClient.close, Client.close

    async def close_async(self):
        closed.append("async")
        return await original_async(self)

    def close_sync(self):
        closed.append("sync")
        return original_sync(self)

    monkeypatch.setattr(AsyncClient, "close", close_async)
    monkeypatch.setattr(Client, "close", close_sync)

    async def handler(request):
        return httpx.Response(
            200,
            json=local_info()
            if request.url.path == "/api/show"
            else chat("{" if bad else context[3]),
        )

    result, transport = run(context, handler)
    assert result.status == ("invalid_output" if bad else "complete") and transport.closed
    assert closed == ["async", "sync"]


def test_live_mode_cli_uses_mock_transport_keeps_private_fields_local_and_preserves_storage(
    config_files, repository_root, capsys, monkeypatch
):
    settings, docs, write = config_files
    docs["settings"].update(
        ollama={"model": "example:local"},
        matching_local={
            "share_profile": True,
            "profile_fields": ["skills.0.name", "skills.0.evidence.0"],
        },
    )
    write("settings", docs["settings"])
    docs["profile"]["skills"] = [{"name": "Python", "evidence": ["Synthetic course project"]}]
    docs["profile"]["preferences"]["objectives"] = ["PRIVATE_UNSHARED_OBJECTIVE"]
    profile_path = write("profile", docs["profile"])
    assert (
        main(
            [
                "collect",
                "--config",
                str(settings),
                "--fixture",
                str(repository_root / "tests/fixtures/greenhouse/matching.json"),
                "--persist",
            ]
        )
        == 0
    )
    capsys.readouterr()
    database = load_configuration(settings).settings.database_path
    job = list_jobs(database)[0]
    args = [
        "jobs",
        "match",
        "--config",
        str(settings),
        "--job-id",
        job["id"],
        "--policy",
        str(repository_root / "conf/matching.example.yml"),
        "--ollama",
    ]
    response = (repository_root / "tests/fixtures/matching/proposal.json").read_text()
    available = [True]
    transports = []

    async def handler(request):
        assert "PRIVATE_UNSHARED_OBJECTIVE" not in request.content.decode()
        if not available[0]:
            return httpx.Response(503)
        return httpx.Response(
            200, json=local_info() if request.url.path == "/api/show" else chat(response)
        )

    def provider(settings, limits, transport=None):
        mock = Transport(handler)
        transports.append(mock)
        return OllamaProvider(settings, limits, mock)

    monkeypatch.setattr("career_intelligence.matching_proposals.OllamaProvider", provider)
    before = [path.read_bytes() for path in (database, profile_path)]
    assert main(args) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["status"] == "complete" and result["schema_version"] == 2
    assert result["model"] == "example:local" and result["review_required"]
    assert result["draft_review"]["reviewed_by"] is None and "overall_score" not in result
    available[0] = False
    assert main(args) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "provider_unavailable"
    assert all(transport.closed for transport in transports)
    assert [path.read_bytes() for path in (database, profile_path)] == before
    assert main(["jobs", "list", "--config", str(settings)]) == 0
