"""Terminal tables for persisted jobs and change history."""

import unicodedata


def _text(value: object) -> str:
    if value is None:
        return "unknown"
    text = "".join(
        " " if unicodedata.category(char).startswith("C") else char for char in str(value)
    )
    return " ".join(text.split())


def table(headers: list[str], rows: list[list[object]]) -> str:
    cells = [[_text(cell) for cell in row] for row in rows]
    widths = [
        max([len(header)] + [len(row[index]) for row in cells])
        for index, header in enumerate(headers)
    ]
    lines = [" | ".join(header.ljust(width) for header, width in zip(headers, widths, strict=True))]
    lines.append("-+-".join("-" * width for width in widths))
    lines.extend(
        " | ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        for row in cells
    )
    return "\n".join(lines)


def job_table(records: list[dict]) -> str:
    return table(
        ["ID", "Company", "Title", "Location", "Availability", "Decision", "Reasons", "Apply"],
        [
            [
                row["id"],
                row["payload"]["company"],
                row["payload"]["title"],
                row["payload"]["location"],
                row["availability"],
                row["filter_decision"]["decision"] if row["filter_decision"] else "pending",
                ", ".join(reason["code"] for reason in row["filter_decision"]["reasons"])
                if row["filter_decision"]
                else "run jobs refilter",
                row["payload"]["official_application_url"],
            ]
            for row in records
        ],
    )


def change_table(records: list[dict]) -> str:
    return table(
        ["Event", "Job", "Kind", "Observed at", "Run", "Fields"],
        [
            [
                row["id"],
                row["job_id"],
                row["kind"],
                row["observed_at"],
                row["run_id"],
                ", ".join(row["fields"]),
            ]
            for row in records
        ],
    )
