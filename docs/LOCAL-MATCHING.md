# Opt-in local matching proposals (2.3c)

`jobs match --ollama` can request dimension proposals from the existing optional
local Ollama adapter. It reuses the [proposal validation/review boundary](MATCHING-PROPOSALS.md)
and the [reviewed scoring contract](MATCHING.md). There is no automatic approval,
aggregate score, application action or change to candidate facts.

## Explicit local sharing

The shipped settings have `matching_local.share_profile: false` and an empty field
list. Merely configuring Ollama, installing the extra or collecting offers grants no
profile-sharing permission. Calling `--ollama` while sharing is disabled returns
`profile_sharing_disabled`, with zero provider attempts and no input body/hash.
This check also works without the optional packages installed.

For a deliberate local run, first use a trusted daemon and an already installed
completion model as described in the [local-provider guide](EXTRACTION.md#optional-local-ollama-21b).
That guide covers disabling daemon cloud features and the trust boundary: loopback
and reported metadata cannot prove that a malicious server will never forward data.
This application does not configure/restart the daemon or download a model.

After reviewing what you intend to share, explicitly edit an ignored settings copy.
The following selection refers to the public synthetic profile; choose exact paths
from your own validated profile for personal use:

```yaml
ollama:
  model: YOUR_INSTALLED_LOCAL_MODEL
  base_url: http://127.0.0.1:11434
matching_local:
  share_profile: true
  profile_fields:
    - skills.0.name
    - skills.0.evidence.0
```

`share_profile: true` authorizes only explicitly requested local matching with that
configuration's selected fields. It is not permission for remote inference, trace
uploads or automatic runs. The command never modifies private settings or profiles.
Keep personal settings/reviews/exports ignored by Git.

Only known scalar leaves, explicit empty lists and explicit nulls can be selected.
Nulls convey unknown facts and cannot support a nonnull dimension assessment.
An unavailable path, object or nonempty list is refused before any provider request;
select specific leaves instead of sharing an entire section. At most 128 unique
validated dot paths are allowed. Dates retain their supplied precision. Profile
evidence strings are data and may contain sensitive text; select them deliberately.

After installing the approved optional extra, explicitly request proposals:

```text
uv sync --locked --dev --extra ollama
uv run --locked --offline --extra ollama career-intelligence jobs match --config conf/matching-demo.local.yml --job-id JOB_ID --policy conf/matching.example.yml --ollama
```

Use the [matching demo](MATCHING.md) to prepare those files/ID first. `uv --offline`
prevents package downloads; `--ollama` still contacts the configured loopback daemon.
There is no live call in default tests, CI or installed-wheel validation.

## Minimized request and strict response

The user message contains `profile_facts`, a flat map from allowed dot paths to
their selected values, plus selected posting fields, bounded hints and rubric
definitions. It contains no whole-profile object, unselected candidate facts,
raw source record, runtime settings, policy weights or credentials. Source text,
profile declarations, references and rubrics remain untrusted data. No tools or
file/URL access are available to the model.

The existing provider allows only a validated loopback-IP origin and one `/api/show`
preflight followed by one `/api/chat`. The preflight receives only the model name;
reported remote/unknown/non-completion models are refused before posting/profile
content is sent. Redirects, proxies, inherited credentials, tracing, retries, tool
calls and incomplete generations remain disabled/refused. Native schema steering
does not repair bad JSON. HTTP/response bytes, output tokens, context and timeout
remain bounded. Pinned SDK clients close on success, failure and cancellation.

Input budgeting includes the entire serialized contract before any preflight. Inputs
are snapshotted across the asynchronous call so caller mutation cannot silently
change the bound profile, policy or selected source. Every returned profile reference
must be in the exact selected allowlist, even if an unshared field happens to exist
locally. All quotes and selected known profile references are validated, including
zero-weight/low-confidence dimensions. Any unsupported dimension discards the entire
draft. An existing quote/path still proves presence rather than semantic correctness.

## Versions, statuses and provenance

Proposal/result envelope versions are now 2 and prompt version is
`matching-proposal-v2`, including local replay. The response schema and reviewed
matching schema/evaluator stay at 1. Result changes are:

- `provider` is `fixture` or `ollama`, with a configured model tag for Ollama;
- `provider_attempts` counts at most one generation attempt (preflight/chat are its
  transport requests); `fixture_reads` stays zero for Ollama;
- `profile_fields` records the selected paths for local inference, not their values;
- `input_sha256` may be null when sharing prevents request construction;
- `inference_config_sha256` binds Ollama settings, sharing selection and budgets;
- `profile_hash` still binds the full local profile, including unshared facts.

The input hash covers system/content/schema/token contract. Model/context/seed and
other transport/budget options are bound separately by `inference_config_sha256`.
The model tag is a configured label, not an independently verified model digest.
Keep the result envelope for provenance; later manual reviews do not embed it.

In addition to existing proposal outcomes, local mode returns
`profile_sharing_disabled`, `provider_not_installed` or `provider_unavailable` as safe
failures (exit 1). Invalid configuration/field selections exit 2. No draft is emitted
on failure. Successful proposals retain null reviewer metadata and cannot be scored
until actual local review. Declared confidence remains uncalibrated.

Validation for this milestone uses synthetic profiles and the real pinned SDK with
mock HTTP transport, without a live daemon or model. Live hardware/context/quality
validation, independent annotation review, cross-offer ranking, persistent caching
and run-wide token/cost accounting remain pending. No matching-model quality claim
follows from offline transport success.
