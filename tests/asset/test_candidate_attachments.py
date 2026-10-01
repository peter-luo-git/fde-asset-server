"""草稿附件：原件存 BlobStore，提交时写进仓库，不能路径穿越。"""

from __future__ import annotations


import pytest

from fde_asset.modules.harvest import service
from fde_asset.platform.blobs import BlobError, BlobStore
from fde_asset.platform.identity import Principal

CHEN = Principal("chen", display_name="小陈", department_code="data-intel")


@pytest.fixture()
def store(tmp_path):
    return BlobStore(tmp_path / "blobs")


def _draft(context):
    return service.create_draft(
        context.engine,
        CHEN,
        service.CandidateInput(
            kind="Solution",
            name="attach-demo",
            title="带附件的方案",
            scope="department",
            department_code="data-intel",
        ),
    )


def test_attach_then_list_and_download(context, store) -> None:
    candidate = _draft(context)
    result = service.attach_file(
        context.engine,
        candidate["candidate_id"],
        filename="方案评审.md",
        content="# 方案\n正文".encode(),
        store=store,
    )
    assert result["path"] == "attachments/方案评审.md"
    assert result["text_extraction"] == "ok" and result["chars"] > 0

    files = service.get_candidate(context.engine, candidate["candidate_id"])["files"]
    assert files["attachments/方案评审.md"] == service.ATTACHMENT_PLACEHOLDER
    assert service.read_attachment(candidate["candidate_id"], "方案评审.md", store) is not None


def test_attach_rejects_oversized_file(context, store) -> None:
    candidate = _draft(context)
    with pytest.raises(service.HarvestError, match="上限"):
        service.attach_file(
            context.engine,
            candidate["candidate_id"],
            filename="big.bin",
            content=b"x" * 2048,
            store=store,
            size_limit=1024,
        )


def test_detach_removes_file_and_blob(context, store) -> None:
    candidate = _draft(context)
    cid = candidate["candidate_id"]
    service.attach_file(context.engine, cid, filename="a.md", content=b"# a", store=store)
    service.detach_file(context.engine, cid, filename="a.md", store=store)
    files = service.get_candidate(context.engine, cid)["files"]
    assert "attachments/a.md" not in files
    assert service.read_attachment(cid, "a.md", store) is None


def test_path_traversal_is_rejected(store) -> None:
    with pytest.raises(BlobError):
        store.put("c1", "..", b"x")


def test_submit_writes_real_bytes_not_placeholder(context, store) -> None:
    """提交后仓库里必须是原件，不能是 <binary> 占位。"""
    candidate = _draft(context)
    cid = candidate["candidate_id"]
    original = "# 真正的方案正文\n结论在这里".encode()
    service.attach_file(context.engine, cid, filename="原件.md", content=original, store=store)

    files = service.get_candidate(context.engine, cid)["files"]
    files["README.md"] = (
        "# 带附件的方案\n\n## 结论\n给演示客户做批量导入。\n\n"
        "## 客户类型与场景\n中小保险公司的保单迁移。\n\n"
        "## 方案概述\n双写加对账。\n\n## 适用边界\n不适用于实时清算。\n"
    )
    files["asset.yaml"] = "\n".join(
        [
            "apiVersion: fde.asset/v1",
            "kind: Solution",
            "metadata:",
            "  name: attach-demo",
            "  title: 带附件的方案",
            "  summary: 用双写加对账做保单批量导入",
            "  tags: []",
            "spec:",
            "  owner: department:data-intel",
            "  lifecycle: experimental",
            "  industry: []",
            "  applicability:",
            "    suitable: 中小保险公司保单迁移",
            "    notSuitable: 实时清算",
            "  version: 0.1.0",
        ]
    )
    service.update_candidate(context.engine, cid, files)

    target = context.repo_for("department", department_code="data-intel")
    try:
        result = service.submit(
            context.engine,
            CHEN,
            context.repo_port,
            cid,
            target_repo=target,
            blob_store=store,
        )
    except service.HarvestError as exc:  # pragma: no cover - 校验通不过就是真失败
        raise AssertionError(f"草稿未通过格式校验：{exc}") from exc

    written = context.repo_port.read_file(
        target, f"{_prefix(context, cid)}/attachments/原件.md", ref=result["branch"]
    )
    assert written == original


def _prefix(context, candidate_id: str) -> str:
    candidate = service.get_candidate(context.engine, candidate_id)
    return service._target_prefix(
        candidate["kind"], candidate["name"], candidate["scope"], candidate["files"]
    )


def test_update_meta_rewrites_asset_yaml(context) -> None:
    """前端按字段改元数据，不用自己拼 YAML。"""
    import yaml

    candidate = _draft(context)
    cid = candidate["candidate_id"]
    service.update_meta(
        context.engine,
        cid,
        {
            "title": "改过的标题",
            "summary": "一句话结论",
            "tags": ["导入", "对账"],
            "suitable": "批量场景",
            "notSuitable": "实时场景",
            "lifecycle": "stable",
        },
    )
    updated = service.get_candidate(context.engine, cid)
    document = yaml.safe_load(updated["files"]["asset.yaml"])
    assert document["metadata"]["summary"] == "一句话结论"
    assert document["metadata"]["tags"] == ["导入", "对账"]
    assert document["spec"]["applicability"]["notSuitable"] == "实时场景"
    assert document["spec"]["lifecycle"] == "stable"
    # 标题同时更新到候选记录，工作台列表才会跟着变
    assert updated["title"] == "改过的标题"
