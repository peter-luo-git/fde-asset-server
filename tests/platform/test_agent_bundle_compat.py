"""最新资产包对平台revision/base64合同与旧布局内容的兼容回归。"""

import base64
import hashlib
import re

from fastapi.testclient import TestClient
import pytest
import yaml

from fde_asset.api.app import create_app
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
    assert all(item["name"] != "oracle-to-pg-cutover" for item in payload["assets"])
    assert "/internal/v1/agent-bundle" not in service.app.openapi()["paths"]


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
