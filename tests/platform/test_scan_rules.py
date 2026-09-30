"""敏感信息扫描：每条规则一个命中样例与一个不该命中的样例。"""

from __future__ import annotations

import pytest

from fde_asset.platform.scan.rules import BUILTIN_RULES, blocking, scan_files, scan_text

SAMPLES = {
    "private_key": ("-----BEGIN RSA PRIVATE KEY-----", "这里讲私钥应该怎么保管"),
    "aws_access_key": ("AKIAIOSFODNN7EXAMPLE", "AKIA 是前缀的说明"),
    "jwt": ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcd", "JWT 是一种令牌格式"),
    "secret_assignment": ("password = hunter2000", "password 字段需要脱敏"),
    "private_ip": ("连接 10.1.2.3 即可", "版本号 10.1.2 不是地址"),
    "internal_domain": ("db.internal 解析失败", "internal 一词本身不算"),
}


@pytest.mark.parametrize("rule", BUILTIN_RULES, ids=lambda r: r.code)
def test_rule_hits_and_misses(rule) -> None:
    hit_text, miss_text = SAMPLES[rule.code]
    hits = [h.code for h in scan_text("f.md", hit_text)]
    assert rule.code in hits
    assert rule.code not in [h.code for h in scan_text("f.md", miss_text)]


def test_customer_name_is_high_risk() -> None:
    hits = scan_text("f.md", "现场来自测试保险公司", customer_names=("测试保险公司",))
    assert hits[0].severity == "high" and hits[0].code == "customer_name"


def test_sensitive_term_is_medium() -> None:
    hits = scan_text("f.md", "见内部代号X", sensitive_terms=("内部代号X",))
    assert hits[0].severity == "medium"


def test_masked_output_does_not_leak() -> None:
    hits = scan_text("f.md", "password = supersecretvalue")
    assert "supersecretvalue" not in hits[0].masked


def test_blocking_only_counts_high() -> None:
    hits = scan_files({"a.md": "10.1.2.3", "b.md": "-----BEGIN PRIVATE KEY-----"})
    assert len(hits) == 2 and len(blocking(hits)) == 1


def test_line_numbers_reported() -> None:
    hits = scan_text("a.md", "第一行\n第二行 password = abcdefg")
    assert hits[0].line == 2
