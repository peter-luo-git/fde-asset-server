"""SOP 只用于执行：带类型的检查点、编写检查、进度与推进、Agent 回报规则、默认流程。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from fde_asset.api.app import create_app
from fde_asset.core.db import asset_mandatory, assets, sop_defaults, work_item_assets
from fde_asset.modules.asset import sop
from fde_asset.modules.asset.indexer import index_all
from fde_asset.modules.asset.snapshot import select_assets

KEY = "sop-execution-service-key"
HEADERS = {"X-FDE-Service-Key": KEY}
CALLER = {"username": "chen", "engagement_slug": "policy-import"}

L1_STEPS = """## 步骤
1. 方案
   写方案。
   - 检查点[交付物]：`docs/方案.md` 已提交，含「范围」「风险」两节
   - 检查点[确认:客户]：客户确认范围
   - 检查点：方案经过评审
   - 产出：方案文档
   - 技能：requirement-survey、risk-list
2. 实施
   - 检查点[命令]：测试通过 `pytest -q`
   - 推进：自动
3. 验收
   - 检查点[判断]：交付质量良好
"""


# ---- 解析与编写检查 ----


def test_typed_checkpoints_skills_and_auto_are_parsed() -> None:
    steps = sop.parse_steps(L1_STEPS.split("\n", 1)[1])
    first = steps[0]
    assert [c.type for c in first.checks] == ["artifact", "confirm", "confirm"]
    assert first.checks[0].path == "docs/方案.md"
    assert first.checks[0].sections == ["范围", "风险"]
    assert first.checks[1].role == "客户" and first.checks[1].tagged
    assert first.checks[2].tagged is False
    assert first.skills == ["requirement-survey", "risk-list"]
    assert first.outputs == ["方案文档"]
    assert steps[1].checks[0].command == "pytest -q" and steps[1].auto is True
    # 旧接口的字符串列表照旧
    assert first.checkpoints[1] == "客户确认范围"


def test_half_width_brackets_and_colons_are_accepted() -> None:
    step = sop.parse_steps("1. 准备\n   - 检查点[确认:业务]: 口径确认\n")[0]
    assert step.checks[0].type == "confirm" and step.checks[0].role == "业务"


def test_lint_reports_vague_words_untyped_and_missing_command() -> None:
    content = "## 步骤\n1. 准备\n   - 检查点[命令]：环境就绪\n   - 检查点：方案合理\n   - 检查：漏了冒号\n2. 空\n"
    messages = [f["message"] for f in sop.lint(content)]
    assert any("反引号" in m for m in messages)
    assert any("没写类型" in m for m in messages)
    assert any("「合理」" in m for m in messages)
    assert any("看着像检查点" in m for m in messages)
    assert any("没有检查点" in m for m in messages)


def test_lint_judgment_may_use_vague_words_and_auto_needs_machine_checks() -> None:
    content = "## 步骤\n1. 评审\n   - 检查点[判断]：质量良好\n   - 推进：自动\n"
    findings = sop.lint(content)
    assert not any("良好" in f["message"] for f in findings)
    assert any("不会自动进入下一步" in f["message"] for f in findings)
    assert any("全靠人工" in f["message"] for f in findings)


def test_lint_unknown_type_and_missing_steps() -> None:
    assert sop.lint("## 结论与目的\n无\n")[0]["level"] == "error"
    assert sop.lint("## 步骤\n没有编号\n")[0]["level"] == "error"
    findings = sop.lint("## 步骤\n1. 甲\n   - 检查点[随便]：x\n")
    assert any("认不出" in f["message"] for f in findings)


def test_lint_layer_needs_parent() -> None:
    assert sop.lint(L1_STEPS, layer="L2")[0]["level"] == "error"


# ---- 服务 ----


@pytest.fixture()
def service(seeded):
    directory_path = seeded.root / "directory.json"
    directory = json.loads(directory_path.read_text())
    later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    earlier = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    directory["work_items"] = {
        "w-1": {"engagement_slug": "policy-import"},
        "w-new": {"engagement_slug": "policy-import", "created_at": later},
        "w-old": {"engagement_slug": "policy-import", "created_at": earlier},
    }
    directory_path.write_text(json.dumps(directory))
    with TestClient(create_app(seeded.model_copy(update={"server_service_key": KEY}))) as client:
        ctx = client.app.state.context
        index_all(ctx.engine, ctx.repo_port, ctx.repos(), file_store=ctx.file_store)
        client.headers.update({"X-FDE-User": "chen"})
        yield client


def _id(service, name: str = "delivery-standard") -> str:
    with service.app.state.context.engine.connect() as conn:
        return conn.execute(select(assets.c.asset_id).where(assets.c.name == name)).scalar_one()


def _bind(service, step: int = 1, work_item: str = "w-1", **extra):
    return service.post(
        f"/api/v1/work-items/{work_item}/assets",
        json={"asset_id": _id(service), "role": "sop_step", "sop_step": step, **extra},
    )


def _state(service, work_item: str = "w-1") -> dict:
    return service.get(f"/api/v1/work-items/{work_item}/sop").json()


def _report(service, results, step: int = 1, work_item: str = "w-1"):
    return service.post(
        f"/internal/v1/work-items/{work_item}/sop-report",
        headers=HEADERS,
        json={**CALLER, "step": step, "results": results, "agent": "coder", "turn_id": "t-1"},
    )


def test_binding_freezes_steps_and_version(service) -> None:
    assert _bind(service).status_code == 200
    state = _state(service)
    assert state["bound"] and state["sop_step"] == 1 and state["sop_version"]
    assert state["steps"][0]["checks"][0]["type"] == "artifact"
    assert state["steps"][0]["phase"] == "current" and state["outdated"] is False
    with service.app.state.context.engine.begin() as conn:
        conn.execute(
            update(assets).where(assets.c.asset_id == _id(service)).values(version="9.9.9")
        )
    state = _state(service)
    assert state["outdated"] is True and state["latest_version"] == "9.9.9"
    assert service.post("/api/v1/work-items/w-1/sop/refresh").status_code == 200
    assert _state(service)["outdated"] is False


def test_agent_claims_follow_rules(service) -> None:
    _bind(service)
    response = _report(
        service,
        [
            {"index": 0, "status": "passed", "evidence": "docs/方案.md", "verified": True},
            {"index": 1, "status": "passed", "evidence": "邮件截图"},
        ],
    )
    assert response.status_code == 200, response.text
    results = {item["index"]: item for item in response.json()["results"]}
    assert results[0]["status"] == "passed"
    # 确认类 Agent 说通过也只到待确认
    assert results[1]["status"] == "pending"


def test_machine_check_without_platform_verification_is_unknown(service) -> None:
    _bind(service)
    response = _report(service, [{"index": 0, "status": "passed", "evidence": "docs/方案.md"}])
    assert response.json()["results"][0]["status"] == "unknown"
    response = _report(
        service,
        [
            {
                "index": 0,
                "status": "passed",
                "evidence": "x",
                "verified": False,
                "verify_detail": "文件不存在",
            }
        ],
    )
    assert response.json()["results"][0]["status"] == "failed"


def test_passed_without_evidence_is_downgraded(service) -> None:
    _bind(service)
    response = _report(
        service, [{"index": 0, "status": "passed", "evidence": "", "verified": True}]
    )
    assert response.json()["results"][0]["status"] == "unknown"


def test_report_only_for_current_step(service) -> None:
    _bind(service)
    response = _report(service, [{"index": 0, "status": "failed"}], step=2)
    assert response.status_code == 409


def test_advance_needs_force_when_unfinished_and_human_can_confirm(service) -> None:
    _bind(service)
    response = _bind(service, step=2)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SOP_STEP_UNFINISHED"
    assert _bind(service, step=2, force=True).status_code == 200
    state = _state(service)
    assert state["sop_step"] == 2 and state["steps"][0]["phase"] == "passed_over"
    assert state["history"][-1]["forced"] is True
    # 退回第 1 步，人逐条确认后就能正常推进
    assert _bind(service, step=1).status_code == 200
    for index in range(2):
        assert (
            service.post(
                "/api/v1/work-items/w-1/sop/checks",
                json={"step": 1, "index": index, "decision": "confirm"},
            ).status_code
            == 200
        )
    assert _bind(service, step=2).status_code == 200


def test_board_summary_shows_step_and_check_counts(service) -> None:
    """看板卡片：第几步/共几步、当前步标题、检查点通过和待确认数；别的项目看不到。"""
    assert service.get("/api/v1/engagements/policy-import/sop-board").json() == {"items": {}}
    _bind(service)
    service.post(
        "/api/v1/work-items/w-1/sop/checks",
        json={"step": 1, "index": 0, "decision": "confirm"},
    )
    item = service.get("/api/v1/engagements/policy-import/sop-board").json()["items"]["w-1"]
    state = _state(service)
    assert item["step"] == 1 and item["total"] == len(state["steps"])
    assert item["step_title"] == state["steps"][0]["title"]
    assert item["checks"] == len(state["steps"][0]["checks"]) and item["passed"] == 1
    assert item["finished"] is False
    assert service.get("/api/v1/engagements/other-project/sop-board").status_code == 404


@pytest.mark.parametrize("decision, expected", [("confirm", "passed"), ("reject", "failed")])
def test_human_decision_preserves_agent_report_turn(service, decision, expected) -> None:
    assert _bind(service).status_code == 200
    assert (
        _report(service, [{"index": 1, "status": "passed", "evidence": "邮件截图"}]).status_code
        == 200
    )
    response = service.post(
        "/api/v1/work-items/w-1/sop/checks",
        json={"step": 1, "index": 1, "decision": decision},
    )
    assert response.status_code == 200, response.text
    check = _state(service)["steps"][0]["checks"][1]
    assert check["status"] == expected
    assert check["turn_id"] == "t-1" and check["evidence"] == "邮件截图"
    assert check["by"] == "user:chen"


def test_agent_cannot_override_human_confirmation(service) -> None:
    _bind(service)
    service.post(
        "/api/v1/work-items/w-1/sop/checks", json={"step": 1, "index": 1, "decision": "confirm"}
    )
    response = _report(service, [{"index": 1, "status": "failed", "evidence": "x"}])
    assert response.json()["results"][0]["status"] == "passed"


def test_viewer_cannot_confirm(service) -> None:
    _bind(service)
    service.headers.update({"X-FDE-User": "zhao"})
    response = service.post(
        "/api/v1/work-items/w-1/sop/checks", json={"step": 1, "index": 1, "decision": "confirm"}
    )
    assert response.status_code == 404


def test_agent_prompt_has_outline_current_step_and_report_rules(service) -> None:
    _bind(service)
    payload = service.get(
        "/internal/v1/work-items/w-1/sop-step", params=CALLER, headers=HEADERS
    ).json()
    text = payload["prompt_md"]
    assert payload["found"] and payload["step_title"] == "调研与方案确认"
    assert "全流程提纲" in text and "← 当前" in text and "4. 验收与复盘" in text
    assert "fde_sop_checkpoint_report" in text and "需客户确认" in text
    assert payload["checks"][0]["path"] == "docs/方案.md"


def test_opt_out_is_remembered_against_default(service) -> None:
    ctx = service.app.state.context
    assert _state(service, "w-new")["bound"] is True  # 种子设了全公司默认流程
    assert service.delete("/api/v1/work-items/w-new/sop-step").status_code == 200
    state = _state(service, "w-new")
    assert state["bound"] is False and state["opted_out"] is True
    with ctx.engine.connect() as conn:
        roles = {row.role for row in conn.execute(select(work_item_assets))}
    assert roles == {"sop_none"}


def test_default_only_applies_to_tasks_created_after_it(service) -> None:
    assert _state(service, "w-old")["bound"] is False
    assert _state(service, "w-1")["bound"] is False  # 不知道创建时间的也不动
    state = _state(service, "w-new")
    assert state["bound"] and state["source"].startswith("default:company")


def test_default_most_specific_scope_wins_and_permissions(service) -> None:
    service.headers.update({"X-FDE-User": "chen"})
    response = service.put(
        "/api/v1/sop-defaults",
        json={"entry": "engagement:policy-import", "asset_id": _id(service, "bank-core-migration")},
    )
    assert response.status_code == 403
    service.headers.update({"X-FDE-User": "wang"})  # 项目负责人
    current = service.get("/api/v1/engagements/policy-import/sop-default").json()
    assert current["inherited"] is True and current["can_manage"] is True
    response = service.put(
        "/api/v1/sop-defaults",
        json={"entry": "engagement:policy-import", "asset_id": _id(service, "delivery-standard")},
    )
    assert response.status_code == 200, response.text
    current = service.get("/api/v1/engagements/policy-import/sop-default").json()
    assert current["inherited"] is False and current["default"]["scope"] == "engagement"
    assert (
        service.put(
            "/api/v1/sop-defaults", json={"entry": "user:wang", "asset_id": _id(service)}
        ).status_code
        == 422
    )
    assert service.post(
        "/api/v1/sop-defaults/remove", json={"entry": "engagement:policy-import"}
    ).json()["removed"]


def test_sop_cannot_be_reference_mandatory_or_linked(service) -> None:
    response = service.post(
        "/api/v1/work-items/w-1/assets", json={"asset_id": _id(service), "role": "reference"}
    )
    assert response.status_code == 422
    service.headers.update({"X-FDE-User": "admin"})
    response = service.post(f"/api/v1/assets/{_id(service)}/mandatory", json={"entry": "company"})
    assert response.status_code == 422 and "默认流程" in response.json()["detail"]
    response = service.post(
        "/api/v1/recommend/link",
        json={"target_type": "engagement", "target_id": "policy-import", "asset_id": _id(service)},
    )
    assert response.status_code == 422


def test_sops_are_not_carried_in_bundle_even_if_mandatory(service) -> None:
    ctx = service.app.state.context
    with ctx.engine.begin() as conn:
        conn.execute(
            asset_mandatory.insert().values(asset_id=_id(service), scope="company", code="")
        )
    chosen = select_assets(
        ctx.engine,
        department_code="finance",
        customer_code="HUAAN",
        engagement_slug="policy-import",
        target_type="engagement",
        target_id="policy-import",
    )
    assert all(row.kind != "Sop" for row in chosen.rows)
    listing = service.get("/api/v1/mandatory", headers={"X-FDE-User": "admin"}).json()["items"]
    sop_rows = [row for row in listing if row["kind"] == "Sop"]
    assert sop_rows and all(not row["effective"] and row.get("inert_reason") for row in sop_rows)


def test_preview_merges_with_parent_and_flags_overrides(service) -> None:
    response = service.post(
        "/api/v1/sop/preview",
        json={
            "content": "## 步骤\n2. 环境（增强）\n   - 检查点[命令]：`make check`\n",
            "layer": "L2",
            "extends": "delivery-standard",
        },
    )
    body = response.json()
    assert response.status_code == 200 and body["parent_found"]
    assert [s["number"] for s in body["steps"]] == [1, 2, 3, 4]
    assert body["steps"][1]["overridden_by"]
    assert any("整步替换" in f["message"] for f in body["findings"])
    parents = service.get("/api/v1/sop/parents", params={"layer": "L2"}).json()["items"]
    assert [p["name"] for p in parents] == ["delivery-standard"]
    assert service.get("/api/v1/sop/parents", params={"layer": "L1"}).json()["items"] == []


def test_executable_list_orders_by_layer(service) -> None:
    items = service.get("/api/v1/sops", params={"work_item_id": "w-1"}).json()["items"]
    layers = [item["layer"] for item in items]
    assert layers == sorted(layers, key=lambda layer: {"L3": 0, "L2": 1, "L1": 2}[layer])


def test_seed_sets_company_default_not_mandatory(service) -> None:
    with service.app.state.context.engine.connect() as conn:
        defaults = conn.execute(select(sop_defaults)).fetchall()
        mandatory_kinds = {
            row.kind
            for row in conn.execute(
                select(assets.c.kind).join(
                    asset_mandatory, asset_mandatory.c.asset_id == assets.c.asset_id
                )
            )
        }
    assert [row.scope for row in defaults] == ["company"]
    assert "Sop" not in mandatory_kinds


# ---- 编写：模板、层级、上层流程 ----


def _draft(client, **overrides):
    body = {
        "kind": "Sop",
        "title": "测试流程",
        "scope": "department",
        "department_code": "data-intel",
    }
    body.update(overrides)
    return client.post("/api/v1/harvest-candidates", json=body)


def test_new_sop_draft_is_l1_without_extends_and_passes_layer_checks(client) -> None:
    from tests.conftest import as_user

    chen = as_user(client, "chen")
    draft = _draft(chen).json()
    manifest = draft["files"]["asset.yaml"]
    assert "layer: L1" in manifest and "extends" not in manifest
    checked = chen.post(f"/api/v1/harvest-candidates/{draft['candidate_id']}/checks").json()
    checks = checked.get("checks", checked)
    codes = {item.get("code") for item in checks["format"]}
    assert "sop_extends_missing" not in codes and "sop_steps_invalid" not in codes
    assert "sop_hints" in checks
    content = draft["files"]["SOP.md"]
    assert "检查点[交付物]" in content and "检查点[命令]" in content


def test_layer_and_parent_are_editable_on_the_draft(client) -> None:
    from tests.conftest import as_user

    chen = as_user(client, "chen")
    draft = _draft(chen).json()
    url = f"/api/v1/harvest-candidates/{draft['candidate_id']}"
    updated = chen.patch(
        url, json={"meta": {"sop": {"layer": "L2", "extends": "delivery-standard"}}}
    )
    assert updated.status_code == 200, updated.text
    manifest = updated.json()["files"]["asset.yaml"]
    assert "layer: L2" in manifest and "extends: delivery-standard" in manifest
    back = chen.patch(url, json={"meta": {"sop": {"layer": "L1", "extends": "delivery-standard"}}})
    assert "extends" not in back.json()["files"]["asset.yaml"]
    assert chen.patch(url, json={"meta": {"sop": {"layer": "L9"}}}).status_code == 409


def test_layered_template_only_has_override_steps(client) -> None:
    l1 = client.get("/api/v1/sop/template", params={"layer": "L1", "title": "甲"}).json()["content"]
    l2 = client.get("/api/v1/sop/template", params={"layer": "L2", "title": "甲"}).json()["content"]
    assert l1.startswith("# 甲") and "1. <第一步标题>" in l1
    assert "只写要覆盖或新增的步骤" in l2 and "1. <第一步标题>" not in l2
    assert "## 异常处理" in l2
    assert not [f for f in sop.lint(l2, layer="L2", extends="x") if f["level"] == "error"]
