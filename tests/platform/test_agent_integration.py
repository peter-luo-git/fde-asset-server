"""和 Agent 之间：给 Agent 关联的资产进资产包、读了什么计入使用、任务当前的流程步骤。"""

from __future__ import annotations

from sqlalchemy import select

from fde_asset.core.db import asset_usages, assets, target_assets
from fde_asset.modules.asset import agent_bundle

KEY = "test-service-key-0123456789abcdef"
HEADERS = {"X-FDE-Service-Key": KEY}


def _asset_id(context, name: str) -> str:
    with context.engine.connect() as conn:
        return conn.execute(select(assets.c.asset_id).where(assets.c.name == name)).scalar_one()


def _link(context, target_type: str, target_id: str, name: str) -> None:
    with context.engine.begin() as conn:
        conn.execute(
            target_assets.insert().values(
                target_type=target_type,
                target_id=target_id,
                asset_id=_asset_id(context, name),
                source="manual",
                created_by="admin",
            )
        )


def _bundle(context, tmp_path, **kwargs):
    return agent_bundle.build_bundle(
        context.engine,
        context.repo_port,
        bundle_root=tmp_path / "bundles",
        department_code="finance",
        engagement_slug="policy-import",
        target_type="engagement",
        target_id="policy-import",
        **kwargs,
    )


def test_bundle_carries_all_knowledge_kinds_but_no_procedures(indexed, tmp_path) -> None:
    # 上层的知识要关联了才带；项目自己的案例不用关联
    _link(indexed, "engagement", "policy-import", "bank-core-migration-solution")
    bundle = _bundle(indexed, tmp_path)
    tops = {"/".join(path.split("/")[:2]) for path in bundle.files if path.startswith("knowledge/")}
    assert "knowledge/cases" in tops and "knowledge/solutions" in tops
    # SOP 只用于执行任务（2026-10-25）：当前步骤由平台注入，不放进资产包当参考
    assert not any(path.startswith("knowledge/sops/") for path in bundle.files)
    # 运行镜像里的校验只认这三个顶层目录，多一个 Agent 就起不来
    assert {path.split("/")[0] for path in bundle.files} <= {"rules", "skills", "knowledge"}
    assert not any(path.startswith("applications/") for path in bundle.files), "应用不给 Agent"


def test_assets_linked_to_the_agent_join_the_project_links(indexed, tmp_path) -> None:
    # 项目自己只关联了一份知识：上层的技能和知识就只带关联过的
    _link(indexed, "engagement", "policy-import", "policy-import-poc-retro")
    project_only = _bundle(indexed, tmp_path)
    assert "skills/insurance-policy-import/SKILL.md" not in project_only.files

    # 再给这个 Agent 关联一个公司级技能：用它干活时就带上了，换一个 Agent 则没有
    _link(indexed, "agent", "coder-default", "insurance-policy-import")
    with_agent = _bundle(indexed, tmp_path, agent_key="coder-default")
    assert "skills/insurance-policy-import/SKILL.md" in with_agent.files
    other_agent = _bundle(indexed, tmp_path, agent_key="poc-rapid")
    assert "skills/insurance-policy-import/SKILL.md" not in other_agent.files
    assert with_agent.revision != other_agent.revision


def test_paths_in_a_bundle_resolve_back_to_assets(indexed, tmp_path) -> None:
    _link(indexed, "engagement", "policy-import", "insurance-policy-import")
    bundle = _bundle(indexed, tmp_path)
    skill = _asset_id(indexed, "insurance-policy-import")
    assert agent_bundle.resolve_path(bundle, "skills/insurance-policy-import/SKILL.md") == (
        skill,
        "loaded",
    )
    assert agent_bundle.resolve_path(bundle, "skills/insurance-policy-import/asset.yaml") == (
        skill,
        "read",
    )
    rule = next(path for path in bundle.files if path.startswith("rules/"))
    resolved = agent_bundle.resolve_path(bundle, rule)
    assert resolved is not None and resolved[1] == "read"
    assert agent_bundle.resolve_path(bundle, "knowledge/INDEX.md") is None
    assert agent_bundle.resolve_path(bundle, "skills/no-such-skill/SKILL.md") is None


