"""沿用旧数据目录启动：名单文件里后来才有的部分要补上，已有的一律不动。"""

from __future__ import annotations

import json

from scripts.seed_assets import DIRECTORY, merge_missing, refresh_directory


def _old_directory() -> dict:
    """十月一日那一版的名单：只有用户，没有项目和 Agent 清单，成员关系里也没有客户代号。"""
    users = json.loads(json.dumps(DIRECTORY["users"]))
    for user in users.values():
        user.pop("is_department_head", None)
        for membership in user["memberships"]:
            membership.pop("customer_code", None)
    return {"users": users}


def test_old_directory_gets_what_was_added_later(settings) -> None:
    target = settings.root / "directory.json"
    target.write_text(json.dumps(_old_directory(), ensure_ascii=False), encoding="utf-8")

    added = refresh_directory(settings)

    assert json.loads(target.read_text(encoding="utf-8")) == DIRECTORY
    assert "engagements" in added and "agents" in added
    assert "users.wang.is_department_head" in added
    assert "users.chen.memberships[policy-import].customer_code" in added
    assert refresh_directory(settings) == [], "补过一次就不该再动"


def test_existing_content_is_never_overwritten(settings) -> None:
    mine = _old_directory()
    mine["users"]["wang"]["display_name"] = "王总"
    mine["users"]["wang"]["memberships"][0]["role"] = "member"
    mine["users"]["chen"]["memberships"] = []  # 有意把小陈移出了项目
    del mine["users"]["zhao"]  # 有意删了一个人：会被当成缺的补回来，但不影响别人
    mine["users"]["guest"] = {"display_name": "访客", "department_code": "", "memberships": []}
    mine["engagements"] = {"policy-import": {"title": "我改过的项目名", "owner": "chen"}}
    target = settings.root / "directory.json"
    target.write_text(json.dumps(mine, ensure_ascii=False), encoding="utf-8")

    refresh_directory(settings)
    after = json.loads(target.read_text(encoding="utf-8"))

    assert after["users"]["wang"]["display_name"] == "王总"
    assert after["users"]["wang"]["memberships"][0]["role"] == "member"
    assert after["users"]["wang"]["memberships"][0]["customer_code"] == "HUAAN", "只补缺的字段"
    assert after["users"]["chen"]["memberships"] == [], "列表不增不删"
    assert after["users"]["guest"]["display_name"] == "访客", "自己加的人留着"
    assert after["engagements"]["policy-import"]["title"] == "我改过的项目名"
    assert after["engagements"]["policy-import"]["owner"] == "chen"
    assert after["engagements"]["policy-import"]["industry"] == "insurance", "缺的字段补上"
    assert "core-migration" in after["engagements"], "缺的项目补上"


def test_missing_file_is_left_alone(settings) -> None:
    assert refresh_directory(settings) == []
    assert not (settings.root / "directory.json").exists()


def test_scalars_and_type_mismatches_keep_the_existing_side() -> None:
    merged, added = merge_missing({"a": 1, "b": "text"}, {"a": 2, "b": {"nested": True}, "c": 3})
    assert merged == {"a": 1, "b": "text", "c": 3}
    assert added == ["c"]


def test_wang_heads_the_department_his_projects_belong_to(client) -> None:
    """演示数据里要有一位部门主管，否则「部门视图 → 推送给负责人」这条线走不通。"""
    client.headers.update({"X-FDE-User": "wang"})
    targets = client.get("/api/v1/recommend/targets").json()
    assert {item["target_id"] for item in targets["department"]} >= {
        "policy-import",
        "core-migration",
        "migration-reviewer",
    }
    assert all(item["department_code"] == "finance" for item in targets["department"])

    client.headers.pop("X-FDE-User")
    roles = {
        item["user_id"]: item["roles"]
        for item in client.get("/api/v1/dev/identities").json()["items"]
    }
    assert "部门主管" in roles["wang"]
