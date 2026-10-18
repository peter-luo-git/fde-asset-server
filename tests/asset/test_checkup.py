"""资产体检：半年没更新、零复用、被反复拒绝、收到差评、引用失效、人员变动；定时体检与清理。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from fde_asset.core.db import (
    asset_audiences,
    asset_checkup_findings,
    asset_notifications,
    asset_relations,
    asset_usages,
    assets,
)
from fde_asset.modules.asset import checkup
from fde_asset.modules.asset import housekeeping
from fde_asset.platform.identity import Membership, Principal, PrincipalNotFound
from tests.conftest import as_user

CHEN = Principal("chen", department_code="data-intel")
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _my_asset(engine):
    with engine.connect() as conn:
        return conn.execute(select(assets).where(assets.c.owner_ref == "user:chen")).first()


def test_fresh_assets_are_not_flagged_as_stale(indexed) -> None:
    result = checkup.run(indexed.engine, CHEN, now=NOW)
    assert "stale" not in result["by_rule"], "刚建好的资产不该被判定为过期"


def test_half_year_without_update_is_flagged(indexed) -> None:
    row = _my_asset(indexed.engine)
    with indexed.engine.begin() as conn:
        conn.execute(
            update(assets)
            .where(assets.c.asset_id == row.asset_id)
            .values(updated_at=NOW - timedelta(days=200))
        )
    result = checkup.run(indexed.engine, CHEN, now=NOW)
    stale = [item for item in result["items"] if item["rule"] == "stale"]
    assert any(item["asset_id"] == row.asset_id for item in stale)
    assert "半年没动" in stale[0]["suggestion"]


def _usage_started(engine, when) -> None:
    """平台从这天开始上报使用情况（随便一份别的资产的一条使用记录）。"""
    with engine.begin() as conn:
        conn.execute(
            asset_usages.insert().values(
                asset_id="someone-else", event="loaded", session_id="s-old", created_at=when
            )
        )


def test_three_months_without_reuse_is_flagged(indexed) -> None:
    row = _my_asset(indexed.engine)
    with indexed.engine.begin() as conn:
        conn.execute(
            update(assets)
            .where(assets.c.asset_id == row.asset_id)
            .values(created_at=NOW - timedelta(days=120))
        )
    assert not any(
        item["rule"] == "unused" for item in checkup.run(indexed.engine, CHEN, now=NOW)["items"]
    ), "一条使用记录都没有：说明还没开始上报，不能当成零复用"
    _usage_started(indexed.engine, NOW - timedelta(days=200))
    result = checkup.run(indexed.engine, CHEN, now=NOW)
    assert any(
        item["rule"] == "unused" and item["asset_id"] == row.asset_id for item in result["items"]
    )


def test_zero_reuse_counts_from_when_usage_reporting_started(indexed) -> None:
    row = _my_asset(indexed.engine)
    with indexed.engine.begin() as conn:
        conn.execute(
            update(assets)
            .where(assets.c.asset_id == row.asset_id)
            .values(created_at=NOW - timedelta(days=400))
        )
    _usage_started(indexed.engine, NOW - timedelta(days=30))
    result = checkup.run(indexed.engine, CHEN, now=NOW)
    assert not any(item["rule"] == "unused" for item in result["items"]), (
        "老资产入库很久了，但平台才上报了一个月，不够三个月"
    )


def test_repeated_rejections_and_complaints_are_flagged(client) -> None:
    """被拒和差评都要进体检——它们是最直接的改进信号。"""
    chen = as_user(client, "chen")
    mine = chen.get("/api/v1/assets", params={"mine": "personal", "limit": 1}).json()["items"][0]
    chen.post(
        f"/api/v1/assets/{mine['asset_id']}/feedback",
        json={"verdict": "not_helpful", "note": "对不上"},
    )

    result = as_user(client, "chen").get("/api/v1/governance/checkup").json()
    assert any(
        item["rule"] == "criticized" and item["asset_id"] == mine["asset_id"]
        for item in result["items"]
    )


def test_checkup_only_covers_what_i_can_fix(indexed) -> None:
    """默认只看我负责的——别人的资产我改不动，列出来只是噪声。"""
    mine = checkup.run(indexed.engine, CHEN, now=NOW, owner_only=True)
    everything = checkup.run(indexed.engine, CHEN, now=NOW, owner_only=False)
    assert mine["checked"] < everything["checked"]


def test_each_finding_carries_a_suggestion(indexed) -> None:
    row = _my_asset(indexed.engine)
    with indexed.engine.begin() as conn:
        conn.execute(
            update(assets)
            .where(assets.c.asset_id == row.asset_id)
            .values(updated_at=NOW - timedelta(days=400), created_at=NOW - timedelta(days=400))
        )
    result = checkup.run(indexed.engine, CHEN, now=NOW)
    assert result["items"], "应当有发现"
    assert all(item["suggestion"] for item in result["items"]), "每条发现都要给出该怎么办"


class People:
    """平台人员名单的替身：只有给出的这些人在。"""

    def __init__(self, *people: Principal) -> None:
        self.people = {person.user_id: person for person in people}

    def users(self) -> list[str]:
        return sorted(self.people)

    def resolve(self, user_id: str) -> Principal:
        if user_id not in self.people:
            raise PrincipalNotFound(user_id)
        return self.people[user_id]


def _rules(result, asset_id):
    return {
        (item["rule"], item["subject"]) for item in result["items"] if item["asset_id"] == asset_id
    }


def test_broken_reference_is_flagged(indexed) -> None:
    row = _my_asset(indexed.engine)
    with indexed.engine.begin() as conn:
        conn.execute(
            asset_relations.insert().values(
                from_asset_id=row.asset_id,
                to_ref="case/never-landed",
                type="references",
                source="body",
            )
        )
    result = checkup.run(indexed.engine, CHEN, now=NOW)
    assert ("broken_ref", "case/never-landed") in _rules(result, row.asset_id)


def test_people_rules_owner_and_named_user_gone(indexed) -> None:
    row = _my_asset(indexed.engine)
    with indexed.engine.begin() as conn:
        conn.execute(
            asset_audiences.insert().values(asset_id=row.asset_id, audience="user", code="left-guy")
        )
    everyone = People(CHEN, Principal("admin", is_admin=True))
    rules = _rules(checkup.run(indexed.engine, CHEN, now=NOW, directory=everyone), row.asset_id)
    assert ("named_gone", "left-guy") in rules and not any(
        rule == "owner_gone" for rule, _ in rules
    )
    gone = _rules(
        checkup.run(indexed.engine, CHEN, now=NOW, directory=People(Principal("admin"))),
        row.asset_id,
    )
    assert ("owner_gone", "chen") in gone
    # 名单读不到：人员相关的规则不算
    assert not any(
        rule in checkup.PEOPLE_RULES
        for rule, _ in _rules(checkup.run(indexed.engine, CHEN, now=NOW), row.asset_id)
    )


def test_sweep_stores_findings_notifies_once_and_resolves(indexed) -> None:
    engine = indexed.engine
    row = _my_asset(engine)
    people = People(CHEN, Principal("rev", is_asset_reviewer=True))
    with engine.begin() as conn:
        conn.execute(
            update(assets)
            .where(assets.c.asset_id == row.asset_id)
            .values(updated_at=NOW - timedelta(days=200))
        )
    first = checkup.sweep(engine, people, now=NOW)
    assert first["new"] >= 1 and first["notified"] >= 1

    def notes():
        with engine.connect() as conn:
            return conn.execute(
                select(asset_notifications).where(asset_notifications.c.kind == "checkup")
            ).fetchall()

    to_chen = [note for note in notes() if note.user_id == "chen"]
    assert len(to_chen) == 1, "一个人一条通知，不逐条刷屏"
    assert row.title in to_chen[0].body
    again = checkup.sweep(engine, people, now=NOW + timedelta(days=1))
    assert again["new"] == 0 and len([n for n in notes() if n.user_id == "chen"]) == 1, (
        "老问题不重复通知"
    )
    # 页面上能看到是哪天发现的
    page = checkup.run(engine, CHEN, now=NOW, directory=people)
    stale = [
        item
        for item in page["items"]
        if item["rule"] == "stale" and item["asset_id"] == row.asset_id
    ]
    assert stale and stale[0]["first_seen_at"]
    # 更新了资产：问题消失，记成已解决
    with engine.begin() as conn:
        conn.execute(update(assets).where(assets.c.asset_id == row.asset_id).values(updated_at=NOW))
    later = checkup.sweep(engine, people, now=NOW + timedelta(days=2))
    assert later["resolved"] >= 1
    with engine.connect() as conn:
        stored = conn.execute(
            select(asset_checkup_findings).where(
                asset_checkup_findings.c.asset_id == row.asset_id,
                asset_checkup_findings.c.rule == "stale",
            )
        ).first()
    assert stored.resolved_at is not None


def test_named_user_who_moved_away_is_flagged_but_outsiders_are_not(indexed) -> None:
    """B5：点名的人原来在相关项目里、后来调走了，提醒负责人；一开始就是有意点名给外人的，不提醒。"""
    engine = indexed.engine
    row = _my_asset(engine)
    with engine.begin() as conn:
        conn.execute(
            update(assets)
            .where(assets.c.asset_id == row.asset_id)
            .values(engagement_slug="policy-import")
        )
        for user_id in ("wang", "zhao"):
            conn.execute(
                asset_audiences.insert().values(
                    asset_id=row.asset_id, audience="user", code=user_id
                )
            )
    in_project = Principal(
        "wang", department_code="ops", memberships=(Membership("policy-import", "data-intel"),)
    )
    outsider = Principal("zhao", department_code="sales")
    checkup.sweep(engine, People(CHEN, in_project, outsider), now=NOW, notify=False)
    moved = Principal("wang", department_code="ops")
    people = People(CHEN, moved, outsider)
    result = checkup.sweep(engine, people, now=NOW + timedelta(days=1))
    rules = _rules(checkup.run(engine, CHEN, now=NOW, directory=people), row.asset_id)
    assert ("named_moved", "wang") in rules
    assert ("named_moved", "zhao") not in rules, "有意点名给外部门的人不提醒"
    assert result["new"] >= 1
    # 负责人把他从可见范围里去掉：问题消失
    with engine.begin() as conn:
        conn.execute(
            asset_audiences.delete().where(
                asset_audiences.c.asset_id == row.asset_id, asset_audiences.c.code == "wang"
            )
        )
    checkup.sweep(engine, people, now=NOW + timedelta(days=2))
    assert ("named_moved", "wang") not in _rules(
        checkup.run(engine, CHEN, now=NOW, directory=people), row.asset_id
    )


def test_housekeeping_keeps_recent_bundles_and_referenced_files(indexed, tmp_path) -> None:
    import os
    import time as clock

    now = clock.time()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    for index in range(25):
        path = bundles / f"b{index:02d}.json"
        path.write_text("{}")
        age = (40 if index < 10 else 1) * 86400
        os.utime(path, (now - age - index, now - age - index))
    # 30 天以前的有 10 份，但最新的 20 份总要留着：只删 5 份
    assert housekeeping.prune_bundles(bundles, now=now) == 5
    assert len(list(bundles.glob("*.json"))) == 20

    store = indexed.file_store
    kept = store.put(b"referenced by a draft")
    orphan_old = store.put(b"nobody uses me")
    orphan_new = store.put(b"just uploaded")
    refs = tmp_path / "blobs" / "draft-1" / ".refs"
    refs.mkdir(parents=True)
    (refs / "a.txt").write_text(kept)
    old = now - 10 * 86400
    for sha in (kept, orphan_old):
        os.utime(store.path(sha), (old, old))
    housekeeping.prune_files(indexed.engine, store.root, tmp_path / "blobs", now=now)
    assert store.exists(kept), "草稿引用着的不删"
    assert not store.exists(orphan_old), "没人引用、过了宽限期的删掉"
    assert store.exists(orphan_new), "刚上传的还在宽限期里"
    with indexed.engine.connect() as conn:
        attached = [
            item["sha256"]
            for (raw,) in conn.execute(select(assets.c.attachments_json))
            for item in __import__("json").loads(raw or "[]")
            if item.get("sha256")
        ]
    for sha in attached:
        assert store.exists(sha), "资产附件不删"
