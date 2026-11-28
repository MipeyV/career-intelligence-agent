# Reviewed multidimensional matching (2.3a)

`jobs match` aggregates a local, reviewed assessment against a selected immutable
job revision, a profile snapshot and a versioned policy. It is deterministic,
offline and read-only. It does not derive ratings from prose or call a model.
The result describes configured fit within `reviewed_dimensions_only`, never a
hiring probability, eligibility decision or automatic application recommendation.

## Nine dimensions and explicit policy

The [synthetic policy](../conf/matching.example.yml) defines five anchors per
dimension, explicit weights and coverage/confidence thresholds. There is no default
personal policy: `--policy` is required. Its equal weights and thresholds are
demonstration choices, not calibrated recommendations.

| Key | Reviewed scope |
| --- | --- |
| `technical_skills` | Declared skills against reviewed technical requirements |
| `professional_experience` | Declared experience against reviewed tasks |
| `academics` | Declared education against reviewed academic topics |
| `seniority` | Responsibility level against declared target level |
| `domain` | Declared background against reviewed domain tasks |
| `location` | Disclosed work location against preferences; no work-rights inference |
| `compensation` | Reviewed comparable disclosed compensation against configured preferences |
| `career_objectives` | Reviewed role activities against declared objectives |
| `entrepreneurial_learning` | Reviewed learning opportunities against declared learning goals |

Both weights and rubrics must include exactly these nine dimensions. Each weight
is finite and between 0 and 100, with at least one positive weight. Rubric anchors
are five distinct nonempty texts indexed 0–4. Versions identify the policy and
rubric, while a hash binds their complete contents, including thresholds.

## Prepare a synthetic review

Create an ignored `conf/matching-demo.local.yml` using public synthetic data:

```yaml
schema_version: 1
profile_file: profile.example.yml
companies_file: companies.yml
database_path: ../data/matching-demo/jobs.sqlite3
```

Persist the [synthetic posting](../tests/fixtures/greenhouse/matching.json) and take
its job ID from the listing:

```text
uv run --locked --offline career-intelligence collect --config conf/matching-demo.local.yml --fixture tests/fixtures/greenhouse/matching.json --persist
uv run --locked --offline career-intelligence jobs list --config conf/matching-demo.local.yml
```

Generate the unreviewed JSON template, replacing `JOB_ID`. PowerShell example:

```powershell
uv run --locked --offline career-intelligence jobs match --config conf/matching-demo.local.yml --job-id JOB_ID --policy conf/matching.example.yml --template | Set-Content -Encoding utf8 data/matching-demo/review.local.yml
```

JSON is also accepted by the bounded safe-YAML loader. Keep the binding fields;
fill `reviewed_by` and an exact `reviewed_on` calendar date after reading the source,
profile and rubric. The initial null review fields deliberately prevent assessment.
The nine `assessments` keys must stay explicit. Replace a null only when you can
support that dimension; do not delete unknown dimensions or encode them as zero.

For the synthetic example, the following is one assessment entry, not a complete
review or a proposed real candidate fact:

```yaml
technical_skills:
  level: 3
  confidence: 0.8
  rationale: "Synthetic Python overlap within the reviewed task scope"
  source_evidence:
    - text: "Build Python services"
      evidence: {field: description, start: 0, end: 21}
  profile_refs: [skills.0.name, skills.0.evidence.0]
  strengths: ["Declared Python project evidence"]
  gaps: null
  blockers: null
  transferable_skills: null
  recommendations: null
```

Place this entry under the generated `assessments`, retaining the eight other nulls.
Run the review:

```text
uv run --locked --offline career-intelligence jobs match --config conf/matching-demo.local.yml --job-id JOB_ID --policy conf/matching.example.yml --review data/matching-demo/review.local.yml
```

