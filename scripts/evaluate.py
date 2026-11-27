"""Run the versioned synthetic benchmark and print machine-readable measurements."""

import argparse
import json
from pathlib import Path

from career_intelligence.evaluation import evaluate_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/cases.json"))
    parser.add_argument("--output", type=Path, help="Write measured JSON results to this path")
    args = parser.parse_args()
    try:
        report = evaluate_dataset(args.dataset)
    except (OSError, ValueError):
        parser.error(
            "Cannot evaluate the dataset. Check the file path, size, schema and case fields"
        )
    rendered = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
