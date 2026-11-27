# Structured extraction: fixtures and optional local Ollama

Phase 2.1a implemented the validation boundary and local response replay. Phase 2.1b
adds an optional LangChain/Ollama adapter behind the same validator. Fixture mode
remains offline and the default installation has no LangChain dependencies. Live
local inference requires both explicit configuration and the `--ollama` flag.

## Reproduce the demonstration

Follow [the operating guide](OPERATIONS.md) to create `conf/demo.local.yml` with a
disposable database path. Run the following commands from the repository root.

```text
uv run --locked --offline career-intelligence collect --config conf/demo.local.yml --fixture tests/fixtures/greenhouse/initial.json --persist
uv run --locked --offline career-intelligence jobs list --config conf/demo.local.yml
```

Copy the `id` of the **Research Engineer** job into `JOB_ID` below. The synthetic
response fixture quotes that posting's initial description; it is not a generic
response for every job. Its values were prepared by the coding assistant for this
demonstration, rather than independently reviewed model-quality annotations.

```text
uv run --locked --offline career-intelligence jobs extract --config conf/demo.local.yml --job-id JOB_ID --revision 1 --response-fixture tests/fixtures/extraction/initial-response.json
```

Omit `--revision` to select the current revision. A single read-only SQL query binds
the payload, revision number and fingerprint. Unknown job IDs, nonpositive revision
numbers, unavailable revisions and unsupported database schemas are refused.

The command emits versioned JSON with the job ID, revision, source fingerprint,
input SHA-256, prompt version, provider name, configured model (null for fixtures),
attempt count, status and extraction.
It writes no analysis, source revision, decision or event to SQLite. Future durable
analysis and caching belong to the planned persistence work in Phase 2.5.

## What is validated

The provider receives a fixed system instruction, the output JSON schema and a JSON
object containing only the posting's `title`, `location` and `description`. Candidate
profile/CV content, raw source records, application URLs, credentials and database IDs
are excluded from the provider request. Posting text is untrusted data. No executable
tools are exposed and no returned links or instructions are followed.

The output schema has fixed fields for responsibilities, requirements, experience,
skills, authorization, role conditions, dates and compensation wording. Every field
is required and nullable. A fact contains `text`, evidence (`field`, `start`, `end`)
and a nullable confidence value between 0 and 1. Its text must exactly match the
named input field at the given zero-based Unicode code-point offsets `[start, end)`.
Each quotation is nonblank and at most 4096 characters; each collection has at most
64 items. Unknown fields, invalid types, duplicate JSON keys, non-finite numbers,
unsupported evidence and fenced or malformed JSON fail validation.

A null collection means unknown. An explicitly empty collection uses `items: []`
with an `empty_evidence` quotation; a populated collection requires
`empty_evidence: null`. This preserves the distinction without treating missing
mentions as an empty set. Dates and compensation remain quoted wording, preserving
precision and provenance. They are not normalized into invented dates, amounts,
currency conversions or eligibility decisions.

Exact quotation proves that text is present. It does not prove that the chosen
category, interpretation or explicit-empty claim is semantically correct. Confidence
is an unverified provider value. Human review and an independently annotated dataset
are still needed to assess extraction quality. The fixture path is a contract test,
not evidence of any model's capabilities or prompt-injection resistance.

## Limits and failure outcomes

Settings accept an `extraction` section with the following defaults.

```yaml
extraction:
  max_input_bytes: 65536
  max_output_bytes: 131072
  max_output_tokens: 4096
  timeout_seconds: 30
```

Input bytes count the canonical ASCII-escaped JSON of the three posting fields,
excluding the fixed prompt and schema. Oversized input is refused before a provider
attempt, without truncating source evidence. The fixture reader reads at most the
output byte limit plus one byte. Output is checked before JSON parsing. Token limits
are passed in the provider contract; a replayed fixture has no generated tokens.
This increment does not implement run-wide token/cost accounting or pricing.

At most one attempt is made, with no automatic retries. Async providers must honor
cancellation for the configured timeout to apply; it is not a process-level kill.
The local fixture uses a bounded synchronous file read and needs no server. The
optional adapter's HTTP bounds are described below.

