#!/usr/bin/env python3
"""Asset固定冒烟，硬上限60秒；只使用明确提供的测试PG服务器。"""

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
BUDGET_SECONDS = 60
TESTS = (
    "tests/test_health.py",
    "tests/test_contract.py",
    "tests/asset/test_manifest.py",
    "tests/asset/test_rerank_http_contract.py",
    "tests/platform/test_scan_rules.py",
    "tests/platform/test_asset_permissions.py::test_engagement_asset_only_for_members",
    "tests/platform/test_asset_permissions.py::test_missing_and_forbidden_are_indistinguishable",
    "tests/platform/test_agent_bundle_compat.py::test_http_bundle_service_auth_and_project_visibility_are_distinct",
)


def run_bounded(command, cwd, env, seconds):
    process = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
    try:
        return process.wait(timeout=seconds)
    except BaseException as error:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait(timeout=2)
        if isinstance(error, subprocess.TimeoutExpired):
            print("Asset冒烟超过60秒，测试已停止。", file=sys.stderr)
            return 124
        raise


def cleanup_owned_databases(python, env, deadline):
    manifest = Path(env["FDE_ASSET_TEST_DATABASE_MANIFEST"])
    if not any(manifest.iterdir()):
        return 0
    seconds = min(8, max(0.001, deadline - time.monotonic() - 2))
    try:
        return run_bounded([python, str(ROOT / "scripts/smoke_database.py")], ROOT, env, seconds)
    except OSError:
        print("Asset owned database cleanup could not start.", file=sys.stderr)
        return 1


def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="Explicit locked Asset test interpreter")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    database = os.environ.get("FDE_ASSET_TEST_DATABASE_URL", "")
    if not database.startswith(("postgresql://", "postgresql+psycopg://")):
        print(
            "BLOCKED: provide FDE_ASSET_TEST_DATABASE_URL for an owned test PG server.",
            file=sys.stderr,
        )
        return 2
    if any(not (ROOT / test.split("::")[0]).is_file() for test in TESTS):
        print("Asset固定冒烟文件缺失。", file=sys.stderr)
        return 2
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("FDE_", "GIT_", "OPENAI_", "ANTHROPIC_", "AZURE_OPENAI_"))
        and key not in {"DATABASE_URL", "PYTEST_ADDOPTS", "PYTHONPATH", "PYTEST_PLUGINS"}
    }
    env.update(
        FDE_ASSET_TEST_DATABASE_URL=database,
        FDE_ASSET_SKIP_ENV_FILE="1",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(ROOT / "src"),
    )
    with tempfile.TemporaryDirectory(prefix="fde-asset-smoke-") as folder:
        manifest = Path(folder) / "databases"
        manifest.mkdir(mode=0o700)
        env.update(
            FDE_ASSET_TEST_DATABASE_MANIFEST=str(manifest),
            FDE_ASSET_TEST_DATABASE_PREFIX="asset_smoke_" + uuid.uuid4().hex + "_",
        )
        report = args.report.resolve() if args.report else Path(folder) / "results.xml"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.unlink(missing_ok=True)
        command = [
            args.python,
            "-m",
            "pytest",
            "-q",
            "-x",
            "--tb=short",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(Path(folder) / "pytest"),
            "--junitxml",
            str(report),
            *TESTS,
        ]
        code = 1
        try:
            code = run_bounded(
                command, ROOT, env, max(0.001, BUDGET_SECONDS - 15 - (time.monotonic() - started))
            )
        finally:
            cleanup_code = cleanup_owned_databases(args.python, env, started + BUDGET_SECONDS - 3)
            if cleanup_code:
                print("Asset owned database cleanup failed; smoke cannot pass.", file=sys.stderr)
                code = code or cleanup_code
        if code == 0:
            try:
                suites = list(ElementTree.parse(report).getroot().iter("testsuite"))
                if (
                    not suites
                    or sum(int(s.get("tests", "0")) for s in suites) == 0
                    or any(
                        int(s.get(key, "0"))
                        for s in suites
                        for key in ("failures", "errors", "skipped")
                    )
                ):
                    code = 1
            except (OSError, ValueError, ElementTree.ParseError):
                code = 1
    print(f"Asset冒烟：退出码{code}，耗时{time.monotonic() - started:.2f}秒（上限60秒）")
    return code if code >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
