"""索引流水线：三类仓库、无效资产可见、软删除、重建幂等。"""

from __future__ import annotations

from sqlalchemy import select

from fde_asset.core.db import asset_index_findings, asset_relations, assets
from fde_asset.modules.asset.indexer import discover, index_all, index_repository
from fde_asset.platform.repo.ports import RepoRef


ONE = "11111111-1111-4111-8111-111111111111"
TWO = "22222222-2222-4222-8222-222222222222"


def test_discover_groups_files_by_asset_directory() -> None:
    found = discover(
        [
            f"assets/{ONE}/SKILL.md",
            f"assets/{ONE}/asset.yaml",
            f"assets/{ONE}/scripts/run.sh",
            f"assets/{TWO}/README.md",
            "assets/stray-file.md",
            "README.md",
        ]
    )
    assert {item.asset_id: set(item.files) for item in found} == {
        ONE: {"SKILL.md", "asset.yaml", "scripts/run.sh"},
        TWO: {"README.md"},
    }
    assert found[0].directory == f"assets/{ONE}"


def test_legacy_layout_is_converted_with_stable_ids(context) -> None:
    """旧的分层布局转过来以后，编号仍是按层级和名字算出来的那个，层级写进了 asset.yaml。"""
    import yaml

    from fde_asset.modules.asset.indexer import legacy_asset_id
    from fde_asset.modules.asset.legacy_layout import convert_legacy

    legacy = RepoRef("policy-import", "engagement", engagement_slug="policy-import")
    files = {
        ".agents/skills/b/SKILL.md": b"# b\n",
        ".agents/skills/b/asset.yaml": (
            b"apiVersion: fde.asset/v1\nkind: Skill\nmetadata:\n  name: b\n  title: B\n"
            b"spec:\n  owner: user:chen\n"
        ),
        ".agents/skills/b/attachments/a.txt": b"hello",
        "src/main.py": b"print(1)\n",
    }
    converted = convert_legacy(legacy, files, context.file_store)
    asset_id = legacy_asset_id("engagement", "", "policy-import", "Skill", "b")
    assert set(converted) == {asset_id}
    members = converted[asset_id]
    assert set(members) == {f"assets/{asset_id}/SKILL.md", f"assets/{asset_id}/asset.yaml"}
    document = yaml.safe_load(members[f"assets/{asset_id}/asset.yaml"])
    assert document["metadata"]["id"] == asset_id
    assert document["spec"]["visibility"] == ["engagement:policy-import"]
    (attachment,) = document["spec"]["attachments"]
    assert attachment["name"] == "a.txt"
    assert context.file_store.get(attachment["sha256"]) == b"hello"


def test_index_single_repository_covers_all_layers(context) -> None:
    (report,) = index_all(
        context.engine, context.repo_port, context.repos(), file_store=context.file_store
    )
    assert report.repo == "assets"
    assert report.invalid == 2
    with context.engine.connect() as conn:
        by_scope: dict[str, int] = {}
        for row in conn.execute(
            select(assets.c.scope).where(assets.c.valid.is_(True), assets.c.deleted_at.is_(None))
        ):
            by_scope[row.scope] = by_scope.get(row.scope, 0) + 1
    assert set(by_scope) == {"company", "department", "customer", "engagement"}
    assert sum(by_scope.values()) == report.indexed


def test_invalid_assets_are_visible_with_reason(context) -> None:
    index_all(context.engine, context.repo_port, context.repos())
    with context.engine.connect() as conn:
        rows = conn.execute(select(asset_index_findings)).fetchall()
    reasons = {(row.name, row.code) for row in rows}
    assert ("no-summary-case", "summary_missing") in reasons
    assert ("legacy-rollout-no-rollback", "section_missing_company") in reasons


def test_relations_from_metadata_and_body(context) -> None:
    index_all(context.engine, context.repo_port, context.repos())
    with context.engine.connect() as conn:
        case_id = conn.execute(
            select(assets.c.asset_id).where(assets.c.name == "oracle-to-pg-sequence-gap")
        ).scalar_one()
        targets = (
            conn.execute(
                select(asset_relations.c.to_ref).where(asset_relations.c.from_asset_id == case_id)
            )
            .scalars()
            .all()
        )
    assert "impl/oracle-to-pg-cutover" in targets