Statuses are `complete`, `input_limit`, `output_limit`, `timeout`, `provider_not_installed`,
`provider_unavailable`, `invalid_output` and `ungrounded_evidence`. A failed response
is discarded as a whole, leaving `extraction: null`. Exit code is 0 for complete,
1 for extraction/storage failure and 2 for invalid invocation/configuration. Failure
diagnostics contain only safe status codes, never raw responses or provider exception
messages. Successful output intentionally contains quoted job text: store private
exports under ignored paths such as `data/`.

Collection, filtering and ordinary job listing never invoke extraction. A failure
cannot stop those deterministic commands or modify verified candidate skills.

## Optional local Ollama (2.1b)

The user approved the extra on 2026-11-27; the
[dependency review](OLLAMA-DEPENDENCIES.md) records pinned versions and transitive
dependencies. Install it explicitly:

```text
uv sync --locked --dev --extra ollama
```

Use a trusted local Ollama server with an already installed completion model that
supports native JSON schema output and `think: false`. Choose the model yourself;
no model or hardware capability is assumed and the application never downloads one.
To keep the daemon local, disable Ollama cloud features in its own configuration
with `OLLAMA_NO_CLOUD=1`, then restart it as described in the
[official local FAQ](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features).
The application does not change or restart your daemon. A loopback URL alone cannot
prove that a server will never forward data; it must be a trusted local service.

Add the following to an ignored local settings copy, replacing `YOUR_LOCAL_MODEL`
with the exact installed model name. The name is intentionally not set by default.

```yaml
ollama:
  model: YOUR_LOCAL_MODEL
  base_url: http://127.0.0.1:11434
  num_ctx: 8192
  seed: 0
  max_response_bytes: 2097152
```

Only HTTP loopback IP endpoints are accepted (127.0.0.1 or ::1, for example).
Hostnames, credentials, paths, query strings and cloud model tags are refused.
Select a stored job and invoke inference explicitly:

```text
uv run --locked --offline --extra ollama career-intelligence jobs extract --config conf/demo.local.yml --job-id JOB_ID --ollama
```

Here `--offline` controls uv's package installation. `--ollama` still contacts the
configured local inference server. It is mutually exclusive with `--response-fixture`.

One provider attempt comprises at most two HTTP POSTs: `/api/show` first, with only
the model name, and `/api/chat` after a successful local completion-model preflight.
Remote-model metadata, missing metadata and unsupported capabilities are refused
before posting text is sent. There are no redirects, proxy inheritance or retries.
Every HTTP response is bounded by `max_response_bytes`, including preflight and
protocol overhead; compressed responses are refused. The separate extraction-output
byte cap still applies to the generated text. Inherited Ollama authorization headers
are stripped. Tracing is disabled even with tracing environment variables, and ambient
LangChain callback contexts are isolated.

The adapter sends the native output schema through `format`, with no tools. It
preserves the raw content for strict JSON/evidence validation instead of allowing
an upstream parser to repair malformed output. Tool calls, missing completion markers
and token-limit terminations are rejected. Generation uses temperature 0, configured
seed/context, `num_predict` from `max_output_tokens`, and `keep_alive: 0`. These are
resource requests to Ollama, not a guarantee of deterministic model output or GPU
behavior. The timeout bounds cooperative client waiting; cancellation does not prove
server-side inference stopped immediately. Cold model loading may need a larger
configured timeout (maximum 300 seconds). Input tokenization/context coverage and
run-wide cost accounting are not measured by this increment.

A missing extra returns `provider_not_installed`. A stopped server, missing or
incompatible model, or failed preflight returns `provider_unavailable`. Quotation and
schema failures use the same safe outcomes as fixtures; ordinary collection and
listing remain available. Model identity records the configured tag, which can be
mutable; no model digest, quality score or eligibility verdict is claimed.

Offline tests exercise the real pinned LangChain/Ollama code with mock HTTP transport,
including cancellation, safe failures and cleanup. No live inference, model quality,
hardware capacity or installed-model compatibility has been validated in this increment.
A live check is a separate explicit opt-in using synthetic job content. Remote providers,
candidate-profile transmission, eligibility, scoring and orchestration stay in later
milestones. See the official
[LangChain Ollama integration](https://reference.langchain.com/python/langchain-ollama)
and [structured output reference](https://reference.langchain.com/python/langchain-ollama/chat_models/ChatOllama/with_structured_output)
for the planned adapter contract.
