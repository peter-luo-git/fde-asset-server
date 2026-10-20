import importlib.util
from pathlib import Path
import sys


def runner():
    path = Path(__file__).resolve().parents[1] / "scripts/smoke.py"
    spec = importlib.util.spec_from_file_location("asset_fixed_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_test_database_is_blocked_before_starting_pytest(monkeypatch):
    module = runner()
    monkeypatch.delenv("FDE_ASSET_TEST_DATABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["smoke.py", "--python", sys.executable])
    assert module.main() == 2


def test_smoke_sanitizes_model_git_and_business_database_environment(monkeypatch, tmp_path):
    module = runner()
    report = tmp_path / "smoke.xml"
    db = "postgresql://controlled:controlled@127.0.0.1:9/postgres"
    monkeypatch.setenv("FDE_ASSET_TEST_DATABASE_URL", db)
    for key in [
        "FDE_ASSET_LLM_API_KEY",
        "FDE_DATABASE_URL",
        "DATABASE_URL",
        "GIT_INDEX_FILE",
        "OPENAI_API_KEY",
    ]:
        monkeypatch.setenv(key, "controlled-never-use")
    monkeypatch.setattr(
        sys, "argv", ["smoke.py", "--python", sys.executable, "--report", str(report)]
    )

    def invoke(command, cwd, env, seconds):
        assert 0 < seconds <= 60
        assert env["FDE_ASSET_TEST_DATABASE_URL"] == db
        assert env["FDE_ASSET_SKIP_ENV_FILE"] == "1"
        assert not any(
            key in env
            for key in [
                "FDE_ASSET_LLM_API_KEY",
                "FDE_DATABASE_URL",
                "DATABASE_URL",
                "GIT_INDEX_FILE",
                "OPENAI_API_KEY",
            ]
        )
        assert "--live-model" not in command and "--docker-integration" not in command
        report.write_text(
            '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"/></testsuites>'
        )
        return 0

    monkeypatch.setattr(module, "run_bounded", invoke)
    assert module.main() == 0


def test_empty_or_skipped_success_report_cannot_pass_smoke(monkeypatch, tmp_path):
    module = runner()
    report = tmp_path / "smoke.xml"
    monkeypatch.setenv(
        "FDE_ASSET_TEST_DATABASE_URL", "postgresql://controlled@127.0.0.1:9/postgres"
    )
    monkeypatch.setattr(
        sys, "argv", ["smoke.py", "--python", sys.executable, "--report", str(report)]
    )

    def invoke(*args):
        report.write_text(
            '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="1"/></testsuites>'
        )
        return 0

    monkeypatch.setattr(module, "run_bounded", invoke)
    assert module.main() == 1


def test_success_exit_without_a_new_report_cannot_reuse_an_old_pass(monkeypatch, tmp_path):
    module = runner()
    report = tmp_path / "smoke.xml"
    report.write_text(
        '<testsuites><testsuite tests="34" failures="0" errors="0" skipped="0"/></testsuites>'
    )
    monkeypatch.setenv(
        "FDE_ASSET_TEST_DATABASE_URL", "postgresql://controlled@127.0.0.1:9/postgres"
    )
    monkeypatch.setattr(
        sys, "argv", ["smoke.py", "--python", sys.executable, "--report", str(report)]
    )
    monkeypatch.setattr(module, "run_bounded", lambda *args: 0)
    assert module.main() == 1


def test_timeout_still_cleans_exact_parent_owned_database_manifest(monkeypatch, tmp_path):
    module = runner()
    monkeypatch.setenv(
        "FDE_ASSET_TEST_DATABASE_URL", "postgresql://controlled@127.0.0.1:9/postgres"
    )
    monkeypatch.setattr(sys, "argv", ["smoke.py", "--python", sys.executable])
    events = []

    def invoke(command, cwd, env, seconds):
        assert "FDE_ASSET_TEST_DATABASE_MANIFEST" in env
        manifest = Path(env["FDE_ASSET_TEST_DATABASE_MANIFEST"])
        prefix = env["FDE_ASSET_TEST_DATABASE_PREFIX"]
        assert manifest.is_dir() and len(prefix) == 45
        (manifest / (prefix + "a" * 12)).touch()
        assert 0 < seconds <= 45
        events.append("timeout")
        return 124

    def cleanup(python, env, deadline):
        manifest = Path(env["FDE_ASSET_TEST_DATABASE_MANIFEST"])
        assert len(list(manifest.iterdir())) == 1
        events.append("cleanup")
        return 0

    monkeypatch.setattr(module, "run_bounded", invoke)
    monkeypatch.setattr(module, "cleanup_owned_databases", cleanup, raising=False)
    assert module.main() == 124
    assert events == ["timeout", "cleanup"]


def test_cleanup_failure_prevents_green_report(monkeypatch, tmp_path):
    module = runner()
    report = tmp_path / "smoke.xml"
    monkeypatch.setenv(
        "FDE_ASSET_TEST_DATABASE_URL", "postgresql://controlled@127.0.0.1:9/postgres"
    )
    monkeypatch.setattr(
        sys, "argv", ["smoke.py", "--python", sys.executable, "--report", str(report)]
    )

    def invoke(*args):
        report.write_text('<testsuites><testsuite tests="1"/></testsuites>')
        return 0

    monkeypatch.setattr(module, "run_bounded", invoke)
    monkeypatch.setattr(module, "cleanup_owned_databases", lambda *args: 1)
    assert module.main() == 1


def test_unexpected_runner_error_still_calls_parent_cleanup(monkeypatch):
    import pytest

    module = runner()
    events = []
    monkeypatch.setenv(
        "FDE_ASSET_TEST_DATABASE_URL", "postgresql://controlled@127.0.0.1:9/postgres"
    )
    monkeypatch.setattr(sys, "argv", ["smoke.py", "--python", sys.executable])

    def invoke(*args):
        raise RuntimeError("controlled child start error")

    def cleanup(*args):
        events.append("cleanup")
        return 0

    monkeypatch.setattr(module, "run_bounded", invoke)
    monkeypatch.setattr(module, "cleanup_owned_databases", cleanup)
    with pytest.raises(RuntimeError, match="controlled"):
        module.main()
    assert events == ["cleanup"]
