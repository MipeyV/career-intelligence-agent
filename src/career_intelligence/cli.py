"""Validate settings or preview collection without persisting state."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from career_intelligence import __version__
from career_intelligence.backup import copy_database
from career_intelligence.config import ConfigurationError, load_configuration
from career_intelligence.eligibility import (
    _exact_date,
    assess_eligibility,
    load_review,
    review_template,
)
from career_intelligence.extraction import FixtureProvider, extract_revision
from career_intelligence.filtering import configuration_hash
from career_intelligence.matching import (
    assess_matching,
    load_matching_review,
    load_policy,
    matching_template,
)
from career_intelligence.ollama_provider import OllamaProvider
from career_intelligence.presentation import change_table, job_table
from career_intelligence.preview import preview
from career_intelligence.storage import StorageError, Store, get_revision, list_changes, list_jobs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="career-intelligence", description="Validate configuration and preview job collection."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    config_parser = commands.add_parser("config", help="Inspect configuration")
    config_commands = config_parser.add_subparsers(dest="config_command", required=True)
    validate_parser = config_commands.add_parser(
        "validate", help="Validate settings and linked YAML"
    )
    validate_parser.add_argument("--config", type=Path, required=True, help="Path to settings YAML")
    collect_parser = commands.add_parser("collect", help="Preview normalized jobs as JSON")
    collect_parser.add_argument("--config", type=Path, required=True, help="Path to settings YAML")
    mode = collect_parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixture", type=Path, help="Read a Greenhouse JSON response offline")
    mode.add_argument(
        "--live", action="store_true", help="Fetch enabled Greenhouse boards over HTTPS"
    )
    collect_parser.add_argument("--source", help="Select one enabled source for live collection")
    collect_parser.add_argument(
        "--persist", action="store_true", help="Save observations to SQLite"
    )
    jobs_parser = commands.add_parser("jobs", help="Inspect persisted opportunities")
    jobs_commands = jobs_parser.add_subparsers(dest="jobs_command", required=True)
    jobs_list = jobs_commands.add_parser("list", help="List current job revisions as JSON")
    jobs_list.add_argument("--config", type=Path, required=True)
    jobs_list.add_argument("--limit", type=int, default=100)
    jobs_list.add_argument("--offset", type=int, default=0)
    jobs_list.add_argument("--format", choices=["json", "table"], default="json")
    jobs_list.add_argument("--decision", help="Comma-separated keep,review,exclude labels")
    refilter_parser = jobs_commands.add_parser(
        "refilter", help="Evaluate stored jobs with current preferences"
    )
    refilter_parser.add_argument("--config", type=Path, required=True)
    extraction_parser = jobs_commands.add_parser(
        "extract", help="Extract quoted facts from a stored revision (fixture or local Ollama)"
    )
    extraction_parser.add_argument("--config", type=Path, required=True)
    extraction_parser.add_argument("--job-id", required=True)
    extraction_parser.add_argument("--revision", type=int)
    extraction_mode = extraction_parser.add_mutually_exclusive_group(required=True)
    extraction_mode.add_argument("--response-fixture", type=Path)
    extraction_mode.add_argument(
        "--ollama", action="store_true", help="Call configured local Ollama"
    )
    eligibility_parser = jobs_commands.add_parser(
        "eligibility", help="Assess locally reviewed requirements without changing job state"
    )
    eligibility_parser.add_argument("--config", type=Path, required=True)
    eligibility_parser.add_argument("--job-id", required=True)
    eligibility_parser.add_argument("--revision", type=int)
    eligibility_mode = eligibility_parser.add_mutually_exclusive_group(required=True)
    eligibility_mode.add_argument("--template", action="store_true")
    eligibility_mode.add_argument("--requirements", type=Path)
    eligibility_parser.add_argument("--as-of", help="Exact YYYY-MM-DD assessment date")
    matching_parser = jobs_commands.add_parser(
        "match", help="Aggregate locally reviewed dimension ratings without changing job state"
    )
    matching_parser.add_argument("--config", type=Path, required=True)
    matching_parser.add_argument("--job-id", required=True)
    matching_parser.add_argument("--revision", type=int)
    matching_parser.add_argument("--policy", type=Path, required=True)
    matching_mode = matching_parser.add_mutually_exclusive_group(required=True)
    matching_mode.add_argument("--template", action="store_true")
    matching_mode.add_argument("--review", type=Path)
    changes_parser = commands.add_parser("changes", help="Inspect durable change events")
    changes_commands = changes_parser.add_subparsers(dest="changes_command", required=True)
    changes_list = changes_commands.add_parser("list", help="List change events as JSON")
    changes_list.add_argument("--config", type=Path, required=True)
    changes_list.add_argument("--since-run", help="Exclude this run and earlier runs")
    changes_list.add_argument("--limit", type=int, default=100)
    changes_list.add_argument("--offset", type=int, default=0)
    changes_list.add_argument("--format", choices=["json", "table"], default="json")
    database_parser = commands.add_parser("db", help="Back up or restore a SQLite database")
    database_commands = database_parser.add_subparsers(dest="db_command", required=True)
    backup_parser = database_commands.add_parser(
        "backup", help="Create a consistent database snapshot"
    )
    backup_parser.add_argument("--config", type=Path, required=True)
    backup_parser.add_argument("--output", type=Path, required=True)
    restore_parser = database_commands.add_parser(
        "restore", help="Restore into a new database file"
    )
    restore_parser.add_argument("--backup", type=Path, required=True)
    restore_parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    assessment_date = None
    if args.command == "jobs" and args.jobs_command == "eligibility" and not args.template:
        try:
            assessment_date = _exact_date(args.as_of or "")
        except ValueError:
            eligibility_parser.error("--as-of requires an exact YYYY-MM-DD date")
    if args.command == "collect" and args.source is not None and not args.live:
        collect_parser.error("--source requires --live")
    selected_decisions = None
    if args.command == "jobs" and args.jobs_command == "list" and args.decision is not None:
        selected_decisions = {label.strip() for label in args.decision.split(",")}
        if not selected_decisions <= {"keep", "review", "exclude"}:
            jobs_list.error("--decision accepts keep,review,exclude")
    try:
        if args.command == "db" and args.db_command == "restore":
            restored = copy_database(args.backup, args.destination)
            print(json.dumps({"event": "database_restored", **restored}))
            return 0
        loaded = load_configuration(args.config)
        if args.command == "jobs" and args.jobs_command == "match":
            policy = load_policy(args.policy)
            revision = get_revision(
                loaded.settings.database_path, args.job_id, number=args.revision
            )
            result = (
                matching_template(revision, loaded.profile, policy)
                if args.template
                else assess_matching(
                    revision, loaded.profile, policy, load_matching_review(args.review)
                )
            )
            print(json.dumps(result))
            return 0
        if args.command == "jobs" and args.jobs_command == "eligibility":
            revision = get_revision(
                loaded.settings.database_path, args.job_id, number=args.revision
            )
            result = (
                review_template(revision, loaded.profile)
                if args.template
                else assess_eligibility(
                    revision, loaded.profile, load_review(args.requirements), as_of=assessment_date
                )
            )
            print(json.dumps(result))
            return 0
        if args.command == "jobs" and args.jobs_command == "extract":
            if args.ollama and loaded.settings.ollama is None:
                raise ConfigurationError("Configure the ollama model and loopback endpoint first")
            revision = get_revision(
                loaded.settings.database_path, args.job_id, number=args.revision
            )
            limits = loaded.settings.extraction
            provider = (
                OllamaProvider(loaded.settings.ollama, limits)
                if args.ollama
                else FixtureProvider(args.response_fixture, limits.max_output_bytes)
            )
            result = asyncio.run(
                extract_revision(
                    revision,
                    provider,
                    provider_name="ollama" if args.ollama else "fixture",
                    limits=limits,
                    model_name=loaded.settings.ollama.model if args.ollama else None,
                )
            )
            print(result.model_dump_json())
            if result.status != "complete":
                print(
                    json.dumps({"event": "extraction_failed", "status": result.status}),
                    file=sys.stderr,
                )
            return 0 if result.status == "complete" else 1
        if args.command == "db":
            backup = copy_database(loaded.settings.database_path, args.output)
            print(json.dumps({"event": "database_backed_up", **backup}))
            return 0
        if args.command == "changes":
            records = list_changes(
                loaded.settings.database_path,
                since_run=args.since_run,
                limit=args.limit,
                offset=args.offset,
            )
            print(
                change_table(records)
                if args.format == "table"
                else json.dumps({"schema_version": 1, "changes": records})
            )
            return 0
        if args.command == "jobs":
            if args.jobs_command == "refilter":
                if not loaded.settings.database_path.is_file():
                    raise StorageError("Database does not exist. Run collect with --persist first")
                with Store(loaded.settings.database_path) as store:
                    summary = store.refilter(loaded.profile, loaded.settings.filters)
                print(json.dumps({"schema_version": 1, "event": "jobs_refiltered", **summary}))
                return 0
            records = list_jobs(
                loaded.settings.database_path,
                limit=args.limit,
                offset=args.offset,
                config_hash=configuration_hash(loaded.profile, loaded.settings.filters),
                decisions=selected_decisions,
            )
            print(
                job_table(records)
                if args.format == "table"
                else json.dumps({"schema_version": 1, "jobs": records})
            )
            return 0
        if args.command == "collect":
            if args.persist:
                with Store(loaded.settings.database_path) as store:
                    result = asyncio.run(
                        preview(
                            loaded,
                            fixture=args.fixture,
                            source_key=args.source,
                        )
                    )
                    saved = store.ingest(result, loaded.settings.reconciliation)
                    filters = store.refilter(loaded.profile, loaded.settings.filters)
                    saved["filters"] = filters
                print(json.dumps({"event": "collection_persisted", **saved}), file=sys.stderr)
                if saved["status"] == "failed":
                    return 1
            else:
                result = asyncio.run(preview(loaded, fixture=args.fixture, source_key=args.source))
            for snapshot in result.snapshots:
                if snapshot.issues:
                    print(
                        json.dumps(
                            {
                                "event": "collection_source_issues",
                                "source_key": snapshot.source_key,
                                "status": snapshot.status,
                                "issues": [issue.model_dump() for issue in snapshot.issues],
                            }
                        ),
                        file=sys.stderr,
                    )
            print(result.model_dump_json())
            return result.exit_code
    except ConfigurationError as exc:
        print(json.dumps({"event": "configuration_error", "message": str(exc)}), file=sys.stderr)
        return 2
    except (StorageError, SQLAlchemyError, OSError) as exc:
        message = str(exc) if isinstance(exc, StorageError) else "Database operation failed"
        print(json.dumps({"event": "storage_error", "message": message}), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "event": "configuration_valid",
                "schema_version": loaded.settings.schema_version,
                "company_count": len(loaded.watchlist.companies),
                "enabled_source_count": sum(
                    source.enabled
                    for company in loaded.watchlist.companies
                    for source in company.sources
                ),
            }
        )
    )
    return 0
