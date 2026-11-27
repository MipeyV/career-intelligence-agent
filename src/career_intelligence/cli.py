"""Validate settings or preview collection without persisting state."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from career_intelligence import __version__
from career_intelligence.config import ConfigurationError, load_configuration
from career_intelligence.preview import preview


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
    args = parser.parse_args(argv)
    if args.command == "collect" and args.source is not None and not args.live:
        collect_parser.error("--source requires --live")
    try:
        loaded = load_configuration(args.config)
        if args.command == "collect":
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
