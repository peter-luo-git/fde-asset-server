"""asset.yaml 模型与七种类型的校验规则。"""

from __future__ import annotations

import pytest

from fde_asset.modules.asset.manifest import KIND_RULES, parse_manifest, validate_asset

BASE = """apiVersion: fde.asset/v1
kind: {kind}
metadata:
  name: demo-asset
  title: 演示资产
  summary: 一句话结论
spec:
  owner: department:data-intel
  applicability:
    suitable: 适用场景
    notSuitable: 不适用场景
{extra}"""

EXTRA = {
    "Sop": "  layer: L1\n",
    "Case": "  caseType: fault\n  severity: S2\n",
    "Application": (
        "  sourceType: fcp\n"
        "  maturity: pilot\n"
        "  runtime:\n"
        "    type: url\n"
        "  demo:\n"
        "    network: intranet\n"
        "    url: http://demo.internal/x\n"
    ),
}

SECTIONS = {
    kind: "\n".join(f"## {section}\n内容" for section in rule.required_sections)
    for kind, rule in KIND_RULES.items()
}


@pytest.mark.parametrize("kind", [k for k in KIND_RULES if k != "Rule"])
def test_seven_kinds_parse_and_validate(kind: str) -> None:
    manifest, findings = parse_manifest(BASE.format(kind=kind, extra=EXTRA.get(kind, "")))
    assert findings == []
    assert manifest is not None
    text = SECTIONS[kind] + "\n## 回滚预案\n有"
    parsed = validate_asset(manifest, main_text=text, scope="company", directory_name="demo-asset")
    assert parsed.valid, [f.code for f in parsed.findings]


def test_unknown_api_version_rejected() -> None:
    manifest, findings = parse_manifest(
        BASE.format(kind="Case", extra=EXTRA["Case"]).replace("fde.asset/v1", "fde.asset/v2")
    )
    assert manifest is None
    assert any("apiVersion" in f.message for f in findings)


def test_unknown_kind_reported() -> None:
    manifest, _ = parse_manifest(BASE.format(kind="Ontology", extra=""))
    parsed = validate_asset(
        manifest, main_text="## 结论\n有", scope="company", directory_name="demo-asset"
    )
    assert [f.code for f in parsed.findings] == ["kind_unsupported"]


def test_owner_format_enforced() -> None:
    manifest, findings = parse_manifest(
        BASE.format(kind="Case", extra=EXTRA["Case"]).replace("department:data-intel", "小李")
    )
    assert manifest is None
    assert any("owner" in f.message for f in findings)


def test_directory_name_must_match() -> None:
    manifest, _ = parse_manifest(BASE.format(kind="Case", extra=EXTRA["Case"]))
    parsed = validate_asset(
        manifest, main_text=SECTIONS["Case"], scope="company", directory_name="other"
    )
    assert "name_mismatch" in [f.code for f in parsed.findings]


def test_summary_and_applicability_required() -> None:
    raw = BASE.format(kind="Case", extra=EXTRA["Case"]).replace("  summary: 一句话结论\n", "")
    raw = raw.replace("    suitable: 适用场景", '    suitable: ""')
    manifest, _ = parse_manifest(raw)
    parsed = validate_asset(
        manifest, main_text=SECTIONS["Case"], scope="company", directory_name="demo-asset"
    )
    codes = [f.code for f in parsed.findings]
    assert "summary_missing" in codes and "applicability_missing" in codes


def test_company_implementation_requires_rollback() -> None:
    manifest, _ = parse_manifest(BASE.format(kind="Implementation", extra=""))
    company = validate_asset(
        manifest, main_text=SECTIONS["Implementation"], scope="company", directory_name="demo-asset"
    )
    engagement = validate_asset(
        manifest,
        main_text=SECTIONS["Implementation"],
        scope="engagement",
        directory_name="demo-asset",
    )
    assert "section_missing_company" in [f.code for f in company.findings]
    assert engagement.valid


def test_sop_layer_requires_extends() -> None:
    manifest, _ = parse_manifest(BASE.format(kind="Sop", extra="  layer: L2\n"))
    parsed = validate_asset(
        manifest, main_text=SECTIONS["Sop"], scope="company", directory_name="demo-asset"
    )
    assert "sop_extends_missing" in [f.code for f in parsed.findings]


def test_case_type_and_severity() -> None:
    manifest, _ = parse_manifest(BASE.format(kind="Case", extra="  caseType: unknown\n"))
    parsed = validate_asset(
        manifest, main_text=SECTIONS["Case"], scope="company", directory_name="demo-asset"
    )
    codes = [f.code for f in parsed.findings]
    assert "case_type_invalid" in codes


def test_deprecated_requires_replacement() -> None:
    raw = BASE.format(kind="Case", extra=EXTRA["Case"]).replace(
        "  owner: department:data-intel", "  owner: department:data-intel\n  lifecycle: deprecated"
    )
    manifest, _ = parse_manifest(raw)
    parsed = validate_asset(
        manifest, main_text=SECTIONS["Case"], scope="company", directory_name="demo-asset"
    )
    assert "replaced_by_missing" in [f.code for f in parsed.findings]
