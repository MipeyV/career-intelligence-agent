"""Explicit local Ollama inference through the optional, pinned LangChain adapter."""

import asyncio
import json
from contextvars import Context
from dataclasses import dataclass

import httpx

from career_intelligence.config import ExtractionLimits, OllamaSettings
from career_intelligence.extraction import (
    InvalidProviderOutputError,
    OutputLimitError,
    ProviderNotInstalledError,
    ProviderRequest,
    _reject_constant,
    _unique_object,
)


def _forbid_sync(request):
    raise RuntimeError("Synchronous provider requests are disabled")


class _LocalTransport(httpx.AsyncBaseTransport):
    """Permit one model preflight and one generation, bounding raw response bytes."""

    def __init__(self, settings: OllamaSettings, inner: httpx.AsyncBaseTransport):
        self.settings = settings
        self.inner = inner
        self.origin = httpx.URL(settings.base_url)
        self.routes = []
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        expected = "/api/show" if not self.routes else "/api/chat"
        if (
            self.closed
            or len(self.routes) >= 2
            or request.method != "POST"
            or request.url.path != expected
            or request.url.scheme != self.origin.scheme
            or request.url.host != self.origin.host
            or request.url.port != self.origin.port
            or request.url.query
            or request.url.fragment
        ):
            raise RuntimeError("Unexpected provider request")
        self.routes.append(request.url.path)
        # The SDK otherwise inherits OLLAMA_API_KEY even for a local endpoint.
        for name in ("authorization", "proxy-authorization", "cookie"):
            request.headers.pop(name, None)
        request.headers["accept-encoding"] = "identity"
        response = await self.inner.handle_async_request(request)
        try:
            if response.status_code != 200:
                raise RuntimeError("Local provider request failed")
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise InvalidProviderOutputError
            declared = response.headers.get("content-length")
            if declared is not None and int(declared) > self.settings.max_response_bytes:
                raise OutputLimitError
            chunks = []
            size = 0
            async for chunk in response.stream:
                size += len(chunk)
                if size > self.settings.max_response_bytes:
                    raise OutputLimitError
                chunks.append(chunk)
            return httpx.Response(
                200,
                headers=response.headers,
                content=b"".join(chunks),
                request=request,
            )
        finally:
            await response.aclose()

    async def aclose(self):
        if not self.closed:
            self.closed = True
            await self.inner.aclose()


@dataclass(frozen=True)
class OllamaProvider:
    settings: OllamaSettings
    limits: ExtractionLimits
    transport: httpx.AsyncBaseTransport | None = None

    async def generate(self, request: ProviderRequest) -> str:
        # Isolate ambient Runnable callbacks/cache/tracing contexts. Environment
        # tracing is disabled separately inside this clean task context.
        task = asyncio.create_task(self._generate(request), context=Context())
        return await task

    async def _generate(self, request: ProviderRequest) -> str:
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from langchain_ollama import ChatOllama
            from langsmith import tracing_context
        except ImportError:
            raise ProviderNotInstalledError from None

        guarded = _LocalTransport(
            self.settings, self.transport or httpx.AsyncHTTPTransport(retries=0)
        )
        client_args = {
            "trust_env": False,
            "follow_redirects": False,
            "timeout": httpx.Timeout(
                self.limits.timeout_seconds, connect=min(5, self.limits.timeout_seconds)
            ),
        }
        model = None
        try:
            async with httpx.AsyncClient(transport=guarded, **client_args) as preflight:
                response = await preflight.post(
                    self.settings.base_url + "/api/show", json={"model": self.settings.model}
                )
                info = response.json(
                    object_pairs_hook=_unique_object, parse_constant=_reject_constant
                )
                if (
                    not isinstance(info, dict)
                    or info.get("remote_host") is not None
                    or info.get("remote_model") is not None
                    or not isinstance(info.get("model_info"), dict)
                    or not info["model_info"]
                    or not isinstance(info.get("capabilities"), list)
                    or "completion" not in info.get("capabilities", [])
                ):
                    raise RuntimeError("An installed local completion model is required")
                with tracing_context(enabled=False, parent=False):
                    model = ChatOllama(
                        model=self.settings.model,
                        base_url=self.settings.base_url,
                        validate_model_on_init=False,
                        temperature=0,
                        num_predict=request.max_output_tokens,
                        num_ctx=self.settings.num_ctx,
                        seed=self.settings.seed,
                        keep_alive=0,
                        reasoning=False,
                        # Native schema steering preserves raw JSON for our strict
                        # validator; upstream parsers can repair malformed JSON.
                        format=json.loads(request.response_schema_json),
                        disable_streaming=True,
                        cache=False,
                        client_kwargs=client_args,
                        sync_client_kwargs={"transport": httpx.MockTransport(_forbid_sync)},
                        async_client_kwargs={"transport": guarded},
                    )
                    message = await model.ainvoke(
                        [SystemMessage(request.system), HumanMessage(request.content)],
                        config={"callbacks": []},
                        stream=False,
                    )
                    if (
                        not isinstance(message.content, str)
                        or message.tool_calls
                        or message.invalid_tool_calls
                        or message.response_metadata.get("done_reason") != "stop"
                    ):
                        raise InvalidProviderOutputError
                    return message.content
        except httpx.TimeoutException:
            raise TimeoutError from None
        finally:
            # ChatOllama exposes no public close API. These two pinned integration
            # attributes contain SDK clients with public close methods; tests cover
            # success, failure and cancellation cleanup before dependency upgrades.
            if model is not None:
                try:
                    await model._async_client.close()
                finally:
                    model._client.close()
            await guarded.aclose()