def _service(seeded):
    from fastapi.testclient import TestClient

    from fde_asset.api.app import create_app

    client = TestClient(create_app(seeded.model_copy(update={"server_service_key": KEY})))
    client.headers.update({"X-FDE-User": "admin"})
    client.post("/api/v1/admin/assets/reindex")
    return client


def test_reported_reads_are_counted_once_per_run(seeded) -> None:
    with _service(seeded) as service:
        context = service.app.state.context
        _link(context, "engagement", "policy-import", "insurance-policy-import")
        bundle = service.post(
            "/internal/v1/agent-bundle",
            json={"username": "chen", "engagement_slug": "policy-import"},
            headers=HEADERS,
        ).json()
        skill_path = "skills/insurance-policy-import/SKILL.md"
        assert skill_path in bundle["files"]
        body = {
            "revision": bundle["revision"],
            "paths": [skill_path, "knowledge/INDEX.md"],
            "session_id": "run-1",
            "engagement_slug": "policy-import",
            "work_item_id": "w-1",
            "agent_key": "coder-default",
        }
        assert service.post("/internal/v1/agent-usage", json=body).status_code == 404, (
            "要凭服务密钥"
        )
        first = service.post("/internal/v1/agent-usage", json=body, headers=HEADERS)
        assert first.status_code == 200, first.text
        assert first.json()["ignored"] == ["knowledge/INDEX.md"]
        service.post("/internal/v1/agent-usage", json=body, headers=HEADERS)  # 同一次执行重复报

        with context.engine.connect() as conn:
            rows = conn.execute(
                select(asset_usages).where(asset_usages.c.session_id == "run-1")
            ).fetchall()
        assert len(rows) == 1 and rows[0].event == "loaded"
        assert rows[0].asset_id == _asset_id(context, "insurance-policy-import")
        assert rows[0].actor_id == "coder-default" and rows[0].engagement_slug == "policy-import"

        unknown = service.post(
            "/internal/v1/agent-usage", json={**body, "revision": "0" * 40}, headers=HEADERS
        )
        assert unknown.status_code == 404


def test_work_item_follows_a_procedure_step_by_step(seeded) -> None:
    with _service(seeded) as service:
        context = service.app.state.context
        sop = _asset_id(context, "delivery-standard")
        path = "/internal/v1/work-items/w-9/sop-step"
        caller = {"username": "chen", "engagement_slug": "policy-import"}
        assert service.get(path).status_code == 404, "要凭服务密钥"
        assert service.get(path, params=caller, headers=HEADERS).json()["found"] is False

        bound = service.post(
            "/api/v1/work-items/w-9/assets",
            json={"asset_id": sop, "role": "sop_step", "sop_step": 1},
        )
        assert bound.status_code == 200, bound.text
        first = service.get(path, params=caller, headers=HEADERS).json()
        assert first["found"] is True and first["step"] == 1 and first["prompt_md"]

        # 做完一步换到下一步：当前这步还有没通过的检查点时要明确选"仍然进入"
        blocked = service.post(
            "/api/v1/work-items/w-9/assets",
            json={"asset_id": sop, "role": "sop_step", "sop_step": 2},
        )
        assert blocked.status_code == 409
        moved = service.post(
            "/api/v1/work-items/w-9/assets",
            json={"asset_id": sop, "role": "sop_step", "sop_step": 2, "force": True},
        )
        assert moved.status_code == 200, moved.text
        second = service.get(path, params=caller, headers=HEADERS).json()
        assert second["step"] == 2 and second["prompt_md"] != first["prompt_md"]

        assert service.delete("/api/v1/work-items/w-9/sop-step").json()["removed"] is True
        assert service.get(path, params=caller, headers=HEADERS).json()["found"] is False


