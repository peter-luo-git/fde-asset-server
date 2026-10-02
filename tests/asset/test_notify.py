"""订阅与变更通知。

推荐是别人推给我，订阅是我主动盯；变更通知是"我用过的东西改了"。
三者共用一个站内收件箱。
"""

from __future__ import annotations

from sqlalchemy import select, update

from fde_asset.core.db import assets
from fde_asset.modules.notify import service as notify
from fde_asset.platform.identity import Principal
from tests.conftest import as_user

CHEN = Principal("chen", department_code="data-intel")
LI = Principal("li", department_code="data-intel")


def _first(engine, **where):
    statement = select(assets)
    for key, value in where.items():
        statement = statement.where(getattr(assets.c, key) == value)
    with engine.connect() as conn:
        return conn.execute(statement).first()


def test_subscribe_is_idempotent(indexed) -> None:
    first = notify.subscribe(indexed.engine, CHEN, "industry", "insurance")
    second = notify.subscribe(indexed.engine, CHEN, "industry", "insurance")
    assert first["created"] is True and second["created"] is False
    assert len(notify.list_subscriptions(indexed.engine, CHEN)) == 1


def test_unknown_filter_type_is_rejected(indexed) -> None:
    import pytest

    with pytest.raises(notify.NotifyError, match="不支持"):
        notify.subscribe(indexed.engine, CHEN, "随便", "x")


def test_only_owner_can_unsubscribe(indexed) -> None:
    created = notify.subscribe(indexed.engine, CHEN, "kind", "Case")
    assert notify.unsubscribe(indexed.engine, LI, created["subscription_id"]) is False
    assert notify.unsubscribe(indexed.engine, CHEN, created["subscription_id"]) is True


def test_diff_is_section_level(indexed) -> None:
    """改了什么要能说清楚是哪一节，否则通知等于没说。"""
    row = _first(indexed.engine, kind="Case")
    notify.snapshot_version(indexed.engine, row)

    with indexed.engine.begin() as conn:
        conn.execute(
            update(assets)
            .where(assets.c.asset_id == row.asset_id)
            .values(
                commit_sha="v2",
                summary="改过的一句话结论",
                content_text=(row.content_text or "") + "\n## 新增的一节\n内容\n",
            )
        )
    updated = _first(indexed.engine, asset_id=row.asset_id)
    notify.snapshot_version(indexed.engine, updated)

    diff = notify.diff_summary(indexed.engine, row.asset_id)
    assert diff["summary_changed"] is True
    assert "新增的一节" in diff["added"]


def test_snapshot_is_not_duplicated(indexed) -> None:
    row = _first(indexed.engine, kind="Case")
    assert notify.snapshot_version(indexed.engine, row) in (True, False)
    assert notify.snapshot_version(indexed.engine, row) is False, "同一个 commit 不重复存"


def test_subscribers_get_notified_about_new_assets(indexed) -> None:
    notify.subscribe(indexed.engine, LI, "kind", "Case")
    row = _first(indexed.engine, kind="Case")
    result = notify.announce_new(indexed.engine, row.asset_id, author="chen")
    assert result["delivered"] >= 1

    inbox = notify.inbox(indexed.engine, LI)
    assert inbox and inbox[0]["kind"] == "asset_new"
    assert "你订阅了" in inbox[0]["reason"], "要说清楚为什么收到这条"


def test_author_is_not_notified_about_own_change(indexed) -> None:
    notify.subscribe(indexed.engine, CHEN, "kind", "Case")
    row = _first(indexed.engine, kind="Case")
    notify.announce_new(indexed.engine, row.asset_id, author="chen")
    assert notify.inbox(indexed.engine, CHEN) == [], "自己改的不用通知自己"


def test_change_notice_says_what_changed(indexed) -> None:
    notify.subscribe(indexed.engine, LI, "kind", "Case")
    row = _first(indexed.engine, kind="Case")
    notify.snapshot_version(indexed.engine, row)
    with indexed.engine.begin() as conn:
        conn.execute(
            update(assets)
            .where(assets.c.asset_id == row.asset_id)
            .values(commit_sha="v2", content_text=(row.content_text or "") + "\n## 回滚预案\n有\n")
        )
    notify.snapshot_version(indexed.engine, _first(indexed.engine, asset_id=row.asset_id))

    result = notify.announce_change(indexed.engine, row.asset_id, author="chen")
    assert result["delivered"] >= 1
    assert "回滚预案" in result["body"]

    inbox = notify.inbox(indexed.engine, LI)
    assert inbox[0]["kind"] == "asset_changed"
    assert "回滚预案" in inbox[0]["body"]


def test_mark_read_removes_from_unread(indexed) -> None:
    notify.subscribe(indexed.engine, LI, "kind", "Case")
    row = _first(indexed.engine, kind="Case")
    notify.announce_new(indexed.engine, row.asset_id, author="chen")
    item = notify.inbox(indexed.engine, LI)[0]
    assert notify.mark_read(indexed.engine, LI, item["notification_id"]) is True
    assert notify.inbox(indexed.engine, LI) == []
    assert notify.inbox(indexed.engine, LI, unread_only=False), "读过的还能翻出来"


def test_notification_hides_assets_you_can_no_longer_see(indexed) -> None:
    """通知发出之后资产被收紧了作用域，收件箱里也不该再露出来。"""
    zhao = Principal("zhao", department_code="market")
    notify.subscribe(indexed.engine, zhao, "kind", "Case")
    row = _first(indexed.engine, scope="engagement")
    notify.announce_new(indexed.engine, row.asset_id, author="chen")
    assert notify.inbox(indexed.engine, zhao) == []


def test_subscription_endpoints(client) -> None:
    chen = as_user(client, "chen")
    created = chen.post(
        "/api/v1/subscriptions", json={"filter_type": "industry", "filter_value": "insurance"}
    )
    assert created.status_code == 200
    listed = chen.get("/api/v1/subscriptions").json()
    assert listed["items"] and "industry" in listed["filter_types"]

    removed = chen.delete(f"/api/v1/subscriptions/{created.json()['subscription_id']}")
    assert removed.status_code == 200
    assert chen.get("/api/v1/subscriptions").json()["items"] == []
