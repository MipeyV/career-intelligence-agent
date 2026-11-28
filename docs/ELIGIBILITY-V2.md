# Groups and dated profile facts (2.2b)

This extends the [reviewed eligibility workflow](ELIGIBILITY.md). Requirements still
need actual local review, source evidence and revision/profile binding. Candidate
facts stay in validated local configuration. No model calls or database writes are
introduced. Results retain `scope: reviewed_constraints_only`.

## Versions and bounded groups

This increment introduced review schema 2. Current templates use
[schema 3 with optional degree mappings](DEGREE-EQUIVALENCES.md), which retains these
group semantics. Existing flat schema-1 and compound schema-2 reviews are accepted;
groups and explicit completion policies require schema 2 or later. Assessment output
and evaluator versions were 2 in 2.2b and are now 3. Group results include nested `conditions`; atomic
results retain their existing fields and scoped meanings.

`any_of` requires at least one feasible alternative; `all_of` requires every child.
Groups have `id`, `mandatory`, `source` and `conditions`. IDs must be unique across
the tree. Each group has 1–32 children, a review has at most 128 total nodes including
groups, and depth is at most four levels including roots. Only top-level requirements
may be preferred; nested `mandatory: false` is refused.

The following constraint matches the description in
[eligibility_alternatives.json](../tests/fixtures/greenhouse/eligibility_alternatives.json):

```yaml
constraints:
  - id: education-or-experience
    kind: any_of
    source:
      text: "A completed MSc degree or at least 2 years of professional experience is required."
      evidence: {field: description, start: 0, end: 82}
    conditions:
      - id: degree
        kind: degree
        accepted_degrees: [MSc]
        completion: completed
        source:
          text: "A completed MSc degree"
          evidence: {field: description, start: 0, end: 22}
      - id: experience
        kind: minimum_experience
        minimum_years: 2
        apprenticeship_counts: null
        source:
          text: "at least 2 years of professional experience"
          evidence: {field: description, start: 26, end: 69}
```

Use a template for that posting/profile and review the meanings before filling it.
For OR, `met` wins, then `potential`, then `unknown`; contradiction requires every
alternative to contradict. For AND, contradiction wins, then unknown, then potential;
all children must be met to return met. Thus a failed degree branch cannot reject a
passing experience branch, while a failed branch plus an unknown alternative remains
unknown. Every branch's evidence is validated, including alternatives not needed to
pass. Root coverage/preferred rules keep the aggregation behavior in the basic guide.

## Explicit, dated facts

Education adds nullable `completed`, `completion_date` and `status_as_of`. Employment
adds nullable `ongoing` and `status_as_of`. These are user-attested facts, not facts
verified by the application. Setting either boolean, including false, requires an
exact status date and a nonempty evidence-reference list. References are not opened
or sent to a model. A profile status cannot follow the requirement review date.

```yaml
education:
  - institution: Example Institute
    degree: MSc
    expected_graduation: "2028"
    completed: true
    completion_date: "2028-05"
    status_as_of: "2028-06-30"
    evidence: [Synthetic diploma record]
experience:
  - employer: Example Company
    role: Engineer
    start: "2026-01"
    end: null
    kind: employment
    counts_as_professional_experience: true
    ongoing: true
    status_as_of: "2028-06-30"
    evidence: [Synthetic employment status record]
```

These are synthetic examples, not proposed candidate facts. Unknown booleans stay
null without status metadata. A completion date needs `completed: true`; its possible
earliest date cannot follow the attestation. No completion day is invented: a true
status establishes completion by its date, and a partial completion date narrows that
interval. False means not yet completed on that date; completion after that date can
become unknown until the profile is updated.

Ongoing employment cannot have an end date. Definite credit stops at its attestation,
clipped to assessment date. Continued work afterward is only possible, not confirmed.
A false/ended status without an end date keeps a zero lower duration bound and caps
possible duration at its attestation. Existing unknown-status missing-end episodes
retain their previous uncertainty. Professional/apprenticeship policies still apply.

New unknown fields remain null in serialization but are omitted from the canonical
profile hash view. Frozen legacy profile/filter bindings therefore stay valid while
these facts remain unknown. Known facts and evidence change the hash, requiring review
again and possibly `jobs refilter`; no existing database rows are migrated.

## Conditions on the same qualification

`degree` now accepts `completion: completed` or `expected_or_completed`, and optional
`graduation_earliest` / `graduation_latest`. A window requires an explicit completion
policy. Completion and window are evaluated on the same education entry. One completed
qualification cannot combine with another qualification's forecast to pass.

Each matching entry is an alternative path. Hard failure still requires a complete
education profile without an unresolved alternative qualification. Unmapped degree
equivalence remains unknown unless resolved by an explicit scoped schema-3 review.
A completed requirement uses dated facts at assessment
time. An expected-or-completed requirement may use a forecast, giving at most potential;
an out-of-window forecast does not prove contradiction. Actual completion supersedes
the forecast for that entry under this bound rule.

Without a completion policy, unverified legacy matches remain potential; established
completion can satisfy them. Standalone `graduation_window` still concerns expected
dates; use a bound degree rule for a qualification's completion/window. General
cross-condition bindings beyond this rule, equivalence inference, authentication of
review and automatic semantic interpretation remain planned. Source meanings and
attested facts still need human verification; tests are synthetic contract examples,
not real-world eligibility accuracy measurements.