def test_references_in_a_delivery_summary_are_kept_apart_from_reuse(seeded) -> None:
    """Agent 自己说参考了什么：记下来，但不算进使用次数和等级。"""
    with _service(seeded) as service:
        context = service.app.state.context
        _link(context, "engagement", "policy-import", "insurance-policy-import")
        bundle = service.post(
            "/internal/v1/agent-bundle",
            json={"username": "chen", "engagement_slug": "policy-import"},
            headers=HEADERS,
        ).json()
        loaded = agent_bundle.load_bundle(context.settings.bundle_dir, bundle["revision"])
        skill = _asset_id(context, "insurance-policy-import")
        # 它写引用的格式不统一：照类型写的、照目录写的、照知识索引里给的写的，都要认得
        assert agent_bundle.resolve_ref(loaded, "[[skill/insurance-policy-import]]") == skill
        assert agent_bundle.resolve_ref(loaded, "skills/insurance-policy-import") == skill
        assert agent_bundle.resolve_ref(loaded, "company:insurance-policy-import") == skill
        assert agent_bundle.resolve_ref(loaded, "knowledge/INDEX") is None

        before = service.get(f"/api/v1/assets/{skill}").json()["usage_count"]
        reported = service.post(
            "/internal/v1/agent-usage",
            json={
                "revision": bundle["revision"],
                "refs": ["skill/insurance-policy-import", "knowledge/INDEX"],
                "session_id": "run-2",
                "engagement_slug": "policy-import",
                "agent_key": "coder-default",
            },
            headers=HEADERS,
        )
        assert reported.status_code == 200 and reported.json()["ignored"] == ["knowledge/INDEX"]
        with context.engine.connect() as conn:
            (row,) = conn.execute(
                select(asset_usages).where(asset_usages.c.session_id == "run-2")
            ).fetchall()
        assert row.event == "cited" and row.asset_id == skill
        assert service.get(f"/api/v1/assets/{skill}").json()["usage_count"] == before


def test_agent_fetches_attachment_original_only_when_project_can_see_it(seeded) -> None:
    """Agent 要改附件原件时来取：项目看得到、是知识类、附件属于它才给；README 里写明怎么取。"""
    import base64

    with _service(seeded) as service:
        context = service.app.state.context
        asset_id = _asset_id(context, "oracle-to-pg-cutover")
        body = {
            "username": "chen",
            "engagement_slug": "policy-import",
            "asset_id": asset_id,
            "filename": "baseline.xlsx",
        }
        assert service.post("/internal/v1/agent-attachment", json=body).status_code in (
            401,
            403,
            404,
        )
        got = service.post("/internal/v1/agent-attachment", json=body, headers=HEADERS)
        assert got.status_code == 200, got.text
        content = base64.b64decode(got.json()["content_base64"])
        assert content[:2] == b"PK" and got.json()["bytes"] == len(content)
        missing = service.post(
            "/internal/v1/agent-attachment", json={**body, "filename": "没有.xlsx"}, headers=HEADERS
        )
        assert missing.status_code == 404
        outsider = service.post(
            "/internal/v1/agent-attachment", json={**body, "username": "zhao"}, headers=HEADERS
        )
        assert outsider.status_code == 403

        _link(context, "engagement", "policy-import", "oracle-to-pg-cutover")
        bundle = service.post(
            "/internal/v1/agent-bundle",
            json={"username": "chen", "engagement_slug": "policy-import"},
            headers=HEADERS,
        ).json()
        readme_path = next(
            path for path in bundle["files"] if path.endswith("oracle-to-pg-cutover/README.md")
        )
        readme = base64.b64decode(bundle["files"][readme_path]).decode("utf-8")
        assert f"asset_id={asset_id}，filename=baseline.xlsx" in readme
        assert "fde_asset_attachment_fetch" in readme