def test_reindex_is_idempotent(context) -> None:
    first = index_all(context.engine, context.repo_port, context.repos())
    second = index_all(context.engine, context.repo_port, context.repos())
    assert [r.indexed for r in first] == [r.indexed for r in second]
    with context.engine.connect() as conn:
        total = conn.execute(select(assets)).fetchall()
    assert len({row.asset_id for row in total}) == len(total)


def test_removed_asset_is_soft_deleted(context) -> None:
    repo = RepoRef()
    index_repository(context.engine, context.repo_port, repo)
    with context.engine.connect() as conn:
        victim = conn.execute(
            select(assets).where(assets.c.name == "oracle-to-pg-sequence-gap")
        ).first()
    remaining = {
        path: context.repo_port.read_file(repo, path)
        for path in [e.path for e in context.repo_port.list_tree(repo)]
        if not path.startswith(victim.path + "/")
    }
    import shutil

    shutil.rmtree(context.repo_port.path_of(repo))
    context.repo_port.commit_files(repo, "main", remaining, "chore: 删除一个资产")
    report = index_repository(context.engine, context.repo_port, repo)
    assert report.removed == 1
    with context.engine.connect() as conn:
        row = conn.execute(
            select(assets).where(assets.c.name == "oracle-to-pg-sequence-gap")
        ).first()
    assert row.deleted_at is not None


def test_same_owner_same_name_is_flagged(context) -> None:
    """同一个负责人名下同类型不重名：仓库里真出现了，后一份标成无效并说明原因。"""
    import yaml

    repo = RepoRef()
    index_repository(context.engine, context.repo_port, repo)
    with context.engine.connect() as conn:
        original = conn.execute(
            select(assets).where(assets.c.name == "oracle-to-pg-sequence-gap")
        ).first()
    twin = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    files = {}
    for entry in context.repo_port.list_tree(repo, original.path):
        relative = entry.path[len(original.path) + 1 :]
        content = context.repo_port.read_file(repo, entry.path)
        if relative == "asset.yaml":
            document = yaml.safe_load(content)
            document["metadata"]["id"] = twin
            content = yaml.safe_dump(document, allow_unicode=True, sort_keys=False).encode()
        files[f"assets/{twin}/{relative}"] = content
    context.repo_port.commit_files(repo, "main", files, "chore: 重名的一份")
    index_repository(context.engine, context.repo_port, repo)
    with context.engine.connect() as conn:
        codes = {
            row.code
            for row in conn.execute(
                select(asset_index_findings).where(asset_index_findings.c.path == f"assets/{twin}")
            )
        }
        row = conn.execute(select(assets).where(assets.c.asset_id == twin)).first()
    assert "name_duplicate" in codes
    assert row.valid is False


def test_attachment_text_is_indexed(context) -> None:
    index_all(context.engine, context.repo_port, context.repos(), file_store=context.file_store)
    with context.engine.connect() as conn:
        row = conn.execute(select(assets).where(assets.c.name == "oracle-to-pg-cutover")).first()
    assert "shared_buffers" in row.content_text  # 来自 xlsx 附件
    assert '"text_extraction": "ok"' in row.attachments_json


def test_reindex_without_changes_keeps_updated_at(indexed) -> None:
    """服务每次启动都会重建索引；内容没变，"最近更新"不该跟着变成重启时间。"""
    from sqlalchemy import select

    from fde_asset.core.db import assets
    from fde_asset.modules.asset.indexer import index_all

    def stamps() -> dict[str, tuple]:
        with indexed.engine.connect() as conn:
            rows = conn.execute(select(assets.c.asset_id, assets.c.created_at, assets.c.updated_at))
            return {row.asset_id: (row.created_at, row.updated_at) for row in rows}

    before = stamps()
    index_all(indexed.engine, indexed.repo_port, indexed.repos(), file_store=indexed.file_store)
    assert stamps() == before
