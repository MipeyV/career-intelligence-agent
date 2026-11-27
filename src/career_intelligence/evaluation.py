"""Offline measurements on an explicitly labelled synthetic development dataset."""

import hashlib
import platform
import statistics
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from sqlalchemy import func, select

from career_intelligence.backup import copy_database
from career_intelligence.collection import CollectionPreview, CollectionSnapshot, Posting
from career_intelligence.config import CandidateProfile, FilterPolicy
from career_intelligence.filtering import configuration_hash, evaluate
from career_intelligence.storage import Store, changes, list_changes, list_jobs

OBSERVATION = datetime(2026, 11, 27, tzinfo=UTC)


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    relevance: Literal["relevant", "irrelevant", "ambiguous"]
    expected_decision: Literal["keep", "review", "exclude"]
    rationale: str = Field(min_length=1)
    posting: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_posting_fields(self):
        reserved = {
            "source_provider",
            "source_scope",
            "source_post_id",
            "company_key",
            "company",
            "source_url",
            "observed_at",
            "raw_record",
        }
        if (
            reserved.intersection(self.posting)
            or not self.posting.keys() <= Posting.model_fields.keys()
        ):
            raise ValueError("case posting contains reserved or unsupported fields")
        return self


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    dataset_version: str
    annotation_provenance: str
    profile: CandidateProfile
    policy: FilterPolicy
    keyword_baseline: list[str] = Field(min_length=1)
    cases: list[Case] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("case IDs must be unique")
        return self


