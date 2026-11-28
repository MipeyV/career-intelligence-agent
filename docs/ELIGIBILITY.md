# Reviewed eligibility rules (2.2a)

This increment evaluates a locally reviewed set of requirements against the configured
profile. It is deterministic, offline and read-only. It does not call a model, infer
requirements from prose, submit applications, change discovery decisions or certify
legal eligibility. Every result has `scope: reviewed_constraints_only`.

An exact quote proves textual presence, not that a requirement was interpreted
correctly. Read the source and review its meaning before encoding a constraint.
Extraction responses cannot be passed directly as requirements files: the reviewed
contract requires its own provenance, source/revision binding and profile hash.
`reviewed_by` records your attestation, not an authentication mechanism. Never treat
model-generated review metadata as proof that a human checked it.

## Prepare and assess a synthetic example

Create an ignored `conf/eligibility-demo.local.yml` using the public examples:

```yaml
schema_version: 1
profile_file: profile.example.yml
companies_file: companies.yml
database_path: ../data/eligibility-demo/jobs.sqlite3
```

Collect the synthetic one-condition posting and obtain its job ID:

```text
uv run --locked --offline career-intelligence collect --config conf/eligibility-demo.local.yml --fixture tests/fixtures/greenhouse/eligibility.json --persist
uv run --locked --offline career-intelligence jobs list --config conf/eligibility-demo.local.yml
```

Generate a template, replacing `JOB_ID`. In PowerShell use UTF-8 output:

```powershell
uv run --locked --offline career-intelligence jobs eligibility --config conf/eligibility-demo.local.yml --job-id JOB_ID --template | Set-Content -Encoding utf8 data/eligibility-demo/review.local.yml
```

Keep the generated identity/hash fields. The initial template is deliberately
unreviewed and cannot be assessed until its review fields are filled. Its JSON is
also valid input for the bounded safe YAML loader. You can edit it as JSON or YAML.
The completed document for this synthetic source has the following shape; replace
the identity placeholders with values from the template and identify the reviewer:

```yaml
schema_version: 1
job_id: COPY_FROM_TEMPLATE
revision: 1
revision_fingerprint: COPY_FROM_TEMPLATE
profile_hash: COPY_FROM_TEMPLATE
reviewed_by: YOUR_REVIEW_IDENTIFIER
reviewed_on: "2026-11-28"
coverage: complete
candidate_assertions:
  complete_sections: []
  work_locations: null
constraints:
  - id: application-close
    kind: application_deadline
    mandatory: true
    cutoff: "2028-06-30"
    source:
      text: "Apply by 2028-06-30."
      evidence: {field: description, start: 0, end: 20}
```

Assess using an explicit exact calendar date:

```text
uv run --locked --offline career-intelligence jobs eligibility --config conf/eligibility-demo.local.yml --job-id JOB_ID --requirements data/eligibility-demo/review.local.yml --as-of 2028-07-01
```

That date is after the synthetic deadline, so the scoped result is `not_eligible`.
The command exits 0 because assessment completed; the offer stays in `jobs list`
with its existing availability and filter state. Syntax, configuration or review errors
return 2 with safe diagnostics; storage/selection failures return 1. `--revision` selects a
historical revision explicitly; otherwise the latest is selected.

## Binding, review and output

A review binds job ID, revision number, revision fingerprint and the current profile
hash. Changing a revision, even a return to historical content, or changing any profile
facts/preferences makes an older review stale. Re-examine it and regenerate/rebind
the template rather than silently copying hashes to bypass review. Reviewed-on is an
exact date; reviewer text and unique constraint IDs are required.

Every constraint carries an exact quote and zero-based Unicode code-point offsets
from `title`, `location` or `description` in the selected revision. Whitespace is
preserved. Invalid evidence invalidates the assessment. No external link is fetched.
Input files have the existing 1 MiB safe-YAML boundary: no aliases, duplicate keys,
custom tags or unsupported fields. At most 128 constraints are accepted.

Output records evaluator version, as-of date, hashes, scoped status, normalized requirements, source quotes,
profile-field references, rule reason codes and any computed bounds. `assessment_hash`
includes the review, evaluator version and as-of date. `unassessed_kinds` exposes
uncovered rule families. These records are not stored in SQLite in this increment;
keep private review files/exports in ignored locations. Candidate values stay local.

## Rule types

All rules have `id`, `kind`, `mandatory` (default true) and `source`. Additional fields:

