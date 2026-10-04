"""按问题检索：一句话按语义找资产，不要求字面重合；权限照旧在 SQL 里下推。"""

from __future__ import annotations

from fde_asset.api import routes_assets
from fde_asset.modules.asset import ask
from fde_asset.modules.recommend import service as recommend
from fde_asset.platform.llm.reranker import Scored
from tests.conftest import as_user

#: 和任何资产都没有字面重合的一句话
QUESTION = "换库之后编号不连续"


class MeaningReranker:
    """重排模型替身：文档里出现 `marker` 就当作语义相关，给高分，其余给低分。"""

    usable = True

    def __init__(self, marker: str, *, hit: float = 0.8, miss: float = 0.01) -> None:
        self.marker = marker
        self.hit = hit
        self.miss = miss
        self.documents: list[str] = []

    def rank(self, query: str, documents: list[str], top_n: int) -> list[Scored]:
        self.documents = documents
        scored = [
            Scored(index=i, score=self.hit if self.marker in text else self.miss)
            for i, text in enumerate(documents)
        ]
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_n]


class BrokenReranker:
    usable = True

    def rank(self, query: str, documents: list[str], top_n: int) -> list[Scored]:
        raise RuntimeError("超时")


def _principal(context, user: str):
    return context.directory.resolve(user)


def test_finds_asset_without_any_literal_overlap(indexed, client) -> None:
    literal = as_user(client, "chen").get("/api/v1/assets", params={"q": QUESTION}).json()
    assert literal["total"] == 0, "目录的关键词筛选对这句话无能为力"

    result = ask.ask(
        indexed.engine, _principal(indexed, "chen"), QUESTION, reranker=MeaningReranker("序列跳号")
    )
    assert result["mode"] == "semantic"
    assert [item["name"] for item in result["items"]] == ["oracle-to-pg-sequence-gap"]
    assert result["items"][0]["relevance"] == 0.8
    assert "content_text" not in result["items"][0], "列表不带全文"


def test_everything_below_threshold_returns_nothing(indexed) -> None:
    result = ask.ask(
        indexed.engine,
        _principal(indexed, "chen"),
        "怎么做蛋炒饭",
        reranker=MeaningReranker("蛋炒饭"),
    )
    assert result["mode"] == "semantic"
    assert result["items"] == [], "不相关就不出，不拿低分的凑数"
    assert result["scanned"] > 0


def test_model_only_sees_what_the_asker_may_see(indexed) -> None:
    chen = MeaningReranker("导入超过 1 万行")
    zhao = MeaningReranker("导入超过 1 万行")
    for_chen = ask.ask(indexed.engine, _principal(indexed, "chen"), "导入很慢", reranker=chen)
    for_zhao = ask.ask(indexed.engine, _principal(indexed, "zhao"), "导入很慢", reranker=zhao)

    assert [item["name"] for item in for_chen["items"]] == ["import-over-10k-rows-timeout"]
    assert for_zhao["items"] == []
    assert not any("导入超过 1 万行" in text for text in zhao.documents), "项目级资产不该送去打分"
    assert for_zhao["scanned"] < for_chen["scanned"]


def test_invalid_and_deprecated_assets_are_not_scanned(indexed) -> None:
    reranker = MeaningReranker("旧版上线手册")
    result = ask.ask(indexed.engine, _principal(indexed, "admin"), "上线", reranker=reranker)
    assert result["items"] == []
    assert not any("旧版上线手册" in text for text in reranker.documents)


def test_kind_filter_narrows_the_scan(indexed) -> None:
    reranker = MeaningReranker("保单")
    result = ask.ask(
        indexed.engine, _principal(indexed, "chen"), "保单怎么导", reranker=reranker, kind="Skill"
    )
    assert result["items"], "应当命中技能"
    assert {item["kind"] for item in result["items"]} == {"Skill"}


def test_without_model_falls_back_to_keywords(indexed) -> None:
    result = ask.ask(indexed.engine, _principal(indexed, "chen"), "导入超过 1 万行报错怎么办")
    assert result["mode"] == "keyword"
    assert result["items"][0]["name"] == "import-over-10k-rows-timeout"


def test_model_failure_falls_back_to_keywords(indexed) -> None:
    result = ask.ask(
        indexed.engine,
        _principal(indexed, "chen"),
        "序列跳号",
        reranker=BrokenReranker(),
    )
    assert result["mode"] == "keyword"
    assert result["items"][0]["name"] == "oracle-to-pg-sequence-gap"


def test_endpoint_is_not_shadowed_by_asset_id_route(client, monkeypatch) -> None:
    monkeypatch.setattr(routes_assets, "build_reranker", lambda: MeaningReranker("序列跳号"))
    chen = as_user(client, "chen")
    body = chen.get("/api/v1/assets/ask", params={"q": QUESTION}).json()
    assert body["mode"] == "semantic"
    assert body["items"][0]["name"] == "oracle-to-pg-sequence-gap"
    assert chen.get("/api/v1/assets/ask", params={"q": "  "}).status_code == 422


def test_threshold_follows_the_system_setting(client, monkeypatch) -> None:
    monkeypatch.setattr(
        routes_assets, "build_reranker", lambda: MeaningReranker("序列跳号", hit=0.3)
    )
    admin = as_user(client, "admin")
    assert admin.get("/api/v1/assets/ask", params={"q": QUESTION}).json()["total"] == 1
    saved = admin.put("/api/v1/settings", json={"values": {"search_min_relevance": 50}})
    assert saved.status_code == 200, saved.text
    assert admin.get("/api/v1/assets/ask", params={"q": QUESTION}).json()["total"] == 0


def test_recommendation_no_longer_needs_literal_hits(indexed) -> None:
    """有重排模型时，推荐的候选是可见范围内的全部技能和知识，不先按字面筛。"""
    principal = _principal(indexed, "wang")
    target = recommend.target_from_directory(indexed.directory, "engagement", "policy-import")
    target.title, target.description, target.industry, target.stage = QUESTION, "", "", ""

    keyword_only, mode = recommend.compute(indexed.engine, principal, target)
    assert mode == "keyword"
    assert "oracle-to-pg-sequence-gap" not in {item["name"] for item in keyword_only}

    reranker = MeaningReranker("序列跳号")
    items, mode = recommend.compute(
        indexed.engine, principal, target, reranker=reranker, min_score=0.1
    )
    assert mode == "reranked"
    picked = [item for item in items if item["group"] not in ("rules", "sops")]
    assert [item["name"] for item in picked] == ["oracle-to-pg-sequence-gap"], (
        "只留过了门槛的，不拿不相关的凑数"
    )
    sops = [item for item in items if item["group"] == "sops"]
    assert len(sops) == 1 and sops[0]["reasons"][0] == "通用流程兜底", "流程始终留一条兜底"
    assert all(item["kind"] == "Rule" for item in items if item["group"] == "rules")


def test_recommendation_falls_back_to_keywords_when_model_breaks(indexed) -> None:
    principal = _principal(indexed, "wang")
    target = recommend.target_from_directory(indexed.directory, "engagement", "policy-import")
    baseline, _ = recommend.compute(indexed.engine, principal, target)
    items, mode = recommend.compute(
        indexed.engine, principal, target, reranker=BrokenReranker(), min_score=0.1
    )
    assert mode == "keyword"
    assert [item["asset_id"] for item in items] == [item["asset_id"] for item in baseline]
