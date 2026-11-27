# Structured extraction: offline contract (2.1a)

This increment implements the validation boundary for Phase 2.1. It replays a local
response fixture against an existing immutable job revision. It does not call a model,
download model weights, install LangChain, or send data to a remote provider. The
remaining 2.1b increment will connect an approved provider to this same boundary.

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
input SHA-256, prompt version, provider name, attempt count, status and extraction.
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
The local fixture uses a bounded synchronous file read. No live HTTP provider or
claim about live-provider resource bounds is included in this increment.

Statuses are `complete`, `input_limit`, `output_limit`, `timeout`,
`provider_unavailable`, `invalid_output` and `ungrounded_evidence`. A failed response
is discarded as a whole, leaving `extraction: null`. Exit code is 0 for complete,
1 for extraction/storage failure and 2 for invalid invocation/configuration. Failure
diagnostics contain only safe status codes, never raw responses or provider exception
messages. Successful output intentionally contains quoted job text: store private
exports under ignored paths such as `data/`.

Collection, filtering and ordinary job listing never invoke extraction. A failure
cannot stop those deterministic commands or modify verified candidate skills.

## Next provider increment

Review and approve the LangChain/Ollama dependency additions before installing them.
Use an explicitly configured local model and loopback endpoint, preserve this output
validator, and add offline transport/provider mocks. The installed hardware and model
are not assumed. Any later remote adapter remains opt-in; private profile transmission
requires separate explicit configuration. Eligibility, scoring and orchestration stay
in their later milestones. See the official
[LangChain Ollama integration](https://reference.langchain.com/python/langchain-ollama)
and [structured output reference](https://reference.langchain.com/python/langchain-ollama/chat_models/ChatOllama/with_structured_output)
for the planned adapter contract.