def _posting(case: Case) -> Posting:
    return Posting(
        source_provider="fixture",
        source_scope="benchmark",
        source_post_id=case.id,
        company_key="synthetic",
        company="Synthetic Benchmark Company",
        source_url=f"https://example.com/benchmark/{case.id}",
        observed_at=OBSERVATION,
        raw_record=case.posting,
        **case.posting,
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _metrics(cases: list[Case], predictions: list[bool]) -> dict:
    matrix = Counter()
    ambiguous = retained_ambiguous = 0
    for case, retained in zip(cases, predictions, strict=True):
        if case.relevance == "ambiguous":
            ambiguous += 1
            retained_ambiguous += retained
            continue
        positive = case.relevance == "relevant"
        matrix[
            "tp" if positive and retained else "fn" if positive else "fp" if retained else "tn"
        ] += 1
    return {
        "confusion": {key: matrix[key] for key in ("tp", "fp", "tn", "fn")},
        "precision": _ratio(matrix["tp"], matrix["tp"] + matrix["fp"]),
        "recall": _ratio(matrix["tp"], matrix["tp"] + matrix["fn"]),
        "false_negative_rate": _ratio(matrix["fn"], matrix["tp"] + matrix["fn"]),
        "ambiguous_retained": retained_ambiguous,
        "ambiguous_total": ambiguous,
    }


def evaluate_dataset(path: Path) -> dict:
    with path.open("rb") as stream:
        payload = stream.read(1_048_577)
    if len(payload) > 1_048_576:
        raise ValueError("benchmark dataset exceeds the 1 MiB limit")
    dataset = Dataset.model_validate_json(payload)
    posts = [_posting(case) for case in dataset.cases]
    elapsed = []
    results = []
    consistent = grounded = evidence_count = 0
    for case, post in zip(dataset.cases, posts, strict=True):
        start = time.perf_counter()
        result = evaluate(post, dataset.profile, dataset.policy)
        elapsed.append((time.perf_counter() - start) * 1000)
        consistent += evaluate(post, dataset.profile, dataset.policy) == result
        for reason in result.reasons:
            field = getattr(post, reason.field, None)
            if reason.evidence is not None and isinstance(field, str):
                evidence_count += 1
                grounded += reason.evidence in field
        text = f"{post.title}\n{post.description or ''}".casefold()
        keyword = any(term.casefold() in text for term in dataset.keyword_baseline)
        results.append(
            {
                "id": case.id,
                "relevance": case.relevance,
                "expected_decision": case.expected_decision,
                "decision": result.decision,
                "keyword_retained": keyword,
                "reasons": [reason.model_dump(mode="json") for reason in result.reasons],
            }
        )

    with tempfile.TemporaryDirectory(prefix="career-benchmark-") as temporary:
        root = Path(temporary)
        database = root / "benchmark.sqlite3"
        start = time.perf_counter()
        snapshot = CollectionSnapshot(
            source_key="benchmark",
            scope_key="fixture:benchmark",
            started_at=OBSERVATION,
            completed_at=OBSERVATION,
            status="complete",
            records=posts,
        )
        preview = CollectionPreview(mode="fixture", snapshots=[snapshot])
        with Store(database) as store:
            initial = store.ingest(preview)
            decisions = store.refilter(dataset.profile, dataset.policy)
            replay = store.ingest(preview)
            failure = snapshot.model_copy(
                update={
                    "status": "failed",
                    "records": [],
                    "started_at": OBSERVATION + timedelta(hours=1),
                    "completed_at": OBSERVATION + timedelta(hours=1),
                }
            )
            failed_run = store.ingest(CollectionPreview(mode="fixture", snapshots=[failure]))
            with store.engine.connect() as connection:
                event_count = connection.scalar(select(func.count()).select_from(changes))
        duration = (time.perf_counter() - start) * 1000
        backup = root / "snapshot.sqlite3"
        restored = root / "restored.sqlite3"
        copy_database(database, backup)
        copy_database(backup, restored)
        restored_jobs = list_jobs(restored, limit=len(posts))
        restore_equal = list_jobs(database, limit=len(posts)) == restored_jobs and list_changes(
            database, limit=100000
        ) == list_changes(restored, limit=100000)
        workload = {
            "normalized_items": len(posts),
            "peak_snapshot_items": len(posts),
            "normalization_included": False,
            "duration_ms": duration,
            "initial_change_events": initial["change_count"],
            "replayed_change_events": replay["change_count"],
            "duplicate_event_rate": _ratio(replay["change_count"], initial["change_count"]),
            "total_change_events": event_count,
            "failed_source_status": failed_run["sources"][0]["status"],
            "jobs_after_failure": len(list_jobs(database, limit=len(posts))),
            "filter_counts": decisions["counts"],
            "backup_restore_equal": restore_equal,
        }
    predictions = [result["decision"] for result in results]
    return {
        "report_version": 1,
        "dataset_version": dataset.dataset_version,
        "dataset_sha256": hashlib.sha256(payload).hexdigest(),
        "annotation_provenance": dataset.annotation_provenance,
        "configuration_hash": configuration_hash(dataset.profile, dataset.policy),
        "environment": {
            "python": platform.python_version(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "case_count": len(results),
        "labels": dict(Counter(case.relevance for case in dataset.cases)),
        "discovery_retained": _metrics(
            dataset.cases, [value != "exclude" for value in predictions]
        ),
        "strict_keep": _metrics(dataset.cases, [value == "keep" for value in predictions]),
        "keyword_baseline": _metrics(dataset.cases, [row["keyword_retained"] for row in results]),
        "decision_agreement": _ratio(
            sum(
                case.expected_decision == result
                for case, result in zip(dataset.cases, predictions, strict=True)
            ),
            len(results),
        ),
        "repeat_consistency": _ratio(consistent, len(results)),
        "evidence_grounding": {
            "matching_string_spans": grounded,
            "string_spans": evidence_count,
            "rate": _ratio(grounded, evidence_count),
        },
        "latency_ms": {"median": statistics.median(elapsed), "maximum": max(elapsed)},
        "llm_calls": 0,
        "llm_cost": {"amount": 0, "currency": "EUR"},
        "eligibility_accuracy": None,
        "hybrid_comparison": None,
        "workload": workload,
        "cases": results,
    }
