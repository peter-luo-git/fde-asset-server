"""[[kind/name]] 资产引用解析：频道消息、工作项描述、资产正文、交付摘要共用。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fde_asset.modules.asset.manifest import KIND_BY_SHORT

REF_PATTERN = re.compile(r"\[\[([a-z]+)/([a-z0-9][a-z0-9-]*)(?:@([0-9]+\.[0-9]+\.[0-9]+))?\]\]")
FENCE_PATTERN = re.compile(r"```.*?```", re.S)
INLINE_CODE_PATTERN = re.compile(r"`[^`\n]*`")


@dataclass(frozen=True)
class AssetRef:
    short_kind: str
    name: str
    version: str = ""

    @property
    def kind(self) -> str:
        return KIND_BY_SHORT.get(self.short_kind, "")

    @property
    def text(self) -> str:
        suffix = f"@{self.version}" if self.version else ""
        return f"[[{self.short_kind}/{self.name}{suffix}]]"


def parse_refs(markdown_text: str) -> list[AssetRef]:
    """解析引用，跳过代码块与行内代码；保持出现顺序并去重。"""
    if not markdown_text:
        return []
    cleaned = FENCE_PATTERN.sub(lambda m: "\n" * m.group(0).count("\n"), markdown_text)
    cleaned = INLINE_CODE_PATTERN.sub("", cleaned)
    seen: set[tuple[str, str, str]] = set()
    refs: list[AssetRef] = []
    for match in REF_PATTERN.finditer(cleaned):
        short_kind, name, version = match.group(1), match.group(2), match.group(3) or ""
        if short_kind not in KIND_BY_SHORT:
            continue
        key = (short_kind, name, version)
        if key in seen:
            continue
        seen.add(key)
        refs.append(AssetRef(short_kind, name, version))
    return refs
