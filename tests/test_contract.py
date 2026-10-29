"""对外契约：`contracts/asset-v1.yaml` 必须和代码里的接口一致。"""

from __future__ import annotations

import yaml

from scripts.export_openapi import CONTRACT, build_spec, render


def test_contract_file_matches_the_code(monkeypatch) -> None:
    # 生成接口描述要建应用、读配置，但不连数据库；没配库地址时给一个占位（2026-10-26 全量里因此报错）
    import os

    monkeypatch.setenv(
        "FDE_ASSET_DATABASE_URL",
        os.environ.get("FDE_ASSET_DATABASE_URL")
        or os.environ.get("FDE_ASSET_TEST_DATABASE_URL")
        # 和 conftest 的测试库默认地址一致（带密码，建应用时会连库）
        or "postgresql://postgres:test@127.0.0.1:55432/postgres",
    )
    assert CONTRACT.exists(), "先运行 python scripts/export_openapi.py 生成契约"
    assert CONTRACT.read_text(encoding="utf-8") == render(build_spec()), (
        "接口变了但契约没更新：运行 python scripts/export_openapi.py 后一起提交"
    )


def test_contract_covers_the_main_surfaces() -> None:
    spec = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    paths = spec["paths"]
    for path in (
        "/api/v1/assets",
        "/api/v1/assets/ask",
        "/api/v1/assets/{asset_id}/graph",
        "/api/v1/assets/{asset_id}/lifecycle",
        "/api/v1/harvest-candidates",
        "/api/v1/harvest-candidates/{candidate_id}",
        "/api/v1/reviews/{review_id}/decide",
        "/api/v1/recommend/compute",
        "/api/v1/workbench",
        "/mcp",
    ):
        assert path in paths, path
    assert "delete" in paths["/api/v1/harvest-candidates/{candidate_id}"]
    ask = {item["name"]: item for item in paths["/api/v1/assets/ask"]["get"]["parameters"]}
    assert ask["q"]["required"] is True and "kind" in ask
