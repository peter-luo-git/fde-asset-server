"""敏感信息扫描：规则用数据结构声明，每条规则都有样例单测。

高危阻断提交，中危需要人工确认（规格 F-06.5.1、F-06.5.2）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HIGH = "high"
MEDIUM = "medium"


@dataclass(frozen=True)
class ScanRule:
    code: str
    label: str
    severity: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class ScanHit:
    code: str
    label: str
    severity: str
    file: str
    line: int
    masked: str


BUILTIN_RULES: tuple[ScanRule, ...] = (
    ScanRule("private_key", "私钥文件头", HIGH, re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ScanRule(
        "aws_access_key",
        "云厂商 AccessKey",
        HIGH,
        re.compile(r"\b(AKIA|LTAI|AKID)[0-9A-Za-z]{10,}\b"),
    ),
    ScanRule(
        "jwt",
        "JWT 令牌",
        HIGH,
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b"),
    ),
    ScanRule(
        "secret_assignment",
        "口令或密钥赋值",
        HIGH,
        re.compile(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b\s*[:=]\s*['\"]?[^\s'\"]{6,}"
        ),
    ),
    ScanRule(
        "private_ip",
        "私有网段地址",
        MEDIUM,
        re.compile(r"\b(10\.\d{1,3}|192\.168|172\.(1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
    ),
    ScanRule(
        "internal_domain",
        "内部域名",
        MEDIUM,
        re.compile(r"\b[a-z0-9-]+\.(internal|local|corp)\b", re.I),
    ),
)


def _mask(text: str) -> str:
    stripped = text.strip()
    if len(stripped) <= 8:
        return stripped[0] + "***" if stripped else "***"
    return f"{stripped[:4]}***{stripped[-2:]}"


def scan_text(
    file_name: str,
    text: str,
    *,
    customer_names: tuple[str, ...] = (),
    sensitive_terms: tuple[str, ...] = (),
) -> list[ScanHit]:
    """扫描一段文本。客户名称是高危，公司/客户/项目敏感词是中危。"""
    hits: list[ScanHit] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for rule in BUILTIN_RULES:
            for match in rule.pattern.finditer(line):
                hits.append(
                    ScanHit(
                        rule.code,
                        rule.label,
                        rule.severity,
                        file_name,
                        line_no,
                        _mask(match.group(0)),
                    )
                )
        for name in customer_names:
            if name and name in line:
                hits.append(
                    ScanHit("customer_name", "客户名称", HIGH, file_name, line_no, _mask(name))
                )
        for term in sensitive_terms:
            if term and term in line:
                hits.append(
                    ScanHit("sensitive_term", "敏感词", MEDIUM, file_name, line_no, _mask(term))
                )
    return hits


def scan_files(
    files: dict[str, str],
    *,
    customer_names: tuple[str, ...] = (),
    sensitive_terms: tuple[str, ...] = (),
) -> list[ScanHit]:
    hits: list[ScanHit] = []
    for name, text in files.items():
        hits.extend(
            scan_text(name, text, customer_names=customer_names, sensitive_terms=sensitive_terms)
        )
    return hits


def blocking(hits: list[ScanHit]) -> list[ScanHit]:
    return [h for h in hits if h.severity == HIGH]
