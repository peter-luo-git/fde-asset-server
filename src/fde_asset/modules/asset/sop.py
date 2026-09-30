"""SOP 三层合并（L1 → L2 → L3）与单步骤摘要抽取。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from fde_asset.core.db import assets

STEP_PATTERN = re.compile(r"^(\d+)[.、]\s*(.+)$")


@dataclass
class SopStep:
    number: int
    title: str
    body: str = ""
    checkpoints: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    source_layer: str = "L1"
    overridden_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "body": self.body,
            "checkpoints": self.checkpoints,
            "outputs": self.outputs,
            "source_layer": self.source_layer,
            "overridden_by": self.overridden_by,
        }


def parse_steps(step_section: str, layer: str = "L1") -> list[SopStep]:
    """解析「步骤」章节：`1. 标题` 起一步，缩进行里 `检查点：`、`产出：` 归到该步。"""
    steps: list[SopStep] = []
    current: SopStep | None = None
    for line in step_section.splitlines():
        stripped = line.strip()
        match = STEP_PATTERN.match(stripped)
        if match:
            current = SopStep(
                number=int(match.group(1)), title=match.group(2).strip(), source_layer=layer
            )
            steps.append(current)
            continue
        if current is None or not stripped:
            continue
        if stripped.startswith(("- 检查点：", "检查点：", "- 检查点:", "检查点:")):
            current.checkpoints.append(stripped.split("：", 1)[-1].split(":", 1)[-1].strip())
        elif stripped.startswith(("- 产出：", "产出：", "- 产出:", "产出:")):
            current.outputs.append(stripped.split("：", 1)[-1].split(":", 1)[-1].strip())
        else:
            current.body = (current.body + "\n" + stripped).strip()
    return steps


class SopCycleError(ValueError):
    pass


def _load_chain(engine: Engine, asset_id: str) -> list[dict[str, Any]]:
    """沿 extends 向上找到 L1，返回自顶向下的链。"""
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current_id = asset_id
    with engine.connect() as conn:
        while current_id:
            if current_id in seen:
                raise SopCycleError(f"SOP extends 成环：{current_id}")
            seen.add(current_id)
            row = conn.execute(select(assets).where(assets.c.asset_id == current_id)).first()
            if row is None:
                break
            spec = json.loads(row.kind_spec_json or "{}")
            chain.append(
                {"row": row, "layer": spec.get("layer", "L1"), "extends": spec.get("extends", "")}
            )
            parent_ref = spec.get("extends", "")
            if not parent_ref:
                break
            parent_name = parent_ref.split("/")[-1]
            parent = conn.execute(
                select(assets).where(assets.c.kind == "Sop", assets.c.name == parent_name)
            ).first()
            current_id = parent.asset_id if parent else ""
    return list(reversed(chain))


def merged_steps(engine: Engine, asset_id: str) -> dict[str, Any]:
    """返回合并后的步骤：子层覆盖同号步骤并标记 overridden_by。"""
    from fde_asset.modules.asset.manifest import split_sections

    chain = _load_chain(engine, asset_id)
    if not chain:
        return {"steps": [], "layers": []}
    merged: dict[int, SopStep] = {}
    layers: list[str] = []
    scope_info: dict[str, Any] = {}
    for node in chain:
        row = node["row"]
        layers.append(f"{node['layer']}:{row.name}")
        sections = split_sections(row.content_text)
        scope_info = {
            "title": row.title,
            "summary": row.summary,
            "applicability": json.loads(row.applicability_json or "{}"),
            "scope_section": sections.get("适用范围", ""),
        }
        for step in parse_steps(sections.get("步骤", ""), layer=node["layer"]):
            if step.number in merged and merged[step.number].source_layer != step.source_layer:
                step.overridden_by = f"{node['layer']}:{row.name}"
            merged[step.number] = step
    return {
        "steps": [merged[k].to_dict() for k in sorted(merged)],
        "layers": layers,
        "info": scope_info,
    }


def step_summary(
    engine: Engine, asset_id: str, step_number: int, *, budget: int = 8192
) -> dict[str, Any]:
    """给提示词用的单步骤摘要：SOP 适用范围 + 该步的操作与检查点，不含全文。"""
    merged = merged_steps(engine, asset_id)
    step = next((s for s in merged["steps"] if s["number"] == step_number), None)
    if step is None:
        return {"found": False, "asset_id": asset_id, "step": step_number}
    info = merged.get("info", {})
    lines = [
        f"# 当前 SOP：{info.get('title', '')}（第 {step_number} 步 / 共 {len(merged['steps'])} 步）",
        f"适用范围：{info.get('applicability', {}).get('suitable', '')}",
        f"不适用：{info.get('applicability', {}).get('notSuitable', '')}",
        "",
        f"## 本步骤：{step['title']}",
        step["body"],
    ]
    if step["checkpoints"]:
        lines.append("检查点（交付摘要里必须逐条回报）：")
        lines.extend(f"- {c}" for c in step["checkpoints"])
    if step["outputs"]:
        lines.append("产出：" + "、".join(step["outputs"]))
    text = "\n".join(line for line in lines if line is not None)
    truncated = len(text.encode("utf-8")) > budget
    if truncated:
        text = (
            text.encode("utf-8")[:budget].decode("utf-8", errors="ignore") + "\n…（已按预算截断）"
        )
    return {
        "found": True,
        "asset_id": asset_id,
        "step": step_number,
        "total_steps": len(merged["steps"]),
        "checkpoints": step["checkpoints"],
        "prompt_md": text,
        "truncated": truncated,
        "layers": merged["layers"],
    }
