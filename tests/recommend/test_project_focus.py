"""精确推荐：项目近期问题总结、给重排模型看的资产描述更完整。"""

from __future__ import annotations

from types import SimpleNamespace

from fde_asset.modules.asset.matching import rich_document
from fde_asset.modules.recommend import project_focus


class FakeLlm:
    usable = True

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, system: str, user: str):
        self.calls += 1
        assert "@" not in user, "人名不交给模型"
        return {
            "summary": "近期在排查 MySQL 慢查询、写生产变更手册",
            "keywords": ["MySQL", "慢查询"],
        }


class FakeDirectory:
    def __init__(self, questions, by_agent=None):
        self.questions = questions
        self.by_agent = by_agent or {}

    def recent_questions(self, slug: str, agent: str = ""):
        return self.by_agent.get(agent, []) if agent else self.questions


def test_focus_is_summarized_once_per_batch_of_questions() -> None:
    llm = FakeLlm()
    directory = FakeDirectory(
        ["@文档助手 怎么定位和分析解决mysql慢查询的问题", "@文档助手 写一份生产版本更新手册"]
    )
    first = project_focus.for_project(directory, "ops-dashboard", llm)
    again = project_focus.for_project(directory, "ops-dashboard", llm)
    assert first["summary"].startswith("近期在排查") and first["questions"] == 2
    assert again == first and llm.calls == 1, "同一批问题只总结一次"


def test_focus_falls_back_to_raw_questions_and_handles_missing_platform() -> None:
    result = project_focus.summarize(["@文档助手 告警规则怎么配置"], llm=None)
    assert result["summary"] == "告警规则怎么配置"
    assert project_focus.for_project(object(), "x")["summary"] == ""
    assert project_focus.summarize([], None)["questions"] == 0


def test_rich_document_includes_applicability_conclusion_and_attachment_summaries() -> None:
    row = SimpleNamespace(
        kind="Solution",
        title="发电行业方案",
        summary="面向电力行业",
        applicability_json='{"suitable": "五大六小发电集团", "notSuitable": "电网"}',
        conclusion_md="先做信创云，再上数据智能平台",
        tags_json='["信创"]',
        industry_json='["能源"]',
        attachments_json='[{"name": "汇报.pdf", "summary": "介绍专属云和案例"}]',
    )
    doc = rich_document(row)
    for part in (
        "方案：发电行业方案",
        "适用：五大六小发电集团",
        "不适用：电网",
        "结论：先做信创云",
        "标签：信创",
        "行业：能源",
        "汇报.pdf：介绍专属云",
    ):
        assert part in doc


def test_agent_focus_only_uses_questions_to_that_agent() -> None:
    directory = FakeDirectory(
        ["@文档助手 写手册", "@开发助手 改接口"], {"pa-dev": ["@开发助手 接口超时怎么排查"]}
    )

    result = project_focus.for_project(directory, "ops-dashboard", None, agent="pa-dev")
    assert result["summary"] == "接口超时怎么排查" and result["agent"] == "pa-dev"
    assert (
        project_focus.for_project(directory, "ops-dashboard", None, agent="pa-none")["questions"]
        == 0
    )


def test_accurate_recommendation_does_not_disclose_other_projects_questions(client) -> None:
    context = client.app.state.context
    data = context.directory._load()
    data["recent_questions"] = {"policy-import": ["项目里尚未公开的交付问题"]}
    context.directory.write(data)
    client.headers.update({"X-FDE-User": "zhao"})
    response = client.post(
        "/api/v1/recommend/compute",
        json={
            "target_type": "engagement",
            "target_id": "policy-import",
            "refresh": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["focus"] == {"summary": "", "keywords": [], "questions": 0}


def test_accurate_recommendation_keeps_focus_for_a_current_project_member(client) -> None:
    context = client.app.state.context
    data = context.directory._load()
    data["recent_questions"] = {"policy-import": ["保单批次导入超时怎么排查"]}
    context.directory.write(data)
    client.headers.update({"X-FDE-User": "chen"})
    response = client.post(
        "/api/v1/recommend/compute",
        json={
            "target_type": "engagement",
            "target_id": "policy-import",
            "refresh": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["focus"]["summary"] == "保单批次导入超时怎么排查"
