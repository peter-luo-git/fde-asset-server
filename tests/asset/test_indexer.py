"""索引流水线：三类仓库、无效资产可见、软删除、重建幂等。"""

from __future__ import annotations

from sqlalchemy import select

from fde_asset.core.db import asset_index_findings, asset_relations, assets
from fde_asset.modules.asset.indexer import discover, index_all, index_repository
from fde_asset.platform.repo.ports import RepoRef


def test_discover_company_and_engagement_layout() -> None:
    company = discover(
        RepoRef("company-assets", "company"),
        [
            "rules/00-x.md",
            "skills/a/SKILL.md",
            "skills/a/asset.yaml",
            "knowledge/cases/c/README.md",
            "sops/l2/s/SOP.md",
            "README.md",
        ],
    )
    assert {(d.kind, d.name) for d in company} == {
        ("Rule", "00-x"),
        ("Skill", "a"),
        ("Case", "c"),
        ("Sop", "s"),
    }

    engagement = discover(
        RepoRef("policy-import", "engagement", engagement_slug="policy-import"),
        [
            ".agents/skills/b/SKILL.md",
            ".fde/assets/cases/d/README.md",
            "src/main.py",
        ],
    )
    assert {(d.kind, d.name) for d in engagement} == {("Skill", "b"), ("Case", "d")}


def test_index_three_repositories(context) -> None:
    reports = {r.repo: r for r in index_all(context.engine, context.repo_port, context.repos())}
    assert reports["company-assets"].indexed == 9
    assert reports["company-assets"].invalid == 2
    # 部门仓库里除了两个知识资产，还有两个应用资产（内网已部署的 + 本地容器化的）
    assert reports["dept-data-intel-assets"].indexed == 4
    assert reports["policy-import"].indexed == 2


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
    repo = RepoRef("company-assets", "company")
    index_repository(context.engine, context.repo_port, repo)
    remaining = {
        path: context.repo_port.read_file(repo, path)
        for path in [e.path for e in context.repo_port.list_tree(repo)]
        if not path.startswith("knowledge/cases/oracle-to-pg-sequence-gap/")
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


def test_attachment_text_is_indexed(context) -> None:
    index_all(context.engine, context.repo_port, context.repos())
    with context.engine.connect() as conn:
        row = conn.execute(select(assets).where(assets.c.name == "oracle-to-pg-cutover")).first()
    assert "shared_buffers" in row.content_text  # 来自 xlsx 附件
    assert '"text_extraction": "ok"' in row.attachments_json
