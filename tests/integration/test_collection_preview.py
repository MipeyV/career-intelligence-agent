import asyncio
import json

import httpx

from career_intelligence.cli import main
from career_intelligence.config import load_configuration
from career_intelligence.preview import preview


def test_fixture_cli_previews_jobs_without_writing_state(config_files, repository_root, capsys):
    path, _, _ = config_files
    fixture = repository_root / "tests/fixtures/greenhouse/partial.json"
    assert main(["collect", "--config", str(path), "--fixture", str(fixture)]) == 3
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["mode"] == "fixture"
    assert result["snapshots"][0]["records"][0]["title"] == "Valid role"
    assert json.loads(output.err)["event"] == "collection_source_issues"
    assert not (path.parent.parent / "state").exists()


def test_live_requires_enabled_source(config_files, capsys):
    path, _, _ = config_files
    assert main(["collect", "--config", str(path), "--live"]) == 2
    assert "No matching enabled Greenhouse source" in capsys.readouterr().err


def test_live_collection_isolates_board_failures(config_files):
    path, docs, write = config_files
    for token in ("bad-board", "good-board"):
        docs["companies"]["companies"].append(
            {
                "key": token,
                "name": token,
                "category": "big-tech-ai",
                "sources": [
                    {
                        "key": token,
                        "board_token": token,
                        "enabled": True,
                        "provider": "greenhouse",
                        "official_careers_url": "https://example.com/careers",
                    }
                ],
            }
        )
    docs["settings"]["collection"] = {"min_request_interval_seconds": 0.001}
    write("companies", docs["companies"])
    write("settings", docs["settings"])

    def handler(request):
        if "/bad-board/" in request.url.path:
            return httpx.Response(404, text="Untrusted private detail")
        return httpx.Response(
            200, json={"jobs": [{"id": 5, "title": "Example"}], "meta": {"total": 1}}
        )

    config = load_configuration(path)
    result = asyncio.run(preview(config, transport=httpx.MockTransport(handler)))
    assert result.exit_code == 3
    assert [snapshot.status for snapshot in result.snapshots] == ["failed", "complete"]
    assert result.snapshots[1].records[0].title == "Example"
    assert "Untrusted private detail" not in result.model_dump_json()
