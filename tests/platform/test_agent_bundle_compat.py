"""最新资产包对平台revision/base64合同与旧布局内容的兼容回归。"""

import base64
import hashlib
import re

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
import yaml

from fde_asset.api.app import create_app
from fde_asset.core.db import assets, target_assets
from fde_asset.modules.asset.indexer import index_all, legacy_asset_id
from fde_asset.modules.asset.legacy_layout import convert_legacy
from fde_asset.platform.files import FileStore
from fde_asset.platform.repo.ports import RepoRef
from scripts.seed_assets import company_files

KEY = "controlled-asset-compat-service-key"


@pytest.fixture()
def service(seeded):
    with TestClient(create_app(seeded.model_copy(update={"server_service_key": KEY}))) as client:
        ctx = client.app.state.context
        index_all(ctx.engine, ctx.repo_port, ctx.repos(), file_store=ctx.file_store)
        yield client


def bundle(client, **body):
    return client.post("/internal/v1/agent-bundle", json=body, headers={"X-FDE-Service-Key": KEY})


def test_http_bundle_preserves_rules_ids_and_pinned_revision_after_content_changes(service):
    response = bundle(service, username="chen", engagement_slug="policy-import")
    assert response.status_code == 200
    payload = response.json()
    assert re.fullmatch(r"[0-9a-f]{40}", payload["revision"])
    decoded = {
        path: base64.b64decode(data, validate=True) for path, data in payload["files"].items()
    }
    assert len(decoded) <= 4096 and sum(map(len, decoded.values())) < 64 * 1024 * 1024
    original = company_files()
    for path in ("rules/00-security-redlines.md", "rules/10-engineering.md"):
        assert decoded[path] == original[path], "必读规范全文和排序文件名不可在转换时丢失"
    catalog = service.get(
        "/internal/v1/agent-catalog",
        params={"username": "chen"},
        headers={"X-FDE-Service-Key": KEY},
    ).json()["items"]
    skill = next(item for item in catalog if item["name"] == "insurance-policy-import")
    assert skill["asset_id"] == legacy_asset_id("company", "", "", "Skill", skill["name"])
    assert (
        hashlib.sha256(decoded["skills/insurance-policy-import/SKILL.md"]).hexdigest()
        == skill["content_sha256"]
    )
    assert len(decoded["skills/insurance-policy-import/SKILL.md"]) == skill["byte_size"]

    # 修改仅fixture的独立临时Git资产库；历史bundle复读不得随当前内容变化。
    rule = next(item for item in catalog if item["name"] == "00-security-redlines")
    ctx = service.app.state.context
    ctx.repo_port.commit_files(
        RepoRef(name=rule["repo"]),
        "main",
        {rule["path"] + "/RULE.md": b"# Controlled changed rule\n"},
        "test: isolated fixture content change",
    )
    index_all(ctx.engine, ctx.repo_port, ctx.repos(), file_store=ctx.file_store)
    latest = bundle(service, username="chen", engagement_slug="policy-import").json()
    assert latest["revision"] != payload["revision"]
    assert bundle(service, revision=payload["revision"]).json() == payload
    assert bundle(service, revision="../not-a-revision").status_code == 404


def test_http_bundle_service_auth_and_project_visibility_are_distinct(service):
    body = {"username": "chen", "engagement_slug": "policy-import"}
    assert service.post("/internal/v1/agent-bundle", json=body).status_code == 404
    assert (
        service.post(
            "/internal/v1/agent-bundle", json=body, headers={"X-FDE-Service-Key": "wrong"}
        ).status_code
        == 404
    )
    assert bundle(service, username="zhao", engagement_slug="policy-import").status_code == 403
    assert bundle(service, username="unknown", engagement_slug="policy-import").status_code == 404
    assert bundle(service, username="chen").status_code == 422
    payload = bundle(service, **body).json()
    assert "skills/policy-date-parse/SKILL.md" in payload["files"]
    # 2026-10-13 起方案、实施、经验、流程也进资产包（以前只有规范、技能、问题案例）；
    # 仍然不带的是应用——它是拿来跑的，不是给 Agent 读的
    assert any(item["name"] == "oracle-to-pg-cutover" for item in payload["assets"])
    assert all(item["kind"] != "Application" for item in payload["assets"])
    assert {path.split("/")[0] for path in payload["files"]} <= {"rules", "skills", "knowledge"}
    assert "/internal/v1/agent-bundle" not in service.app.openapi()["paths"]


