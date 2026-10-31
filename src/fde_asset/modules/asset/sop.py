"""SOP：步骤解析、三层合并（L1 → L2 → L3）、编写检查，以及给 Agent 的当前步骤说明。

SOP 只用来按步骤执行任务，不当参考资料（2026-10-25 定）。步骤的写法：

    1. 调研与方案确认
       梳理业务现状与目标，输出方案初稿。
       - 检查点[交付物]：`docs/方案.md` 已提交，含「范围」「风险」两节
       - 检查点[确认:客户]：客户书面确认方案范围
       - 检查点[命令]：`pytest -q` 全部通过
       - 检查点[判断]：主要风险都有应对措施
       - 产出：方案文档
       - 技能：requirement-survey
       - 推进：自动

检查点分四类，各由合适的一方判定：命令（平台执行）、交付物（平台核对存在和章节，内容由人看）、
确认（只能由人确认）、判断（由人判断）。不写类型的按"确认"处理，最保守。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.engine import Engine

from fde_asset.core.db import assets

STEP_PATTERN = re.compile(r"^(\d+)[.、]\s*(.+)$")
#: `- 检查点[类型]：内容`，类型可省略；方括号全角半角都认，冒号也一样
CHECK_PATTERN = re.compile(r"^-?\s*检查点\s*(?:[\[【]([^\]】]*)[\]】])?\s*[：:]\s*(.*)$")
FIELD_PATTERN = re.compile(r"^-?\s*(产出|技能|推进)\s*[：:]\s*(.*)$")
#: 看着像检查点、但没按格式写（解析不出来）的行
LOOKS_LIKE_CHECK = re.compile(r"^-?\s*(检查点|检查项|检查|验收点)")
BACKTICK = re.compile(r"`([^`]+)`")
QUOTED = re.compile(r"「([^」]+)」")
PATH_LIKE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9]{1,8})(?![\w/])")

#: 检查点类型：页面和提示词里显示的名字
CHECK_TYPES = {"command": "命令", "artifact": "交付物", "confirm": "确认", "judgment": "判断"}
_TYPE_ALIASES = {
    "命令": "command",
    "command": "command",
    "交付物": "artifact",
    "产出物": "artifact",
    "artifact": "artifact",
    "确认": "confirm",
    "人工确认": "confirm",
    "confirm": "confirm",
    "判断": "judgment",
    "专业判断": "judgment",
    "judgment": "judgment",
}
#: 平台自己能核对的检查点类型；只有一步里全是这两类，才可能自动进入下一步
MACHINE_TYPES = {"command", "artifact"}
#: 没法判定的字眼：出现在检查点里就提醒作者改写，或者标成"判断"类
VAGUE_WORDS = (
    "合理",
    "完善",
    "充分",
    "尽量",
    "尽可能",
    "适当",
    "良好",
    "较好",
    "基本",
    "足够",
    "到位",
)


@dataclass
class SopCheck:
    index: int
    text: str
    type: str = "confirm"
    role: str = ""
    #: 写了类型标记没有；没写的按"确认"处理，编写检查会提醒
    tagged: bool = False
    #: 原样写的类型标记（认不出的类型留着给编写检查看）
    raw_tag: str = ""
    command: str = ""
    path: str = ""
    sections: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "text": self.text,
            "type": self.type,
            "type_label": label_of(self.type, self.role),
            "role": self.role,
            "tagged": self.tagged,
            "command": self.command,
            "path": self.path,
            "sections": self.sections,
        }


def label_of(check_type: str, role: str = "") -> str:
    if check_type == "confirm":
        return f"需{role}确认" if role else "需人工确认"
    return CHECK_TYPES.get(check_type, check_type)


def parse_check(index: int, tag: str | None, text: str) -> SopCheck:
    tag = (tag or "").strip()
    parts = re.split(r"[:：]", tag, maxsplit=1)
    name = parts[0].strip()
    role = parts[1].strip() if len(parts) > 1 else ""
    check_type = _TYPE_ALIASES.get(name.lower() if name.isascii() else name, "")
    check = SopCheck(index=index, text=text.strip(), tagged=bool(tag), raw_tag=tag)
    check.type = check_type or "confirm"
    if check.type == "confirm":
        check.role = role
    code = BACKTICK.findall(text)
    if check.type == "command" and code:
        check.command = code[0].strip()
    if check.type == "artifact":
        candidates = code or PATH_LIKE.findall(text)
        check.path = candidates[0].strip() if candidates else ""
        check.sections = [item.strip() for item in QUOTED.findall(text) if item.strip()]
    return check


@dataclass
class SopStep:
    number: int
    title: str
    body: str = ""
    checks: list[SopCheck] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    auto: bool = False
    source_layer: str = "L1"
    overridden_by: str = ""
    #: 编写检查用：这一步里看着像检查点却没按格式写的行
    stray: list[str] = field(default_factory=list)

    @property
    def checkpoints(self) -> list[str]:
        return [check.text for check in self.checks]

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "body": self.body,
            "checkpoints": self.checkpoints,
            "checks": [check.to_dict() for check in self.checks],
            "outputs": self.outputs,
            "skills": self.skills,
            "auto": self.auto,
            "source_layer": self.source_layer,
            "overridden_by": self.overridden_by,
        }


def _split_list(value: str) -> list[str]:
    return [item.strip(" `") for item in re.split(r"[、，,；;\s]+", value) if item.strip(" `")]


def parse_steps(step_section: str, layer: str = "L1") -> list[SopStep]:
    """解析「步骤」章节：`1. 标题` 起一步，后面的 `检查点`、`产出`、`技能`、`推进` 行归到这一步。"""
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
        check = CHECK_PATTERN.match(stripped)
        if check:
            current.checks.append(parse_check(len(current.checks), check.group(1), check.group(2)))
            continue
        extra = FIELD_PATTERN.match(stripped)
        if extra:
            name, value = extra.group(1), extra.group(2).strip()
            if name == "产出":
                current.outputs.append(value)
            elif name == "技能":
                current.skills.extend(_split_list(value))
            elif name == "推进":
                current.auto = value.startswith("自动")
            continue
        if LOOKS_LIKE_CHECK.match(stripped):
            current.stray.append(stripped)
        current.body = (current.body + "\n" + stripped).strip()
    return steps


class SopCycleError(ValueError):
    pass


def _spec(row: Any) -> dict[str, Any]:
    return json.loads(row.kind_spec_json or "{}")


def _find_parent(conn: Any, parent_ref: str) -> Any:
    """`extends` 写的是上层流程的名字（可以带前缀，如 l1/delivery-standard），按最后一段找。"""
    parent_name = parent_ref.split("/")[-1].strip()
    if not parent_name:
        return None
    return conn.execute(
        select(assets).where(
            assets.c.kind == "Sop", assets.c.name == parent_name, assets.c.deleted_at.is_(None)
        )
    ).first()


def _load_chain(engine: Engine, asset_id: str = "", parent_ref: str = "") -> list[dict[str, Any]]:
    """从这份流程（或只给上层名字时从上层）沿 extends 向上找到 L1，返回自顶向下的链。"""
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    with engine.connect() as conn:
        if asset_id:
            row = conn.execute(select(assets).where(assets.c.asset_id == asset_id)).first()
        else:
            row = _find_parent(conn, parent_ref)
        while row is not None:
            if row.asset_id in seen:
                raise SopCycleError(f"SOP extends 成环：{row.asset_id}")
            seen.add(row.asset_id)
            spec = _spec(row)
            chain.append({"row": row, "layer": spec.get("layer", "L1")})
            parent = spec.get("extends", "")
            row = _find_parent(conn, parent) if parent else None
    return list(reversed(chain))


def _merge(nodes: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """nodes：自顶向下，每个有 layer、name、title、summary、applicability、content_text。"""
    from fde_asset.modules.asset.manifest import split_sections

    merged: dict[int, SopStep] = {}
    layers: list[str] = []
    info: dict[str, Any] = {}
    for node in nodes:
        layers.append(f"{node['layer']}:{node['name']}")
        sections = split_sections(node["content_text"])
        info = {
            "title": node["title"],
            "summary": node.get("summary", ""),
            "applicability": node.get("applicability", {}),
            "scope_section": sections.get("适用范围", ""),
        }
        for step in parse_steps(sections.get("步骤", ""), layer=node["layer"]):
            if step.number in merged and merged[step.number].source_layer != step.source_layer:
                step.overridden_by = f"{node['layer']}:{node['name']}"
            merged[step.number] = step
    return {"steps": [merged[k] for k in sorted(merged)], "layers": layers, "info": info}


def _node(entry: dict[str, Any]) -> dict[str, Any]:
    row = entry["row"]
    return {
        "layer": entry["layer"],
        "name": row.name,
        "title": row.title,
        "summary": row.summary,
        "applicability": json.loads(row.applicability_json or "{}"),
        "content_text": row.content_text,
    }


def merged_steps(engine: Engine, asset_id: str) -> dict[str, Any]:
    """返回合并后的步骤：子层覆盖同号步骤并标记 overridden_by。"""
    chain = _load_chain(engine, asset_id)
    if not chain:
        return {"steps": [], "layers": []}
    merged = _merge(_node(entry) for entry in chain)
    return {**merged, "steps": [step.to_dict() for step in merged["steps"]]}


def preview(
    engine: Engine, *, content: str, layer: str, extends: str = "", name: str = "", title: str = ""
) -> dict[str, Any]:
    """草稿页实时预览：把正在写的这份和它的上层合起来，并给出编写检查的提示。"""
    chain = _load_chain(engine, parent_ref=extends) if extends else []
    nodes = [_node(entry) for entry in chain]
    nodes.append(
        {
            "layer": layer or "L1",
            "name": name or "（本流程）",
            "title": title,
            "content_text": content,
        }
    )
    merged = _merge(nodes)
    findings = lint(content, layer=layer, extends=extends, parent_found=bool(chain) or not extends)
    parent_numbers = {step.number for step in _merge(nodes[:-1])["steps"]} if nodes[:-1] else set()
    from fde_asset.modules.asset.manifest import split_sections

    own = parse_steps(split_sections(content).get("步骤", ""), layer=layer or "L1")
    for step in own:
        if step.number in parent_numbers:
            findings.append(
                {
                    "level": "info",
                    "step": step.number,
                    "message": f"第 {step.number} 步会整步替换上层的同号步骤（上层这一步的检查点不再生效）",
                }
            )
    return {
        "steps": [step.to_dict() for step in merged["steps"]],
        "layers": merged["layers"],
        "findings": findings,
        "parent_found": bool(chain) or not extends,
    }


def lint(
    content: str, *, layer: str = "L1", extends: str = "", parent_found: bool = True
) -> list[dict[str, Any]]:
    """编写检查：解析不出步骤、检查点没写类型或写得没法判定，都在这里提醒。

    level：error 必须改（不改流程没法用），warning 建议改，info 只是告诉你会怎样。
    """
    from fde_asset.modules.asset.manifest import split_sections

    findings: list[dict[str, Any]] = []

    def add(level: str, message: str, step: int | None = None) -> None:
        findings.append({"level": level, "step": step, "message": message})

    if layer in {"L2", "L3"} and not extends:
        add("error", f"{layer} 流程要选上层流程（L2 继承一份 L1，L3 继承一份 L2）")
    elif extends and not parent_found:
        add("error", f"找不到上层流程「{extends}」")
    sections = split_sections(content)
    if "步骤" not in sections:
        add("error", "没有「## 步骤」这一节；步骤要写在这个标题下面")
        return findings
    steps = parse_steps(sections.get("步骤", ""), layer=layer)
    if not steps:
        add("error", "「## 步骤」下面没认出步骤：每一步要顶格写成「1. 步骤标题」")
        return findings
    seen: set[int] = set()
    for step in steps:
        if step.number in seen:
            add("warning", f"第 {step.number} 步写了两次，后面那次会盖掉前面的", step.number)
        seen.add(step.number)
        for line in step.stray:
            add(
                "warning",
                f"这一行看着像检查点但没认出来：「{line[:40]}」；格式是「- 检查点[类型]：内容」",
                step.number,
            )
        if not step.checks:
            add("warning", "这一步没有检查点，Agent 做完了也没法判断是否达标", step.number)
            continue
        for check in step.checks:
            where = f"检查点「{check.text[:30]}」"
            if not check.tagged:
                add("info", f"{where}没写类型，按「需人工确认」处理", step.number)
            elif (
                check.raw_tag
                and check.raw_tag.split(":")[0].split("：")[0].strip() not in _TYPE_ALIASES
            ):
                add(
                    "warning",
                    f"{where}的类型「{check.raw_tag}」认不出，只能是 命令、交付物、确认、判断；先按「需人工确认」处理",
                    step.number,
                )
            if check.type == "command" and not check.command:
                add(
                    "warning",
                    f"{where}是命令类，但没用反引号写出要执行的命令，如 `pytest -q`",
                    step.number,
                )
            if check.type == "artifact" and not check.path:
                add(
                    "warning",
                    f"{where}是交付物类，但没写交付物路径，如 `docs/方案.md`",
                    step.number,
                )
            if check.type != "judgment":
                vague = [word for word in VAGUE_WORDS if word in check.text]
                if vague:
                    add(
                        "warning",
                        f"{where}里的「{'、'.join(vague)}」没法判定；改成能核对的说法，或者标成[判断]交给人看",
                        step.number,
                    )
        if not any(check.type in MACHINE_TYPES for check in step.checks):
            add("info", "这一步的检查点全靠人工确认或判断，平台没有可自动核对的", step.number)
        if step.auto and any(check.type not in MACHINE_TYPES for check in step.checks):
            add(
                "warning",
                "标了「推进：自动」，但有要人看的检查点，这一步不会自动进入下一步",
                step.number,
            )
    return findings


# ---- 给 Agent 的当前步骤说明 ----

STATUS_LABELS = {
    "passed": "通过",
    "failed": "未通过",
    "unknown": "无法判断",
    "pending": "待确认",
    "": "未检查",
}

_REPORT_HINTS = {
    "command": "先调用 fde_task_complete（submit=false，checks 里放这条命令）让平台执行；回报时证据填返回的 proof_id 和检查 id",
    "artifact": "回报时证据填交付物在项目里的相对路径，平台会核对文件在不在、必需章节有没有",
    "confirm": "你不能判通过：备好要确认的材料，回报「待确认」，证据写材料在哪",
    "judgment": "给出自评和依据，最终由人判断",
}


def render_prompt(
    *,
    title: str,
    version: str,
    steps: list[dict[str, Any]],
    current: int,
    progress: dict[str, Any] | None = None,
    work_item_id: str = "",
    budget: int = 8192,
) -> str:
    """当前这一步 + 全流程提纲 + 这一步要用的技能 + 怎么回报检查点。"""
    progress = progress or {}
    step = next((s for s in steps if s["number"] == current), None)
    if step is None:
        return ""
    numbers = [s["number"] for s in steps]
    position = numbers.index(current) + 1
    lines = [
        f"# 这个任务按流程执行：{title}（第 {position} 步 / 共 {len(steps)} 步"
        + (f"，流程版本 {version}" if version else "")
        + "）",
        "全流程提纲：",
    ]
    for item in steps:
        mark = (
            "← 当前" if item["number"] == current else ("已过" if item["number"] < current else "")
        )
        lines.append(f"  {item['number']}. {item['title']} {mark}".rstrip())
    lines += [
        "本轮只做当前这一步，不要提前做后面的步骤；这一步做完由负责人决定是否进入下一步。",
        "",
        f"## 当前第 {current} 步：{step['title']}",
    ]
    if step.get("body"):
        lines.append(step["body"])
    states = (progress.get("steps") or {}).get(str(current), {}).get("checks", {})
    if step.get("checks") and all(
        states.get(str(check["index"]), {}).get("status") == "passed" for check in step["checks"]
    ):
        next_step = next((s["number"] for s in steps if s["number"] > current), None)
        lines.append("本步检查点已全部通过，不要重复要求确认或补交已确认检查点的证明。")
        if next_step is not None:
            lines.append(
                f"流程仍停留在第 {current} 步；请提示负责人在任务的执行规范（SOP）中"
                f"点击进入第 {next_step} 步。人工确认通过不等于推进步骤，点击前不要执行或回报后续步骤。"
            )
        else:
            lines.append("这已是流程最后一步；检查点通过不等于任务成果已评审或验收。")
    if step.get("checks"):
        lines.append(
            "检查点（未完成项在收尾前调用 fde_sop_checkpoint_report 逐条回报状态和证据"
            + (f"，work_item_id={work_item_id}" if work_item_id else "")
            + f"，step={current}；已人工确认通过的无需重复回报；没有证据的「通过」会被降为「无法判断」）："
        )
        for check in step["checks"]:
            state = states.get(str(check["index"]), {})
            status = STATUS_LABELS.get(state.get("status", ""), "未检查")
            human_passed = state.get("status") == "passed" and str(state.get("by", "")).startswith(
                "user:"
            )
            if human_passed:
                status += "，已人工确认通过"
            lines.append(
                f"  {check['index'] + 1}. [{check['type_label']}] {check['text']}（目前：{status}）"
            )
            lines.append(
                "     已由人工确认，无需重复回报或补交平台验证证明。"
                if human_passed
                else f"     怎么回报：{_REPORT_HINTS[check['type']]}"
            )
    if step.get("outputs"):
        lines.append("产出：" + "、".join(step["outputs"]))
    if step.get("skills"):
        lines.append(
            "这一步要用的技能：" + "、".join(step["skills"]) + "（在技能目录里按名字加载）"
        )
    text = "\n".join(lines)
    if len(text.encode("utf-8")) > budget:
        text = (
            text.encode("utf-8")[:budget].decode("utf-8", errors="ignore") + "\n…（已按预算截断）"
        )
    return text


def step_summary(
    engine: Engine, asset_id: str, step_number: int, *, budget: int = 8192
) -> dict[str, Any]:
    """按资产当前内容取某一步的说明（没有冻结的绑定时用）。"""
    merged = merged_steps(engine, asset_id)
    step = next((s for s in merged["steps"] if s["number"] == step_number), None)
    if step is None:
        return {"found": False, "asset_id": asset_id, "step": step_number}
    info = merged.get("info", {})
    text = render_prompt(
        title=info.get("title", ""),
        version="",
        steps=merged["steps"],
        current=step_number,
        budget=budget,
    )
    return {
        "found": True,
        "asset_id": asset_id,
        "step": step_number,
        "total_steps": len(merged["steps"]),
        "checkpoints": step["checkpoints"],
        "prompt_md": text,
        "truncated": text.endswith("…（已按预算截断）"),
        "layers": merged["layers"],
    }
