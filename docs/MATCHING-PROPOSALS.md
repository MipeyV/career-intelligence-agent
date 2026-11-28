# Unreviewed matching proposals (2.3b)

`jobs match --response-fixture` reads a local JSON response and prepares a draft for
[the reviewed matching workflow](MATCHING.md). It exercises the response/prompt
contract needed before automatic dimension analysis. No model endpoint is selected,
no profile is transmitted, and a fixture replay does not measure model quality.

## Replay the synthetic response

Use the disposable settings/database from [the matching guide](MATCHING.md), persist
its synthetic posting and take that posting's ID from `jobs list`. Then run:

```text
uv run --locked --offline career-intelligence jobs match --config conf/matching-demo.local.yml --job-id JOB_ID --policy conf/matching.example.yml --response-fixture tests/fixtures/matching/proposal.json
```

The [response fixture](../tests/fixtures/matching/proposal.json) proposes a technical
dimension with exact source evidence and paths into the public synthetic profile.
All other dimensions, including compensation, remain null. `--revision` selects a
historical revision. The response must match the selected source/profile snapshot;
unsupported references invalidate the whole proposal.

The result envelope includes:

- `status`, `provider: fixture`, `fixture_reads` and `review_required: true`;
- job ID, revision/fingerprint, profile hash and full policy hash;
- proposal/output version 1, prompt version `matching-proposal-v1`, input and response hashes;
- `draft_review` only after the entire response passes structural/presence validation.

`fixture_reads` counts attempted reads, including an unavailable file; it is zero
when the input budget prevents any attempt.

No component aggregate or overall score is computed. Keep the envelope in an ignored
location if you need its prompt/response provenance; the review itself does not embed
that envelope. Failed outputs contain no draft, response text, private file path or
exception diagnostics. A response hash is recorded only after complete bounded UTF-8
text is available; it is not proof of semantic correctness.

## The review boundary

The proposal response schema accepts only `schema_version: 1` and `assessments`.
All nine dimensions must be explicit; use null for unknown. A nonnull assessment uses
the existing integer level, confidence, rationale, exact source quotes, profile paths
and bounded notes. Unknown is not level zero. Candidate facts and source fields are
never modified. Null and explicit empty notes keep their distinct meanings.

Model-supplied reviewer identity/date, global score, policy overrides, tool calls and
extra fields are refused. Duplicate JSON keys, nonfinite numbers, invalid UTF-8,
markdown fences, malformed JSON and unsupported dimensions are rejected without
repair. Source quotes and known scalar/explicit-empty profile references are checked
for every supplied dimension, including zero-weight and low-confidence dimensions.
One unsupported reference invalidates the whole result.

Both the envelope and its draft are unusable as an approved `--review`. The draft's
`reviewed_by` and `reviewed_on` remain null. To continue, copy only `draft_review` to
an ignored file, read the source/profile/rubric, correct or discard each interpretation,
then fill your review provenance and run the existing `--review` command. Keep unknown
dimensions explicit. A reviewer-name string does not authenticate a human; filling
metadata mechanically would not establish that review occurred.

An exact quote and an existing profile path establish presence, not semantic support.
The prompt asks for grounded interpretations, but runtime validation cannot establish
that the proposed level, confidence or notes follow from those references. The fixture
is coding-assistant-curated synthetic data, with independent review still pending.

## Input contract, privacy and budgets

The versioned request is built only in memory. It contains title/location/description,
bounded source-span hints, the validated candidate profile, rubric version and the
nine rubric definitions. Raw source records, application URLs, runtime settings,
credentials, weights and confidence/coverage gates are excluded. The latter remain
deterministic scoring policy. No request body is logged or returned; only its hash is.
Profile declarations/references are untrusted data, not independently verified facts.

Prompt instructions prohibit following source/profile instructions, invoking tools,
opening references, inventing facts or generating approval metadata. No tools are
exposed. These instructions are not a demonstrated prompt-injection or semantic-quality
guarantee. The replay API deliberately accepts a local file path, not an arbitrary
provider. A future live matching adapter must separately address explicit profile
sharing configuration/consent; this increment authorizes no profile transmission.

`matching_proposals` settings have independent bounds from job extraction:

```yaml
matching_proposals:
  max_span_hints: 64
  max_input_bytes: 65536
  max_output_bytes: 131072
  max_output_tokens: 4096
  timeout_seconds: 30
```

The input hash and byte check cover the full serialized request: system prompt,
content (including profile, rubrics and hints), response schema and token setting.
An oversized request causes zero fixture reads. Otherwise one bounded file read is
attempted, with no retry. Output bytes and JSON validation are bounded; the token
setting describes the future request contract and does not count fixture tokens.
The timeout is cooperative and cannot preempt blocking local filesystem I/O.
Cancellation propagates to the caller. Run/call/token-cost accounting and persistent
analysis caching remain Phase 2.5 work.

Replay exits 0 only for `complete`, and 1 for `input_limit`, `output_limit`, `timeout`,
`fixture_unavailable`, `invalid_output` or `ungrounded_evidence`, with a safe status on
stderr. Configuration/selection errors retain existing CLI exit conventions.
`--response-fixture`, `--review` and `--template` are mutually exclusive; matching has
no `--ollama` option in this increment. Discovery and reviewed scoring remain usable
after proposal failure. SQLite, profiles and policies are read-only during replay.

Live local matching integration, explicit profile-sharing policy, model-quality
evaluation, automatic dimension analysis, cross-offer ranking, compensation provenance
and persistent analysis budgets remain planned. Base and optional-extra CI use only
offline fixtures and installed-wheel checks, with no live inference.