def test_known_revision_skips_reassembly_until_selection_or_content_changes(service):
    """平台每轮带上上次的版本号：没变只回一句没变；内容、关联、版本号任何一样不对都给完整的。"""
    body = {"username": "chen", "engagement_slug": "policy-import", "agent_key": "coder-default"}
    first = bundle(service, **body).json()
    same = bundle(service, **body, known_revision=first["revision"]).json()
    assert same == {"revision": first["revision"], "unchanged": True}

    # 乱填的、别人的版本号：照常拼一份完整的
    for wrong in ("0" * 40, "not-a-revision"):
        full = bundle(service, **body, known_revision=wrong).json()
        assert full["revision"] == first["revision"] and full["files"] == first["files"]
    # 换一个 Agent，同一个版本号也不能当成没变
    other = bundle(
        service,
        username="chen",
        engagement_slug="policy-import",
        agent_key="poc-rapid",
        known_revision=first["revision"],
    ).json()
    assert "files" in other
    # 不在项目里的人带上版本号也照样拒绝
    assert (
        bundle(
            service,
            username="zhao",
            engagement_slug="policy-import",
            known_revision=first["revision"],
        ).status_code
        == 403
    )

    # 给这个 Agent 关联一份技能：选中的资产变了
    ctx = service.app.state.context
    with ctx.engine.begin() as conn:
        asset_id = conn.execute(
            select(assets.c.asset_id).where(assets.c.name == "policy-import-poc-retro")
        ).scalar_one()
        conn.execute(
            target_assets.insert().values(
                target_type="agent",
                target_id="coder-default",
                asset_id=asset_id,
                source="manual",
                created_by="admin",
            )
        )
    linked = bundle(service, **body, known_revision=first["revision"]).json()
    assert "files" in linked and linked["revision"] != first["revision"]
    assert (
        bundle(service, **body, known_revision=linked["revision"]).json().get("unchanged") is True
    )

    # 绕过资产中心直接改仓库里的文件（库里的记录没变）：仓库提交变了，也要给新的
    catalog = service.get(
        "/internal/v1/agent-catalog",
        params={"username": "chen"},
        headers={"X-FDE-Service-Key": KEY},
    ).json()["items"]
    rule = next(item for item in catalog if item["name"] == "00-security-redlines")
    ctx.repo_port.commit_files(
        RepoRef(name=rule["repo"]),
        "main",
        {rule["path"] + "/RULE.md": b"# Changed outside the index\n"},
        "test: change without reindex",
    )
    changed = bundle(service, **body, known_revision=linked["revision"]).json()
    assert changed["revision"] != linked["revision"]
    assert base64.b64decode(changed["files"]["rules/00-security-redlines.md"]) == (
        b"# Changed outside the index\n"
    )


def test_legacy_conversion_preserves_rule_bytes_id_visibility_and_attachment_storage(tmp_path):
    store = FileStore(tmp_path / "isolated-files")
    text = "# 旧规范\n\n必读完整正文：不可删除。\n".encode()
    # 使用有效旧Skill manifest，文件转换必须保留旧ID、正文与附件内容。
    manifest = yaml.safe_dump(
        {
            "apiVersion": "fde.asset/v1",
            "kind": "Skill",
            "metadata": {"name": "compat-skill"},
            "spec": {"owner": "user:chen"},
        }
    ).encode()
    converted = convert_legacy(
        RepoRef(name="company-assets", scope="company"),
        {
            "rules/00-compat.md": text,
            "skills/compat-skill/asset.yaml": manifest,
            "skills/compat-skill/SKILL.md": b"# Legacy skill\n",
            "skills/compat-skill/attachments/example.bin": b"controlled-attachment-bytes",
        },
        store,
    )
    rule_id = legacy_asset_id("company", "", "", "Rule", "00-compat")
    assert converted[rule_id][f"assets/{rule_id}/RULE.md"] == text
    rule = yaml.safe_load(converted[rule_id][f"assets/{rule_id}/asset.yaml"])
    assert rule["metadata"]["id"] == rule_id and rule["spec"]["visibility"] == ["company"]
    skill_id = legacy_asset_id("company", "", "", "Skill", "compat-skill")
    assert converted[skill_id][f"assets/{skill_id}/SKILL.md"] == b"# Legacy skill\n"
    skill = yaml.safe_load(converted[skill_id][f"assets/{skill_id}/asset.yaml"])
    attachment = skill["spec"]["attachments"][0]
    assert store.get(attachment["sha256"]) == b"controlled-attachment-bytes"
    assert attachment["bytes"] == len(b"controlled-attachment-bytes")
    assert not any("/attachments/" in path for path in converted[skill_id])
