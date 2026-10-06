"""推荐理由按需生成：算推荐时不调生成模型，人点了才写；只解释看得见的资产。"""

from __future__ import annotations

from fde_asset.api import routes_recommend
from fde_asset.modules.asset import rerank
from fde_asset.platform.llm.client import NullLlmClient
from tests.conftest import as_user

TARGET = {"target_type": "engagement", "target_id": "policy-import"}


class FakeLlm:
    usable = True

    def __init__(self, extra: list[dict] | None = None, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.extra = extra or []
        self.fail = fail

    def complete_json(self, system: str, user: str) -> dict:
        import json

        self.calls.append((system, user))
        if self.fail:
            raise RuntimeError("超时")
        asked = json.loads(user)["候选资产"]
        rows = [{"asset_id": item["asset_id"], "reason": f"因为{item['title']}"} for item in asked]
        return {"reasons": rows + self.extra}


def _items(client, user: str = "wang") -> list[dict]:
    response = as_user(client, user).post("/api/v1/recommend/compute", json=TARGET)
    return response.json()["items"]


def test_compute_never_calls_the_generation_model(client, monkeypatch) -> None:
    llm = FakeLlm()
    monkeypatch.setattr(routes_recommend, "build_client", lambda *args: llm)
    assert _items(client)
    assert llm.calls == [], "理由不随推荐一起算"


def test_explain_writes_one_reason_per_asked_asset(client, monkeypatch) -> None:
    llm = FakeLlm(extra=[{"asset_id": "made-up", "reason": "模型编的"}])
    monkeypatch.setattr(routes_recommend, "build_client", lambda *args: llm)
    items = _items(client)[:2]
    ids = [item["asset_id"] for item in items]

    result = (
        as_user(client, "wang")
        .post("/api/v1/recommend/explain", json={**TARGET, "asset_ids": ids})
        .json()
    )
    assert result["available"] is True and result["cached"] is False
    assert result["reasons"] == {item["asset_id"]: f"因为{item['title']}" for item in items}
    assert "made-up" not in result["reasons"], "模型编出来的资产不要"
    assert llm.calls[0][0] == rerank.EXPLAIN_SYSTEM
    assert "华安人寿保单批量导入" in llm.calls[0][1], "要把目标的上下文交给模型"

    again = (
        as_user(client, "wang")
        .post("/api/v1/recommend/explain", json={**TARGET, "asset_ids": ids})
        .json()
    )
    assert again["cached"] is True and len(llm.calls) == 1, "同一问不重复调模型"


def test_explain_only_covers_what_the_asker_may_see(client, monkeypatch) -> None:
    llm = FakeLlm()
    monkeypatch.setattr(routes_recommend, "build_client", lambda *args: llm)
    project_only = next(item for item in _items(client, "wang") if item["scope"] == "engagement")

    # 管理员为这个项目找资产是允许的；换成看不到项目资产的身份，模型就不该收到它
    result = (
        as_user(client, "admin")
        .post(
            "/api/v1/recommend/explain",
            json={**TARGET, "asset_ids": [project_only["asset_id"], "nope"]},
        )
        .json()
    )
    assert set(result["reasons"]) <= {project_only["asset_id"]}
    assert "nope" not in llm.calls[-1][1]


def test_explain_degrades_honestly(client, monkeypatch) -> None:
    ids = [_items(client)[0]["asset_id"]]
    wang = as_user(client, "wang")

    monkeypatch.setattr(routes_recommend, "build_client", lambda *args: NullLlmClient())
    assert wang.post("/api/v1/recommend/explain", json={**TARGET, "asset_ids": ids}).json() == {
        "available": False,
        "reasons": {},
    }

    monkeypatch.setattr(routes_recommend, "build_client", lambda *args: FakeLlm(fail=True))
    failed = wang.post(
        "/api/v1/recommend/explain", json={**TARGET, "asset_ids": ids, "refresh": True}
    )
    assert failed.json() == {"available": False, "reasons": {}}

    assert (
        wang.post("/api/v1/recommend/explain", json={**TARGET, "asset_ids": []}).status_code == 422
    )


def test_explain_is_capped() -> None:
    llm = FakeLlm()
    candidates = [
        {"asset_id": f"a{i}", "kind": "Case", "title": f"资产{i}", "summary": ""} for i in range(9)
    ]
    reasons = rerank.explain(candidates, {"标题": "x"}, llm)
    assert reasons is not None and len(reasons) == rerank.EXPLAIN_LIMIT


def test_rerank_is_on_by_default(client) -> None:
    values = as_user(client, "admin").get("/api/v1/settings").json()
    flag = next(item for item in values["items"] if item["key"] == "rerank_enabled")
    assert flag["value"] is True


def test_explain_gives_the_generation_model_enough_time(client, monkeypatch) -> None:
    """实测生成模型写两句话要三十秒上下，默认的 30 秒超时刚好卡在边上。"""
    from fde_asset.platform.llm.client import LlmConfig

    llm = FakeLlm()
    llm.config = LlmConfig(base_url="http://x", api_key="k", model="m", timeout=30.0)
    monkeypatch.setattr(routes_recommend, "build_client", lambda *args: llm)
    ids = [_items(client)[0]["asset_id"]]
    as_user(client, "wang").post("/api/v1/recommend/explain", json={**TARGET, "asset_ids": ids})
    assert llm.config.timeout == routes_recommend.EXPLAIN_TIMEOUT_SECONDS
