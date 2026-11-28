# Scoped degree-equivalence reviews (2.2c)

Review schema 3 adds explicit decisions about one education entry and one accepted
degree label within one `degree` rule. New templates use schema 3; flat schema-1
and compound schema-2 reviews remain accepted without equivalences. Assessment
output and evaluator versions are 3. Existing profile/filter hashes stay unchanged;
review and assessment hashes are recomputed with the new normalized contract/version.

This extends [the eligibility workflow](ELIGIBILITY.md) and
[the compound-rule contract](ELIGIBILITY-V2.md). It does not infer qualification
hierarchies, recognize an accrediting authority or certify academic equivalence.
The local reviewer must check whether the source actually allows the proposed
alternative. A name in `reviewed_by` does not authenticate that review.

## Review one qualification against one requirement

The public [synthetic fixture](../tests/fixtures/greenhouse/eligibility_equivalence.json)
contains `A completed MSc degree or equivalent is required.` Generate a template
for that persisted posting and your current profile, then review this constraint:

```yaml
schema_version: 3
# Keep the job/revision/profile binding from the generated template.
# Fill reviewed_by, reviewed_on, coverage and other template fields after review.
constraints:
  - id: degree
    kind: degree
    accepted_degrees: [MSc]
    completion: completed
    source:
      text: "A completed MSc degree or equivalent is required."
      evidence: {field: description, start: 0, end: 49}
    equivalences:
      - education_index: 0
        accepted_degree: MSc
        equivalent: true
        rationale: "Synthetic comparison applicable to this requirement"
        evidence: ["Synthetic qualification comparison record"]
```

This is a constraint excerpt, not a completed review or a proposed candidate fact.
`education_index` is a zero-based index into the bound profile's education list.
`accepted_degree` must name one of this rule's accepted labels. `equivalent` is an
explicit boolean, with a nonempty rationale and 1–16 evidence references. References
are local review provenance: the application does not open files, fetch URLs or
independently establish their authority. Keep private reviews/exports ignored by Git.

Mappings are unique per education-index/accepted-label pair, using trimmed,
case-folded labels. At most 64 mappings are allowed per rule. Unknown decisions
are omitted; `null`, string booleans, missing evidence and duplicate/conflicting
pairs are refused. There is no blanket declaration covering unseen qualifications.
An out-of-range index or a redundant decision for an identical literal degree label
invalidates the entire assessment, even in an unused OR branch.

## Outcome and provenance

For each education entry, any literal or positively reviewed accepted alternative
can match. Completion and optional graduation windows are then evaluated on that
same entry. An equivalence does not supply completion, a date or another entry's
facts. Forecasts retain their existing potential/unknown meanings.

`equivalent: false` rejects only that one pairing. The entry contradicts the label
requirement only when every accepted label has an explicit negative review. An
unreviewed label or qualification remains unknown. Hard rejection still requires
an exhaustive education assertion and no passing or unresolved qualification path.
Preferred requirements and compound alternatives retain their existing safeguards.

| Situation for a qualification | Match basis in output |
| --- | --- |
| Degree label matches an accepted label | `literal` |
| At least one accepted alternative has a positive review | `reviewed_equivalence` |
| Every accepted alternative has a negative review | `reviewed_non_equivalence` |
| No match and at least one alternative remains unreviewed | `unmapped` |

Per-entry results include the education index, match basis, outcome and indices of
the relevant mapping records in that rule's `requirement.equivalences` list. The
normalized requirement retains the rationale and evidence references. Relevant
indices describe label matching, not proof of completion or suitability.

Decisions, rationale and references enter the review/assessment hashes. No mapping
transfers to another rule, job or profile; there is no transitive or reverse lookup.
Editing or reordering education changes the bound profile hash and requires a new
review. Review provenance describes the supplied snapshot; verify applicability at
the chosen assessment date. Review files and results remain separate from source
facts, profiles, filtering and application state. SQLite and the profile are read-only.

General cross-condition bindings, automatic semantic normalization, inferred degree
or language equivalences, independent annotation review and real-world accuracy
measurement remain planned. Synthetic tests establish contract behavior, not the
correctness of a human equivalence judgment. No model, source or remote service is
called by this increment.
