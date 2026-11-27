import importlib
import json
from datetime import UTC, datetime, timedelta

import httpx

from career_intelligence import cli
from career_intelligence.greenhouse import GreenhouseConnector


def test_config_http_normalization_lifecycle_filters_cli_and_restore(
    config_files,
    tmp_path,
    monkeypatch,
    capsys,
):
    settings, docs, write = config_files
    docs["profile"]["preferences"]["locations"] = ["Example City"]
    company = docs["companies"]["companies"][0]
    company.pop("disabled_reason")
    company["sources"] = [
        {
            "key": "example",
            "provider": "greenhouse",
            "board_token": "example",
            "enabled": True,
            "official_careers_url": "https://example.com/careers",
        }
    ]
    write("profile", docs["profile"])
    write("companies", docs["companies"])
    state = {"hour": 0, "ids": [1, 2, 3], "status": 200, "content": "<p>Initial body.</p>"}
    epoch = datetime(2026, 11, 27, tzinfo=UTC)

    def handler(request):
        assert request.url.host == "boards-api.greenhouse.io"
        if state["status"] != 200:
            return httpx.Response(state["status"])
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": i,
                        "internal_job_id": i,
                        "title": "Machine Learning Engineer",
                        "location": {"name": "Example City"},
                        "content": state["content"] if i == 1 else "<p>Another role.</p>",
                        "absolute_url": f"https://example.com/jobs/{i}",
                    }
                    for i in state["ids"]
                ],
                "meta": {"total": len(state["ids"])},
            },
        )

    module = importlib.import_module("career_intelligence.preview")
    real_preview = module.preview

    def adapter(limits, **kwargs):
        return GreenhouseConnector(
            limits,
            **kwargs,
            now=lambda: epoch + timedelta(hours=state["hour"]),
        )

    async def offline_preview(configuration, **kwargs):
        return await real_preview(configuration, **kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(module, "GreenhouseConnector", adapter)
    monkeypatch.setattr(cli, "preview", offline_preview)
    sequence = [
        {"hour": 0},
        {"hour": 1, "content": "<p>Changed body.</p>"},
        {"hour": 2, "ids": [1, 3]},
        {"hour": 26},
        {"hour": 27, "status": 404},
        {"hour": 28, "status": 200, "ids": [1, 2, 3]},
        {"hour": 28},
    ]
    for step in sequence:
        state.update(step)
        expected_exit = 1 if state["status"] == 404 else 0
        assert (
            cli.main(["collect", "--config", str(settings), "--live", "--persist"]) == expected_exit
        )
        output = capsys.readouterr()
        assert json.loads(output.out)["schema_version"] == 1
        summary = json.loads(output.err.splitlines()[0])
        if step is sequence[-1]:
            assert summary["change_count"] == 0

    assert cli.main(["changes", "list", "--config", str(settings)]) == 0
    events = json.loads(capsys.readouterr().out)["changes"]
    kinds = [event["kind"] for event in events]
    assert kinds.count("new") == 3
    assert kinds.count("updated") == 1
    assert (
        kinds.count("missing") == kinds.count("removed_from_source") == kinds.count("reopened") == 1
    )
    assert cli.main(["jobs", "list", "--config", str(settings), "--decision", "keep"]) == 0
    rows = json.loads(capsys.readouterr().out)["jobs"]
    assert len(rows) == 3
    assert all(row["availability"] == "listed" for row in rows)
    assert rows[0]["filter_decision"]["decision"] == "keep"

    backup = tmp_path / "backup.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    assert cli.main(["db", "backup", "--config", str(settings), "--output", str(backup)]) == 0
    capsys.readouterr()
    assert cli.main(["db", "restore", "--backup", str(backup), "--destination", str(restored)]) == 0
    capsys.readouterr()
    docs["settings"]["database_path"] = str(restored)
    write("settings", docs["settings"])
    assert cli.main(["jobs", "list", "--config", str(settings), "--decision", "keep"]) == 0
    assert json.loads(capsys.readouterr().out)["jobs"] == rows
