"""[[kind/name]] 解析规则。"""

from __future__ import annotations

from fde_asset.platform.refs.wiki import parse_refs


def test_parses_known_kinds() -> None:
    refs = parse_refs("看 [[case/a-b]] 和 [[impl/c-d@1.2.0]] 与 [[sop/e]]")
    assert [(r.short_kind, r.name, r.version) for r in refs] == [
        ("case", "a-b", ""),
        ("impl", "c-d", "1.2.0"),
        ("sop", "e", ""),
    ]
    assert refs[0].kind == "Case" and refs[1].kind == "Implementation"


def test_skips_code_fence_and_inline_code() -> None:
    text = "正文 [[case/keep]]\n```\n[[case/in-fence]]\n```\n`[[case/inline]]`"
    assert [r.name for r in parse_refs(text)] == ["keep"]


def test_unknown_kind_ignored() -> None:
    assert parse_refs("[[ontology/x]]") == []


def test_dedup_keeps_order() -> None:
    refs = parse_refs("[[case/a]] [[skill/b]] [[case/a]]")
    assert [r.name for r in refs] == ["a", "b"]


def test_empty_and_malformed() -> None:
    assert parse_refs("") == []
    assert parse_refs("[[case]] [[/a]] [[Case/A]]") == []
