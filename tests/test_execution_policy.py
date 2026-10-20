"""Optional acceptance work must be requested, and requested failures stay red."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_rerank_collection_never_reads_local_credentials(monkeypatch):
    from fde_asset.platform.llm import client

    def forbidden(*args):
        raise AssertionError("collection read local credentials")

    monkeypatch.setattr(client, "load_env_file", forbidden)
    path = Path(__file__).parent / "asset/test_rerank.py"
    spec = importlib.util.spec_from_file_location("controlled_rerank_collection", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def test_requested_model_failure_is_not_a_skip(monkeypatch):
    from tests.asset import test_rerank

    monkeypatch.setattr(test_rerank, "build_client", lambda: SimpleNamespace(usable=True))
    monkeypatch.setattr(test_rerank.rerank, "rerank", lambda *a, **kw: ([], "keyword"))
    with pytest.raises(AssertionError, match="reranked"):
        test_rerank.test_real_model_reranks_and_explains(monkeypatch)


def test_requested_model_requires_explicit_configuration(monkeypatch):
    from tests.asset import test_rerank

    monkeypatch.setattr(test_rerank, "build_client", lambda: SimpleNamespace(usable=False))
    with pytest.raises(AssertionError, match="configuration"):
        test_rerank.test_real_model_reranks_and_explains(monkeypatch)


def test_default_configuration_drops_inherited_model_settings(monkeypatch):
    from tests import conftest

    monkeypatch.setenv("FDE_ASSET_LLM_API_KEY", "controlled-never-send")
    monkeypatch.setenv("FDE_ASSET_RERANK_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("FDE_ASSET_REASON_MODEL", "controlled-model")
    conftest.pytest_configure(
        SimpleNamespace(getoption=lambda name: False, addinivalue_line=lambda *args: None)
    )
    import os

    assert not any(
        key.startswith(("FDE_ASSET_LLM_", "FDE_ASSET_RERANK_")) or key == "FDE_ASSET_REASON_MODEL"
        for key in os.environ
    )


@pytest.mark.parametrize(
    "marker,option", [("live_model", "live_model"), ("docker", "docker_integration")]
)
def test_optional_integrations_require_their_own_flag(marker, option):
    from tests import conftest

    item = SimpleNamespace(
        get_closest_marker=lambda name: name == marker,
        config=SimpleNamespace(getoption=lambda name: False),
    )
    with pytest.raises(pytest.skip.Exception, match="explicit"):
        conftest.pytest_runtest_setup(item)
