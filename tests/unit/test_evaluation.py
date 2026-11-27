import json

import pytest
from pydantic import ValidationError

from career_intelligence.evaluation import Dataset, _metrics, evaluate_dataset


def test_benchmark_metrics_and_replay_invariants(repository_root):
    report = evaluate_dataset(repository_root / "evaluation/cases.json")
    assert report["case_count"] == 16
    assert report["labels"] == {"relevant": 8, "irrelevant": 4, "ambiguous": 4}
    assert report["discovery_retained"]["confusion"] == {"tp": 8, "fp": 2, "tn": 2, "fn": 0}
    assert report["strict_keep"]["confusion"] == {"tp": 6, "fp": 0, "tn": 4, "fn": 2}
    assert report["discovery_retained"]["ambiguous_retained"] == 4
    assert report["repeat_consistency"] == 1
    assert report["evidence_grounding"]["rate"] == 1
    assert report["llm_calls"] == 0
    assert report["eligibility_accuracy"] is None
    assert report["workload"]["replayed_change_events"] == 0
    assert report["workload"]["total_change_events"] == 16
    assert report["workload"]["failed_source_status"] == "failed"
    assert report["workload"]["jobs_after_failure"] == 16
    assert report["workload"]["backup_restore_equal"]
    assert report["workload"]["duration_ms"] >= 0
    recorded = json.loads(
        (repository_root / "evaluation/baseline-report.json").read_text(encoding="utf-8")
    )
    assert recorded["dataset_sha256"] == report["dataset_sha256"]
    assert recorded["configuration_hash"] == report["configuration_hash"]
    assert recorded["discovery_retained"] == report["discovery_retained"]


def test_metric_denominators_are_explicit_when_no_definite_labels():
    assert _metrics([], [])["precision"] is None
    assert _metrics([], [])["recall"] is None


def test_duplicate_annotation_ids_fail(repository_root):
    data = json.loads((repository_root / "evaluation/cases.json").read_text(encoding="utf-8"))
    data["cases"][1]["id"] = data["cases"][0]["id"]
    with pytest.raises(ValidationError, match="unique"):
        Dataset.model_validate(data)
