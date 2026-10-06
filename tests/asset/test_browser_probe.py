"""平台够不着的网络，借用户的电脑探活：结果记下是谁探的，平台探失败不盖掉它。"""

from __future__ import annotations

from sqlalchemy import select

from fde_asset.core.db import app_health, assets
from fde_asset.modules.app import health
from tests.conftest import as_user


def _apps(client) -> dict[str, dict]:
    return {item["title"]: item for item in client.get("/api/v1/apps").json()["items"]}


def _report(client, asset_id: str, reachable: bool, latency: int = 120):
    return client.post(
        "/api/v1/apps/health/report",
        json={"items": [{"asset_id": asset_id, "reachable": reachable, "latency_ms": latency}]},
    )


def test_browser_result_is_recorded_with_who_probed(client) -> None:
    chen = as_user(client, "chen")
    console = _apps(chen)["保单导入控制台"]
    assert console["demo"]["url"], "这份示例应用登记了演示地址"

    done = _report(chen, console["asset_id"], True)
    assert done.status_code == 200 and done.json() == {"recorded": 1, "by_status": {"online": 1}}

    after = _apps(as_user(client, "li"))["保单导入控制台"]["health"]
    assert after["status"] == "online"
    assert after["checked_via"] == "browser"
    assert after["checked_by"] == "chen" and after["checked_by_name"] == "小陈"
    assert after["latency_ms"] == 120 and after["last_online_at"]


def test_platform_failure_does_not_override_a_fresh_browser_result(client, context) -> None:
    chen = as_user(client, "chen")
    console = _apps(chen)["保单导入控制台"]
    _report(chen, console["asset_id"], True)

    def unreachable(url: str):
        raise OSError("平台不能出网")

    health.probe_all(context.engine, fetch=unreachable)
    still = _apps(as_user(client, "chen"))["保单导入控制台"]["health"]
    assert still["status"] == "online" and still["checked_via"] == "browser"

    # 平台自己探通了，就以平台的为准
    health.probe_all(context.engine, fetch=lambda url: (200, ""))
    now = _apps(as_user(client, "chen"))["保单导入控制台"]["health"]
    assert now["status"] == "online" and now["checked_via"] == "server" and now["checked_by"] == ""


def test_unreachable_from_one_computer_does_not_override_the_platform(client, context) -> None:
    chen = as_user(client, "chen")
    console = _apps(chen)["保单导入控制台"]
    health.probe_all(context.engine, fetch=lambda url: (200, ""))

    # 这个人的电脑不在那个网络里，连不上很正常：平台刚探通过的不能被他改成离线
    assert _report(chen, console["asset_id"], False).json()["recorded"] == 0
    assert _apps(as_user(client, "chen"))["保单导入控制台"]["health"]["checked_via"] == "server"

    # 平台也够不着的时候，用户电脑连不上才记下来：内网应用记「够不着」，不武断说离线
    def unreachable(url: str):
        raise OSError("no route")

    health.probe_all(context.engine, fetch=unreachable)
    assert _report(as_user(client, "chen"), console["asset_id"], False).json()["recorded"] == 1
    after = _apps(as_user(client, "chen"))["保单导入控制台"]["health"]
    assert after["checked_via"] == "browser"
    assert after["status"] == "unreachable", "用户电脑没连上只记够不着，不下离线的结论"


def test_only_visible_applications_and_only_probeable_ones(client, context) -> None:
    chen = as_user(client, "chen")
    apps = _apps(chen)
    console, labeler = apps["保单导入控制台"], apps["标注小工具"]

    # 看不到这份应用的人报不了它的状态
    zhao = as_user(client, "zhao")
    assert _report(zhao, console["asset_id"], True).json()["recorded"] == 0
    # 本机应用不探，报了也不收；不是应用的资产也不收
    chen = as_user(client, "chen")
    assert labeler["demo"].get("network") == "local"
    assert _report(chen, labeler["asset_id"], True).json()["recorded"] == 0
    with context.engine.connect() as conn:
        case_id = (
            conn.execute(select(assets.c.asset_id).where(assets.c.kind == "Case")).scalars().first()
        )
    assert _report(chen, case_id, True).json()["recorded"] == 0
    assert chen.post("/api/v1/apps/health/report", json={"items": []}).status_code == 422
    with context.engine.connect() as conn:
        assert conn.execute(select(app_health)).fetchall() == []


def test_browser_failure_never_marks_an_app_offline(client, context) -> None:
    """浏览器探活容易被它自己的安全设置拦掉：没连上不等于服务挂了，公网应用也一样。"""
    chen = as_user(client, "chen")
    console = _apps(chen)["保单导入控制台"]

    # 还没人探过：一个人没连上，只记够不着
    assert _report(chen, console["asset_id"], False).json()["by_status"] == {"unreachable": 1}

    # 平台探出来的离线更可靠，不被改写
    health.probe_all(context.engine, fetch=lambda url: (503, ""))
    assert _report(as_user(client, "chen"), console["asset_id"], False).json()["recorded"] == 0
    assert _apps(as_user(client, "chen"))["保单导入控制台"]["health"]["status"] == "offline"

    # 同事刚连上过，另一个人没连上，不推翻
    _report(as_user(client, "li"), console["asset_id"], True)
    assert _report(as_user(client, "chen"), console["asset_id"], False).json()["recorded"] == 0
    after = _apps(as_user(client, "chen"))["保单导入控制台"]["health"]
    assert after["status"] == "online" and after["checked_by"] == "li"
