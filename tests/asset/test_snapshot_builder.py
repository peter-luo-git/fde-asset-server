"""快照三层合并、知识索引与复用。"""

from __future__ import annotations

from fde_asset.modules.asset.snapshot import build_snapshot


def _build(context, **kwargs):
    return build_snapshot(
        context.engine, context.repo_port, snapshot_root=context.settings.snapshot_dir, **kwargs
    )


def test_snapshot_has_four_directories(indexed) -> None:
    result = _build(indexed, department_code="data-intel", engagement_slug="policy-import")
    for sub in ("rules", "skills", "sops", "knowledge"):
        assert (result.path / sub).exists()
    assert (result.path / "knowledge" / "INDEX.md").exists()


def test_three_layers_merged(indexed) -> None:
    result = _build(indexed, department_code="data-intel", engagement_slug="policy-import")
    skills = {p.name for p in (result.path / "skills").iterdir()}
    assert skills == {"insurance-policy-import", "pg-vacuum-tuning", "policy-date-parse"}


def test_company_only_snapshot_excludes_other_layers(indexed) -> None:
    result = _build(indexed)
    skills = {p.name for p in (result.path / "skills").iterdir()}
    assert skills == {"insurance-policy-import"}


def test_same_input_is_reused(indexed) -> None:
    first = _build(indexed, department_code="data-intel")
    second = _build(indexed, department_code="data-intel")
    assert first.sha == second.sha and second.reused


def test_invalid_assets_not_in_snapshot(indexed) -> None:
    result = _build(indexed)
    names = {p.name for p in (result.path / "knowledge" / "case").iterdir()}
    assert "no-summary-case" not in names


def test_index_is_truncated_when_over_limit(indexed) -> None:
    result = _build(indexed, index_limit=400)
    text = (result.path / "knowledge" / "INDEX.md").read_text(encoding="utf-8")
    assert result.index_truncated and "裁剪" in text


def test_attachment_text_written_for_agent(indexed) -> None:
    result = _build(indexed)
    extracted = result.path / "knowledge" / "impl" / "oracle-to-pg-cutover" / "attachments-text"
    assert (extracted / "baseline.xlsx.txt").exists()
    assert not list(result.path.rglob("*.xlsx"))  # 二进制不进快照


def test_customer_layer_sits_between_department_and_engagement() -> None:
    """合并顺序：公司 → 部门 → 客户 → 项目，越靠后越具体。"""
    from fde_asset.modules.asset.snapshot import _layer_order

    assert _layer_order("company") < _layer_order("department")
    assert _layer_order("department") < _layer_order("customer")
    assert _layer_order("customer") < _layer_order("engagement")
