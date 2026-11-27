# Ollama provider dependency review (2.1b)

Reviewed and approved by the user on 2026-11-27 for optional local Ollama integration.
No live model check or model download is implied by this dependency approval.

## Approved optional extra

The `ollama` extra has direct dependencies `langchain-ollama==1.2.0` and
`langchain-core==1.8.5`. Keep the default installation sufficient for deterministic
collection, listing and fixture extraction. Both packages declare Python >=3.11;
the project remains on Python 3.12. LangChain Core supplies the message interface;
LangSmith's public tracing context disables tracing. The integration brings `ollama==0.6.3`
through its dependency graph.

An isolated universal uv resolution found 23 package names not in the current lock,
including platform-specific packages. That preliminary unconstrained resolution also
selected HTTPX 0.29.0 in place of 0.28.1. The final lock preserves HTTPX 0.28.1 and
the other existing package versions. It adds 23 package names, including platform-specific
packages. Local Windows installation and offline adapter/wheel checks were run;
Windows/Ubuntu base and extra installations are verified separately in CI.

Additional resolved package names: charset-normalizer, distro, httpcore2, httpx2,
httpx2-jsfetch, jsonpatch, jsonpointer, langchain-core, langchain-ollama,
langchain-protocol, langsmith, ollama, orjson, requests, requests-toolbelt, sniffio,
tenacity, truststore, urllib3, uuid-utils, websockets, xxhash and zstandard. Packages already
present include HTTPX, Pydantic, PyYAML and typing helpers.

## Runtime boundary

Only an explicitly selected local model and an explicitly enabled `--ollama` command
may invoke inference. Accept loopback IP endpoints only; disable redirects, proxy
environment inheritance, retries and tracing. Permit only one `/api/show` preflight
and one `/api/chat` request, in order.
Bound the transport response and preserve the existing output/evidence validator.
Never download a model automatically. A missing extra, stopped server or missing
model must produce a safe failure while deterministic discovery remains usable.

LangSmith is a transitive package, not a selected external service. Disable tracing
for this operation even when tracing environment variables exist, and verify that
behavior offline. No account, API key, paid service, external trace upload or remote
model is part of this integration. Do not send candidate profiles or CVs to inference.

The pinned ChatOllama integration exposes no public close method. Cleanup uses its
two SDK-client attributes and the SDKs' public close methods, tested on success,
failure and cancellation. Recheck that small compatibility boundary before upgrades.

## Sources

- [PyPI integration metadata](https://pypi.org/pypi/langchain-ollama/json)
- [PyPI Core metadata](https://pypi.org/pypi/langchain-core/json)
- [ChatOllama reference](https://reference.langchain.com/python/langchain-ollama/chat_models/ChatOllama)
- [Structured output reference](https://reference.langchain.com/python/langchain-ollama/chat_models/ChatOllama/with_structured_output)
- [Ollama chat API](https://docs.ollama.com/api/chat)
- [Ollama model preflight](https://docs.ollama.com/api-reference/show-model-details.md)
