"""几个补上的小缺口：删草稿、资产下架与废弃、应用可以被引用、评审通知。"""

from __future__ import annotations

from sqlalchemy import select

from fde_asset.core.db import asset_leads
from fde_asset.modules.asset import lifecycle
from fde_asset.platform.refs.wiki import parse_refs
from tests.conftest import as_user


def _draft(client, **extra) -> dict:
    body = {
        "kind": "Case",
        "title": "一份草稿",
        "scope": "department",
        "department_code": "data-intel",
    }
    body.update(extra)
    response = client.post("/api/v1/harvest-candidates", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _asset(client, name: str) -> dict:
    items = client.get("/api/v1/assets", params={"limit": 200, "include_deprecated": True}).json()
    return next(item for item in items["items"] if item["name"] == name)


def _inbox(client) -> list[dict]:
    return client.get("/api/v1/notifications", params={"unread_only": False}).json()["items"]


# ---------- 删草稿 ----------


def test_author_deletes_own_draft(client) -> None:
    chen = as_user(client, "chen")
    draft = _draft(chen)
    gone = chen.delete(f"/api/v1/harvest-candidates/{draft['candidate_id']}")
    assert gone.status_code == 200 and gone.json()["deleted"] is True
    assert chen.get(f"/api/v1/harvest-candidates/{draft['candidate_id']}").status_code == 404
    drafts = chen.get("/api/v1/workbench").json()["drafts"]
    assert draft["candidate_id"] not in {item["candidate_id"] for item in drafts}


def test_deleting_a_lead_draft_puts_the_lead_back(client, context) -> None:
    chen = as_user(client, "chen")
    chen.post("/api/v1/workbench/leads/refresh")
    lead = chen.get("/api/v1/workbench").json()["leads"][0]
    draft = _draft(
        chen,
        kind=lead["suggested_kind"],
        title=lead["title"],
        scope="engagement",
        engagement_slug="policy-import",
        lead_id=lead["lead_id"],
    )
    open_ids = lambda: {item["lead_id"] for item in chen.get("/api/v1/workbench").json()["leads"]}  # noqa: E731
    assert lead["lead_id"] not in open_ids()

    result = chen.delete(f"/api/v1/harvest-candidates/{draft['candidate_id']}").json()
    assert result["lead_restored"] is True
    assert lead["lead_id"] in open_ids(), "草稿没了，线索等于没处理，要回到工作台"
    with context.engine.connect() as conn:
        status = conn.execute(
            select(asset_leads.c.status).where(asset_leads.c.lead_id == lead["lead_id"])
        ).scalar_one()
    assert status == "open"


def test_cannot_delete_others_or_submitted(client) -> None:
    chen = as_user(client, "chen")
    draft = _draft(chen)
    # 草稿没提交前是私有的，别人连看都看不到
    assert as_user(client, "li").delete(
        f"/api/v1/harvest-candidates/{draft['candidate_id']}"
    ).status_code in (403, 404)
    assert (
        as_user(client, "chen")
        .get(f"/api/v1/harvest-candidates/{draft['candidate_id']}")
        .status_code
        == 200
    )


# ---------- 下架、废弃、恢复 ----------


def test_rewrite_touches_only_the_lifecycle_lines() -> None:
    text = "kind: Case\nspec:\n  owner: user:chen\n  lifecycle: stable\n  industry: []\n"
    archived = lifecycle.rewrite(text, "archived", "")
    assert archived == text.replace("lifecycle: stable", "lifecycle: archived")

    deprecated = lifecycle.rewrite(text, "deprecated", "case/new-one")
    assert "  lifecycle: deprecated\n  replacedBy: case/new-one\n  industry: []\n" in deprecated

    restored = lifecycle.rewrite(deprecated, "stable", "")
    assert restored == text, "恢复之后和原文一字不差"


def test_owner_side_archives_and_restores(client) -> None:
    li = as_user(client, "li")  # 公司级资产由资产评审员管
    asset = _asset(li, "oracle-to-pg-sequence-gap")
    detail = li.get(f"/api/v1/assets/{asset['asset_id']}").json()
    assert detail["can_manage"] is True

    done = li.post(f"/api/v1/assets/{asset['asset_id']}/lifecycle", json={"lifecycle": "archived"})
    assert done.status_code == 200, done.text
    assert done.json()["changed"] is True and done.json()["commit"]

    names = {
        item["name"] for item in li.get("/api/v1/assets", params={"limit": 200}).json()["items"]
    }
    assert "oracle-to-pg-sequence-gap" not in names, "下架后不在目录里"
    found = li.get("/api/v1/assets/ask", params={"q": "序列跳号"}).json()["items"]
    assert "oracle-to-pg-sequence-gap" not in {item["name"] for item in found}, "也搜不到"
    still = li.get(f"/api/v1/assets/{asset['asset_id']}").json()
    assert still["lifecycle"] == "archived", "知道地址还能打开，才恢复得了"

    again = li.post(f"/api/v1/assets/{asset['asset_id']}/lifecycle", json={"lifecycle": "archived"})
    assert again.json()["changed"] is False

    li.post(f"/api/v1/assets/{asset['asset_id']}/lifecycle", json={"lifecycle": "stable"})
    names = {
        item["name"] for item in li.get("/api/v1/assets", params={"limit": 200}).json()["items"]
    }
    assert "oracle-to-pg-sequence-gap" in names


def test_deprecating_needs_a_replacement(client) -> None:
    li = as_user(client, "li")
    asset = _asset(li, "oracle-to-pg-sequence-gap")
    url = f"/api/v1/assets/{asset['asset_id']}/lifecycle"

    assert li.post(url, json={"lifecycle": "deprecated"}).status_code == 422
    assert (
        li.post(url, json={"lifecycle": "deprecated", "replaced_by": "中文不行"}).status_code == 422
    )
    own = li.post(
        url, json={"lifecycle": "deprecated", "replaced_by": "case/oracle-to-pg-sequence-gap"}
    )
    assert own.status_code == 422 and "自己" in own.json()["detail"]
    assert li.post(url, json={"lifecycle": "nonsense"}).status_code == 422

    ok = li.post(
        url, json={"lifecycle": "deprecated", "replaced_by": "[[impl/oracle-to-pg-cutover]]"}
    )
    assert ok.status_code == 200, ok.text
    after = li.get(f"/api/v1/assets/{asset['asset_id']}").json()
    assert after["lifecycle"] == "deprecated"
    assert after["replaced_by"] == "impl/oracle-to-pg-cutover"
    assert after["valid"] is True, "写了取代者，格式校验要过"


def test_only_owner_or_reviewer_may_change_lifecycle(client) -> None:
    chen = as_user(client, "chen")
    asset = _asset(chen, "oracle-to-pg-sequence-gap")
    assert chen.get(f"/api/v1/assets/{asset['asset_id']}").json()["can_manage"] is False
    refused = chen.post(
        f"/api/v1/assets/{asset['asset_id']}/lifecycle", json={"lifecycle": "archived"}
    )
    assert refused.status_code == 403

    # 看不到的资产：和不存在一样
    project = _asset(as_user(client, "chen"), "import-over-10k-rows-timeout")
    hidden = as_user(client, "zhao").post(
        f"/api/v1/assets/{project['asset_id']}/lifecycle", json={"lifecycle": "archived"}
    )
    assert hidden.status_code == 404

    # 规范是单个 Markdown，没有生命周期字段
    rule = _asset(as_user(client, "admin"), "00-security-redlines")
    admin = as_user(client, "admin")
    assert admin.get(f"/api/v1/assets/{rule['asset_id']}").json()["can_manage"] is False
    assert (
        admin.post(
            f"/api/v1/assets/{rule['asset_id']}/lifecycle", json={"lifecycle": "archived"}
        ).status_code
        == 422
    )


# ---------- 应用可以被引用 ----------


def test_application_refs_are_parsed_and_resolved(client) -> None:
    chen = as_user(client, "chen")
    app = next(
        item for item in chen.get("/api/v1/assets", params={"kind": "Application"}).json()["items"]
    )
    assert app["ref"] == f"[[application/{app['name']}]]"
    assert [ref.kind for ref in parse_refs(f"参考 {app['ref']} 的做法")] == ["Application"]

    resolved = chen.get("/api/v1/assets/resolve", params={"ref": app["ref"]}).json()
    assert resolved["status"] == "ok" and resolved["asset_id"] == app["asset_id"]


# ---------- 评审通知 ----------


def _ready_draft(client) -> str:
    """建一份能过校验的草稿：问题资产要填齐必填章节。"""
    draft = _draft(client, title="导入时偶发超时")
    rule = client.get("/api/v1/kinds").json()
    sections = next(item for item in rule["items"] if item["kind"] == "Case")["required_sections"]
    body = "# 导入时偶发超时\n\n" + "\n\n".join(f"## {name}\n这一节写清楚了。" for name in sections)
    patched = client.patch(
        f"/api/v1/harvest-candidates/{draft['candidate_id']}",
        json={
            "files": {"README.md": body},
            "meta": {
                "summary": "批次过大导致超时，分批提交",
                "suitable": "批量导入",
                "notSuitable": "实时写入",
            },
        },
    )
    assert patched.status_code == 200, patched.text
    return draft["candidate_id"]


def test_reviewers_and_author_are_notified(client) -> None:
    chen = as_user(client, "chen")
    candidate_id = _ready_draft(chen)
    submitted = chen.post(f"/api/v1/harvest-candidates/{candidate_id}/submit", json={})
    assert submitted.status_code == 200, submitted.text
    review_id = submitted.json()["review_id"]

    # 部门级草稿：本部门的资产评审员小李和管理员该收到；提交人自己和无关的人不该收到
    for user, expected in (("li", True), ("admin", True), ("chen", False), ("zhao", False)):
        mine = [n for n in _inbox(as_user(client, user)) if n["kind"] == "review"]
        assert bool(mine) is expected, user
        if expected:
            assert mine[0]["title"] == "有草稿等你评审：导入时偶发超时"
            assert mine[0]["asset_id"] == candidate_id, "带着草稿编号，页面才能跳过去"

    rejected = as_user(client, "li").post(
        f"/api/v1/reviews/{review_id}/decide", json={"approve": False, "note": "根因没写清楚"}
    )
    assert rejected.status_code == 200, rejected.text
    to_author = [n for n in _inbox(as_user(client, "chen")) if n["kind"] == "review"]
    assert [n["title"] for n in to_author] == ["被打回：导入时偶发超时"]
    assert to_author[0]["body"] == "根因没写清楚"
    assert "小李" in to_author[0]["reason"]

    # 改完再交、通过：提交人再收到一条已入库
    chen = as_user(client, "chen")
    review_id = chen.post(f"/api/v1/harvest-candidates/{candidate_id}/submit", json={}).json()[
        "review_id"
    ]
    as_user(client, "li").post(f"/api/v1/reviews/{review_id}/decide", json={"approve": True})
    titles = [n["title"] for n in _inbox(as_user(client, "chen")) if n["kind"] == "review"]
    assert sorted(titles) == ["已入库：导入时偶发超时", "被打回：导入时偶发超时"]
