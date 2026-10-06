"""对外契约：`contracts/asset-v1.yaml` 必须和代码里的接口一致。"""

from __future__ import annotations

import yaml

from scripts.export_openapi import CONTRACT, build_spec, render


def test_contract_file_matches_the_code() -> None:
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