That one-dimension example has weight coverage 1/9, below the example threshold
0.6, so its overall score is null. The technical dimension is 75/100; the other
dimensions remain unknown. `--revision` explicitly selects a historical revision;
the latest is otherwise selected. A completed assessment exits 0, including
insufficient coverage. Invalid/stale inputs exit 2; storage/selection failures exit 1.

## Evidence and local review boundaries

Every nonnull dimension needs a level (integer 0–4), finite confidence in [0,1],
nonempty rationale, 1–16 exact source quotes and 1–32 profile references. Quotes use
the existing Unicode-code-point spans in title/location/description. Every quote
and reference is checked, even in zero-weight or low-confidence dimensions. One
invalid assessment invalidates the response; partial results are not emitted.

Profile references are dot paths into validated configuration, such as
`skills.0.name` or `preferences.objectives.0`. They must end at a known scalar or an
explicitly empty list. Nulls, nonexistent indices, object/container references and
arbitrary attribute access are refused. False and zero are known values, not null.
References are checked against the profile snapshot; they do not open referenced
files or establish that a declaration was independently verified. Profile status
attestations cannot follow the review date. Verify applicability at that date:
this contract does not reconstruct historical profiles or compute tenure.

Strengths, gaps, blockers, transferable skills and recommendations each preserve
null versus an explicit empty list, with at most 16 bounded notes. These remain
reviewer interpretations. A matching quote/reference proves presence, not that
the rating or notes follow semantically. `reviewed_by` records provenance without
authenticating a human. No extraction response becomes a trusted review automatically.
Unknown pay stays null; missing facts alone must not be encoded as level 0.

Policy/review files use the existing 1 MiB safe-YAML boundary: duplicate keys,
aliases, custom tags, unsupported fields and invalid dimensions are refused.
Private data is redacted from CLI errors. Keep real reviews/exports in ignored
locations; the JSON result can contain private review notes and profile paths.

## Calculation, coverage and versions

The reviewed levels map to 0, 25, 50, 75 and 100. This is a rubric convention, not
a calibrated measurement scale. A dimension contributes only with positive weight
and confidence at or above `minimum_dimension_confidence`. Unknown, zero-weight
and low-confidence dimensions remain visible with separate statuses.

For included dimensions I and configured dimensions D:

```text
coverage = sum(weight[i] for i in I) / sum(weight[d] for d in D)
score = sum(weight[i] * dimension_score[i] for i in I) / sum(weight[i] for i in I)
confidence = sum(weight[i] * declared_confidence[i] for i in I) / sum(weight[i] for i in I)
```

The overall score is withheld below `minimum_coverage` or when no weight is included,
even if the minimum is zero. Unknowns are neither zero ratings nor silently filled
dimensions. Confidence is the reviewer's uncalibrated confidence, averaged only
over included dimensions; it is not hiring probability or a quality guarantee.
The weighted mean assumes the configured rubric scale is suitable for that use.
Exact rational arithmetic on validated decimal values governs thresholds and
aggregation; output is rounded to six decimal places afterward.

Output/review schema and evaluator version are 1, independent of eligibility
schema versions. Results expose anchors, component scores, coverage, confidence,
unknown dimensions, job/revision/profile/policy binding and review/assessment hashes.
Changes to the job revision, profile, weights, anchors, thresholds or versions require
a fresh review. Review provenance and notes enter the assessment hash. No results
are persisted in SQLite or substituted for eligibility, filtering or source facts.

The [2.3b proposal contract](MATCHING-PROPOSALS.md) now supports local response replay,
prompt/version binding and unreviewed drafts without scoring or profile transmission.
[Opt-in local matching](LOCAL-MATCHING.md) adds a separate consent/field-selection gate
for Ollama proposals. Both paths still require local review. Independently validated
automatic dimension analysis, ranking across offers, compensation normalization,
persistent analysis cache and measured real-world quality
remain planned. Synthetic fixtures/tests are coding-assistant-curated; independent
human annotation review and held-out quality evaluation remain pending.