| Kind | Fields and behavior |
| --- | --- |
| `availability_by` | `cutoff`: compare configured availability against a required start-by date |
| `application_deadline` | `cutoff`: compare the explicit as-of calendar date; exact dates are inclusive |
| `graduation_window` | `earliest` and/or `latest`: compare expected graduation intervals; a compatible expectation is potential, never proof of completion |
| `minimum_experience` | `minimum_years`, nullable `apprenticeship_counts`: compute conservative non-overlapping experience bounds |
| `degree` | `accepted_degrees`: literal alternatives; a declared match is potential because the current profile does not establish completion status |
| `language` | `language`, optional `minimum_proficiency` and `proficiency_order`: compare only the explicit reviewed scale |
| `work_authorization` | `location`, `sponsorship` (`available`, `not_available`, `unknown`): compare declared status only for the matching location |
| `geography` | `allowed_locations`: compare with separately attested feasible locations, never with preferences |

Text labels use whitespace trimming and case-folding, with no inferred country,
degree or language equivalences. Review literal alternatives explicitly. A language
scale might be `[A1, A2, B1, B2, C1, C2]` with minimum `B2`; an unmapped candidate
label such as `fluent` remains unknown. Sponsorship availability means potential,
not an immigration determination. Rights for one location never transfer to another.

Dates accept quoted `YYYY`, `YYYY-MM` or `YYYY-MM-DD`. They remain intervals. A rule
is met only when the intervals establish it; overlap stays unknown. Graduation dates
are expectations, so an expected date outside the window stays uncertain rather than
proving a hard contradiction. No year is silently converted into an invented day.

## Candidate assertions and experience bounds

Lists in a profile may be incomplete. Missing entries ordinarily remain unknown.
`candidate_assertions.complete_sections` can contain `experience`, `education` and/or
`languages` only after you have verified those lists are exhaustive for this review.
A null profile section cannot be declared complete. Explicitly empty plus verified
complete is distinct from missing/unknown. Degree-name mismatches still remain
uncertain because equivalence is not established; an explicitly empty complete
education list can establish absence of a declared degree.

`work_locations` is a separate attested exhaustive set of feasible locations, using
the same canonical labels as the reviewed rule. Null means unknown; an explicit empty
set means no feasible locations are attested. Do not copy preferences as though they
were factual mobility or legal permission. This assertion does not modify the profile
or establish a work right. Both assertions and review provenance enter the review hash.

Experience is clipped to the as-of date and overlapping intervals are merged. Definite
credit needs `counts_as_professional_experience: true`; unknown credit contributes
only to the possible bound, while false is excluded. Apprenticeships additionally
need the job rule's explicit acceptance for definite credit. A missing job policy
keeps that credit uncertain; exclusion removes it. A missing end does not establish
an ongoing episode: its lower bound is zero, with possible credit through as-of.

Exact and partial dates produce day bounds: latest start/earliest end for the lower
bound, earliest start/latest end for the upper, with conservative end-day treatment.
Incomplete history leaves the maximum unknown. For a minimum of Y years, the engine
uses a 365Y–366Y-day threshold band. It confirms sufficiency only above the high
threshold and insufficiency only below the low threshold with a complete history.
Borderline anniversaries may therefore need review. This is a documented conservative
comparison convention, not an exact contractual tenure calculation. Future work is
never credited and gaps are not counted.

## Aggregation and remaining limits

Rules return `met`, `potential`, `unknown` or `contradiction`. Aggregation is ordered:

1. An explicit mandatory contradiction produces `not_eligible` for the reviewed set.
2. No constraints, partial coverage or an unknown mandatory rule produces `uncertain`.
3. Otherwise any potential result or preferred-rule gap produces `potentially_eligible`.
4. Otherwise the result is `eligible` for the reviewed constraints only.

Mark coverage `complete` only when the applicable requirements within your review scope
were checked; this still does not certify unmentioned conditions or all legal rights.
Preferred gaps never become hard rejection. All statuses remain separate from discovery,
source lifecycle, notifications and application tracking. There is no global fit score.

Do not encode either arm of an OR requirement as independently mandatory. Compound
alternatives, requirements tied to a particular degree/episode, verified graduation
completion, broader equivalence mapping and automatic semantic interpretation are not
implemented. Leave such conditions unassessed with partial coverage until reviewed
support exists. The tests use synthetic annotations prepared by the coding assistant;
independent human review and real-world eligibility accuracy measurement remain pending.
No model call or live source check is needed to run these rules.

The assessment date controls deadline and experience calculations. Other profile
facts are the supplied snapshot, not a historical record of rights or language ability;
verify that they apply to the chosen assessment date. A condition requiring a different
effective date or unsupported time-of-day precision should remain unassessed.
