# Synthetic development benchmark

Run from the repository root after the locked installation.

```text
uv run --locked --offline python scripts/evaluate.py
uv run --locked --offline python scripts/evaluate.py --output evaluation/baseline-report.json
```

The second command refreshes the recorded measurements. Keep changes to case labels separate from algorithm changes during review.

## Cases and annotation

`cases.json` contains 16 synthetic descriptions with 8 relevant, 4 irrelevant and 4 ambiguous opportunities. Each case carries a relevance label, expected discovery decision and rationale. The coding assistant curated these cases and annotations. Independent human review is pending.

The profile and policy are synthetic and included in the dataset. A case's structured fields are supplied evaluation inputs. The benchmark does not measure extraction of experience or authorization from prose. The API-to-CLI path has a separate mocked end-to-end integration test.

This is a small development set with no held-out split. Case-level agreement demonstrates conformance to the stated examples. Real-world precision and recall need independently reviewed data and a held-out evaluation in Phase 6.

## Metric definitions

- Discovery retained treats both `keep` and `review` as positive.
- Strict keep treats only `keep` as positive.
- The keyword baseline retains a case if any configured keyword occurs in its title or description, case-insensitively.
- Relevant cases are positive labels and irrelevant cases are negative labels in the binary metrics. Ambiguous cases are excluded from binary denominators and reported separately.
- Precision is TP / (TP + FP), recall is TP / (TP + FN), and false-negative rate is FN / (TP + FN). Undefined denominators produce JSON null.
- Evidence grounding checks whether each emitted string evidence span occurs in its stated posting field. Unknown values contribute no span to that denominator.
- Repeat consistency compares two evaluations of fixed inputs. Decision agreement compares actual decisions against the expected discovery labels.

The normalized-posting workload measures storage, filtering, exact snapshot replay and a failed-source observation. Its duration excludes HTTP collection, normalization, backup and restore. The report records the maximum snapshot item count, event count after replay, failure outcome and a separate backup/restore equality check. Duplicate-event rate is replay-created events divided by initially created events.

The benchmark calls no model. LLM calls and runtime model cost are zero. Eligibility accuracy and hybrid comparison remain null until those components and labels exist.

## Recorded baseline

The committed `baseline-report.json` records the measured output, dataset byte-level SHA-256, configuration hash and execution environment. Evaluation JSON files use LF line endings across platforms to preserve that input hash. On Windows 11, AMD64, CPython 3.12.14, with the processor recorded in that JSON, the following results were measured on 2026-11-27.

| Method | TP | FP | TN | FN | Precision | Recall | False-negative rate | Ambiguous retained |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Discovery retained | 8 | 2 | 2 | 0 | 80% | 100% | 0% | 4 / 4 |
| Strict keep | 6 | 0 | 4 | 2 | 100% | 75% | 25% | 0 / 4 |
| Keyword baseline | 7 | 3 | 1 | 1 | 70% | 87.5% | 12.5% | 3 / 4 |

The two irrelevant opportunities retained for review are sales engineering and accounting roles with uncertain titles. The filter deliberately leaves those for inspection. All 16 expected discovery decisions agreed with the annotations, and all 36 emitted string evidence spans matched their source fields.

The single recorded run measured approximately 0.067 ms median and 0.213 ms maximum per filter evaluation. The 16-item normalized workload took approximately 91.45 ms, created 16 initial events and zero replay events, retained all jobs after a failed source, and restored equal jobs and events from backup. These timings are descriptive observations from one small run and vary with hardware, caches and workload.

## Review before model integration

Review labels independently, add difficult real-source cases with permitted use, reserve a held-out split, and define treatment of eligibility uncertainty. Compare future structured/hybrid analysis with this baseline on the same frozen dataset. Preserve report version, input hashes, model/prompt versions and actual usage/cost attribution when models are introduced.
