"""Install the built wheel with locked runtime requirements, then validate it outside the repo."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120
    )
    if result.returncode:
        raise RuntimeError(f"Command failed: {args[0]}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extra", choices=["ollama"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    wheels = list((root / "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(
            "Expected one built wheel in dist; remove obsolete build artifacts first"
        )
    env = dict(os.environ, PYTHONUTF8="1")
    env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="career-wheel-") as temporary:
        directory = Path(temporary)
        venv = directory / "environment"
        run("uv", "venv", "--python", "3.12", str(venv), cwd=root, env=env)
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        # Install directly from the lock. A prior uv sync caches locked artifacts,
        # but need not cache registry metadata for a second pip-style resolution.
        install_env = dict(env, UV_PROJECT_ENVIRONMENT=str(venv))
        run(
            "uv",
            "sync",
            "--locked",
            "--no-dev",
            "--no-install-project",
            "--offline",
            *(["--extra", args.extra] if args.extra else []),
            cwd=root,
            env=install_env,
        )
        run(
            "uv",
            "pip",
            "install",
            "--offline",
            "--no-deps",
            "--python",
            str(python),
            str(wheels[0]),
            cwd=root,
            env=env,
        )
        run(str(python), "-m", "career_intelligence", "--help", cwd=directory, env=env)
        # Invoke the installed console entry point through Python so the smoke check
        # also works on Windows hosts that restrict generated .exe launchers in Temp.
        entry_point_script = (
            "import sys; from importlib.metadata import distribution; "
            "entry = next(ep for ep in distribution('career-intelligence').entry_points "
            "if ep.group == 'console_scripts' and ep.name == 'career-intelligence'); "
            "sys.exit(entry.load()())"
        )
        output = run(
            str(python),
            "-c",
            entry_point_script,
            "config",
            "validate",
            "--config",
            str(root / "conf/settings.yml"),
            cwd=directory,
            env=env,
        )
        assert json.loads(output)["event"] == "configuration_valid"
        extraction_help = run(
            str(python),
            "-c",
            entry_point_script,
            "jobs",
            "extract",
            "--help",
            cwd=directory,
            env=env,
        )
        assert "--response-fixture" in extraction_help
        assert "--ollama" in extraction_help
        eligibility_help = run(
            str(python),
            "-c",
            entry_point_script,
            "jobs",
            "eligibility",
            "--help",
            cwd=directory,
            env=env,
        )
        assert "--requirements" in eligibility_help and "--template" in eligibility_help
        matching_help = run(
            str(python),
            "-c",
            entry_point_script,
            "jobs",
            "match",
            "--help",
            cwd=directory,
            env=env,
        )
        assert "--policy" in matching_help and "--review" in matching_help
        matching_smoke = (
            "from pathlib import Path; "
            "from career_intelligence.config import load_configuration; "
            "from career_intelligence.matching import load_policy, matching_template, "
            "MatchingReview, assess_matching; "
            f"profile = load_configuration(Path({str(root / 'conf/settings.yml')!r})).profile; "
            f"policy = load_policy(Path({str(root / 'conf/matching.example.yml')!r})); "
            "revision = {'job_id': 'synthetic', 'number': 1, "
            "'fingerprint': 'a' * 64, 'payload': {}}; "
            "document = matching_template(revision, profile, policy); "
            "document.update(reviewed_by='synthetic-wheel-check', reviewed_on='2026-11-28'); "
            "result = assess_matching(revision, profile, policy, "
            "MatchingReview.model_validate(document)); "
            "assert result['status'] == 'insufficient_coverage'; "
            "assert result['overall_score'] is None and len(result['unassessed_dimensions']) == 9"
        )
        run(str(python), "-c", matching_smoke, cwd=directory, env=env)
        dependency_check = (
            "import importlib.util; from importlib.metadata import distribution; "
            "assert any('langchain-ollama' in item for item in "
            "distribution('career-intelligence').requires); "
            "assert (importlib.util.find_spec('langchain_ollama') is not None) "
            f"== {bool(args.extra)!r}"
        )
        run(str(python), "-c", dependency_check, cwd=directory, env=env)
    print("Built wheel installs and validates configuration outside the repository.")


if __name__ == "__main__":
    main()
