"""Command-line entry point. Only configuration validation is implemented in milestone 1.1."""

import argparse
import json
import sys
from pathlib import Path

from career_intelligence import __version__
from career_intelligence.config import ConfigurationError, load_configuration


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="career-intelligence", description="Validate career-monitoring configuration offline."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    config_parser = commands.add_parser("config", help="Inspect configuration")
    config_commands = config_parser.add_subparsers(dest="config_command", required=True)
    validate_parser = config_commands.add_parser(
        "validate", help="Validate settings and linked YAML"
    )
    validate_parser.add_argument("--config", type=Path, required=True, help="Path to settings YAML")
    args = parser.parse_args(argv)
    try:
        loaded = load_configuration(args.config)
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
