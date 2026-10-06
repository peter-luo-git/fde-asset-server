"""订阅与通知补上的几处：订阅选项、新资产入库通知、通知发给现任负责人、版本记录不重复。"""

from __future__ import annotations

from sqlalchemy import select

from fde_asset.core.db import asset_versions, assets, target_assets
from fde_asset.modules.asset.indexer import index_all
from fde_asset.modules.notify import service as notify
from tests.conftest import as_user

README = (
    "# 导入时偶发超时\n\n## 结论\n批次过大，分批提交。\n\n## 现象\n偶发超时。\n\n"
    "## 影响\n导入中断。\n\n## 根因\n单事务过大。\n\n## 解决方法\n按 2000 行分批。\n\n"
    "## 如何避免\n导入前估算行数。\n"
)


def _inbox(client, kind: str) -> list[dict]:
    items = client.get("/api/v1/notifications", params={"unread_only": False}).json()["items"]
    return [item for item in items if item["kind"] == kind]


def test_options_come_from_what_the_user_can_see(client) -> None:
    chen = as_user(client, "chen").get("/api/v1/subscriptions/options").json()
    assert set(chen) == {"kind", "industry", "tag", "owner", "asset"}
    assert {"Case", "Skill", "Rule"} <= {item["value"] for item in chen["kind"]}
    assert "insurance" in {item["value"] for item in chen["industry"]}
    owners = {item["value"]: item["label"] for item in chen["owner"]}
    assert owners["department:data-intel"] == "data-intel 部门"
    titles = {item["label"] for item in chen["asset"]}
    assert "导入超过 1 万行报错" in titles

    # 小赵看不到项目级资产：选项里也不该出现，否则等于泄露标题
    zhao = as_user(client, "zhao").get("/api/v1/subscriptions/options").json()
    assert "导入超过 1 万行报错" not in {item["label"] for item in zhao["asset"]}
    assert len(zhao["asset"]) < len(chen["asset"])


def test_subscribers_hear_about_a_new_asset(client) -> None:
    """订阅了「问题」类型的人，在新的问题资产入库时收到通知。"""
    admin = as_user(client, "admin")
    assert (
        admin.post(
            "/api/v1/subscriptions", json={"filter_type": "kind", "filter_value": "Case"}
        ).status_code
        == 200
    )
    assert _inbox(as_user(client, "admin"), "asset_new") == [], "订阅之前就在库里的不补发"

    chen = as_user(client, "chen")
    draft = chen.post(
        "/api/v1/harvest-candidates",
        json={
            "kind": "Case",
            "title": "导入时偶发超时",
            "scope": "department",
            "department_code": "data-intel",
        },
    ).json()
    chen.patch(
        f"/api/v1/harvest-candidates/{draft['candidate_id']}",
        json={
            "files": {"README.md": README},
            "meta": {
                "summary": "批次过大，分批提交",
                "suitable": "批量导入",
                "notSuitable": "实时写入",
            },
        },
    )
    submitted = chen.post(f"/api/v1/harvest-candidates/{draft['candidate_id']}/submit", json={})
    assert submitted.status_code == 200, submitted.text
    done = as_user(client, "li").post(
        f"/api/v1/reviews/{submitted.json()['review_id']}/decide", json={"approve": True}
    )
    assert done.status_code == 200, done.text

    fresh = _inbox(as_user(client, "admin"), "asset_new")
    assert [item["title"] for item in fresh] == ["新资产：导入时偶发超时"]
    assert fresh[0]["reason"] == "你订阅了kind=Case"
    # 没订阅的人不收
    assert _inbox(as_user(client, "zhao"), "asset_new") == []


def test_change_notice_goes_to_the_current_owner(indexed) -> None:
    """项目换了负责人，或者关联是别人做的：通知要发给现在的负责人。"""
    engine = indexed.engine
    with engine.begin() as conn:
        asset_id = conn.execute(
            select(assets.c.asset_id).where(assets.c.name == "insurance-policy-import")
        ).scalar_one()
        conn.execute(
            target_assets.insert().values(
                target_type="engagement",
                target_id="policy-import",
                asset_id=asset_id,
                source="recommendation",
                created_by="former-owner",
            )
        )

    with_owner = notify.announce_change(
        engine, asset_id, owner_of=lambda kind, target: "wang" if target == "policy-import" else ""
    )
    assert with_owner["delivered"] == 1
    wang = indexed.directory.resolve("wang")
    notices = notify.inbox(engine, wang, unread_only=False)
    assert notices and notices[0]["reason"] == "你的项目 policy-import 关联了这份资产"

    # 查不到负责人时退回当初做关联的人，不至于谁都不通知
    fallback = notify.announce_change(engine, asset_id, owner_of=lambda kind, target: "")
    assert fallback["delivered"] == 1
    broken = notify.announce_change(engine, asset_id, owner_of=lambda kind, target: 1 / 0)
    assert broken["delivered"] == 1, "名单查询出错不能让通知整个失败"


def test_versions_are_kept_by_content_not_by_commit(indexed) -> None:
    """同仓库里别的资产有了新提交，不该给每份资产都多存一份一模一样的版本。"""

    def counts() -> dict[str, int]:
        with indexed.engine.connect() as conn:
            rows = conn.execute(select(asset_versions.c.asset_id)).fetchall()
        result: dict[str, int] = {}
        for row in rows:
            result[row.asset_id] = result.get(row.asset_id, 0) + 1
        return result

    before = counts()
    assert before and set(before.values()) == {1}

    repo = next(item for item in indexed.repos() if item.name == "company-assets")
    path = "knowledge/cases/oracle-to-pg-sequence-gap/asset.yaml"
    text = indexed.repo_port.read_file(repo, path).decode("utf-8")
    changed = text.replace("序列缓存导致跳号", "改过的结论：序列缓存导致跳号")
    indexed.repo_port.commit_files(repo, "main", {path: changed.encode("utf-8")}, "改摘要")
    index_all(indexed.engine, indexed.repo_port, indexed.repos())

    after = counts()
    grown = {asset_id for asset_id, count in after.items() if count != before.get(asset_id)}
    with indexed.engine.connect() as conn:
        names = {
            row.name
            for row in conn.execute(select(assets.c.name).where(assets.c.asset_id.in_(grown)))
        }
    assert names == {"oracle-to-pg-sequence-gap"}, "只有内容真的变了的那一份多一条版本"
