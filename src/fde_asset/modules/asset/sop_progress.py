"""任务按流程执行的进度：绑在哪一步、每个检查点的状态和证据、默认流程。

平台记状态，不靠 Agent 记：

- 绑定时把合并后的步骤冻结下来（连同流程版本），之后流程改了不影响进行中的任务，
  页面提示有新版本，负责人点"更新"才换；
- Agent 收尾时逐条回报检查点。"通过"必须带证据；命令类、交付物类要 fde-server 核对过才算；
  确认类、判断类 Agent 说了不算，只能到"待确认"，由人点确认；
- 进入下一步默认由人点。前一步还有没通过的，要明确选"仍然进入"；
  只有标了「推进：自动」、检查点全是平台能核对的、而且都通过了，才自动进下一步；
- 默认流程：管理员和各范围负责人给全公司、部门、客户、项目设一条，设好之后新建的任务
  第一次被查看或执行时自动绑上；负责人可以换，也可以选"不按流程做"。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, delete, select, update
from sqlalchemy.engine import Connection, Engine

from fde_asset.core.db import assets, sop_defaults, work_item_assets
from fde_asset.modules.asset import sop as sop_module
from fde_asset.modules.asset.sop import MACHINE_TYPES

DEFAULT_SCOPES = ("company", "department", "customer", "engagement")
#: 查默认流程时从最具体的范围往上找
_SPECIFIC_FIRST = ("engagement", "customer", "department", "company")
HISTORY_LIMIT = 200
STATUSES = {"passed", "failed", "unknown", "pending"}
EVIDENCE_LIMIT = 2000


class SopProgressError(ValueError):
    def __init__(self, message: str, status: int = 422, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def usable_asset(conn: Connection, asset_id: str) -> Any:
    """能拿来执行的流程：没删、校验通过、不是草稿/下架。"""
    return conn.execute(
        select(assets).where(
            assets.c.asset_id == asset_id,
            assets.c.kind == "Sop",
            assets.c.deleted_at.is_(None),
            assets.c.valid.is_(True),
            assets.c.lifecycle.notin_(["deprecated", "archived", "draft"]),
        )
    ).first()


def binding_row(conn: Connection, work_item_id: str, role: str = "sop_step") -> Any:
    return conn.execute(
        select(work_item_assets).where(
            and_(work_item_assets.c.work_item_id == work_item_id, work_item_assets.c.role == role)
        )
    ).first()


def load_progress(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(row.progress_json or "{}") if row is not None else {}
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def frozen_steps(engine: Engine, row: Any, progress: dict[str, Any]) -> list[dict[str, Any]]:
    """绑定时冻结的步骤；老绑定没冻结过的，按流程现在的内容算。"""
    steps = progress.get("steps_snapshot")
    if isinstance(steps, list) and steps:
        return steps
    return sop_module.merged_steps(engine, row.asset_id)["steps"]


def _history(progress: dict[str, Any], **entry: Any) -> None:
    items = progress.setdefault("history", [])
    items.append({"at": _iso(), **entry})
    del items[:-HISTORY_LIMIT]


def _snapshot(engine: Engine, asset_id: str) -> tuple[list[dict[str, Any]], str, str]:
    merged = sop_module.merged_steps(engine, asset_id)
    with engine.connect() as conn:
        row = conn.execute(
            select(assets.c.version, assets.c.title).where(assets.c.asset_id == asset_id)
        ).first()
    return merged["steps"], (row.version if row else ""), (row.title if row else "")


def _keep_matching_states(
    old_steps: list[dict[str, Any]], new_steps: list[dict[str, Any]], states: dict[str, Any]
) -> dict[str, Any]:
    """换版本时，文字没变的检查点保留原来的状态，其余清空。"""
    kept: dict[str, Any] = {}
    old = {s["number"]: s for s in old_steps}
    for step in new_steps:
        before = old.get(step["number"])
        saved = (states.get(str(step["number"])) or {}).get("checks", {})
        if before is None or not saved:
            continue
        texts = {c["text"]: c["index"] for c in before.get("checks", [])}
        checks = {
            str(c["index"]): saved[str(texts[c["text"]])]
            for c in step.get("checks", [])
            if c["text"] in texts and str(texts[c["text"]]) in saved
        }
        if checks:
            kept[str(step["number"])] = {"checks": checks}
    return kept


def bind(
    engine: Engine,
    *,
    work_item_id: str,
    engagement_slug: str,
    asset_id: str,
    step: int,
    actor: str,
    force: bool = False,
    source: str = "manual",
) -> dict[str, Any]:
    """绑定、换流程或换步骤。往后推进时，当前这步还有没通过的检查点要 force 才行。"""
    steps, version, _ = _snapshot(engine, asset_id)
    if not any(item["number"] == step for item in steps):
        raise SopProgressError("必须指定实际存在的 sop_step")
    with engine.begin() as conn:
        existing = binding_row(conn, work_item_id)
        progress = load_progress(existing)
        same_asset = existing is not None and existing.asset_id == asset_id
        if same_asset:
            current = existing.sop_step or 1
            snapshot = frozen_steps(engine, existing, progress)
            if step > current and not force:
                unfinished = unfinished_checks(snapshot, progress, current)
                if unfinished:
                    raise SopProgressError(
                        f"第 {current} 步还有 {len(unfinished)} 个检查点没通过",
                        status=409,
                        code="SOP_STEP_UNFINISHED",
                    )
            if step != current:
                progress["current_step"] = step
                _history(
                    progress,
                    by=actor,
                    action="advance" if step > current else "back",
                    step=step,
                    **(
                        {"forced": True, "from": current}
                        if force and step > current
                        else {"from": current}
                    ),
                )
            values: dict[str, Any] = {"sop_step": step}
            if not progress.get("steps_snapshot"):
                # 老绑定没冻结过：按现在的内容补上，之后流程再改就不影响这个任务了
                progress["steps_snapshot"] = snapshot
                if not existing.sop_version:
                    values["sop_version"] = version
        else:
            progress = {
                "steps_snapshot": steps,
                "steps": {},
                "history": progress.get("history", []),
            }
            _history(progress, by=actor, action="bind", step=step, asset_id=asset_id, source=source)
            values = {"asset_id": asset_id, "sop_step": step, "sop_version": version}
        values.update(
            progress_json=json.dumps(progress, ensure_ascii=False),
            engagement_slug=engagement_slug,
            updated_at=_now(),
        )
        if existing is not None:
            conn.execute(
                update(work_item_assets)
                .where(work_item_assets.c.id == existing.id)
                .values(**values)
            )
        else:
            conn.execute(
                work_item_assets.insert().values(
                    work_item_id=work_item_id,
                    role="sop_step",
                    created_by=actor,
                    **values,
                )
            )
        conn.execute(
            delete(work_item_assets).where(
                and_(
                    work_item_assets.c.work_item_id == work_item_id,
                    work_item_assets.c.role == "sop_none",
                )
            )
        )
    return {
        "work_item_id": work_item_id,
        "asset_id": asset_id,
        "sop_step": step,
        "replaced": existing is not None,
    }


def opt_out(engine: Engine, *, work_item_id: str, engagement_slug: str, actor: str) -> bool:
    """不按流程做：解绑，并记下来，免得默认流程又自动绑回来。"""
    with engine.begin() as conn:
        removed = conn.execute(
            delete(work_item_assets).where(
                and_(
                    work_item_assets.c.work_item_id == work_item_id,
                    work_item_assets.c.role == "sop_step",
                )
            )
        ).rowcount
        if binding_row(conn, work_item_id, "sop_none") is None:
            conn.execute(
                work_item_assets.insert().values(
                    engagement_slug=engagement_slug,
                    work_item_id=work_item_id,
                    asset_id="",
                    role="sop_none",
                    created_by=actor,
                )
            )
    return bool(removed)


def refresh_version(engine: Engine, *, work_item_id: str, actor: str) -> dict[str, Any]:
    """换到流程的最新版本：文字没变的检查点保留状态。"""
    with engine.begin() as conn:
        row = binding_row(conn, work_item_id)
        if row is None:
            raise SopProgressError("这个任务没按流程执行", status=404)
    steps, version, _ = _snapshot(engine, row.asset_id)
    progress = load_progress(row)
    old_steps = frozen_steps(engine, row, progress)
    progress["steps"] = _keep_matching_states(old_steps, steps, progress.get("steps") or {})
    progress["steps_snapshot"] = steps
    current = row.sop_step or 1
    if not any(item["number"] == current for item in steps):
        current = steps[0]["number"] if steps else 1
    _history(progress, by=actor, action="refresh", version=version, step=current)
    with engine.begin() as conn:
        conn.execute(
            update(work_item_assets)
            .where(work_item_assets.c.id == row.id)
            .values(
                sop_step=current,
                sop_version=version,
                progress_json=json.dumps(progress, ensure_ascii=False),
                updated_at=_now(),
            )
        )
    return {"work_item_id": work_item_id, "sop_version": version, "sop_step": current}


def unfinished_checks(
    steps: list[dict[str, Any]], progress: dict[str, Any], step_number: int
) -> list[dict[str, Any]]:
    step = next((s for s in steps if s["number"] == step_number), None)
    if step is None:
        return []
    states = (progress.get("steps") or {}).get(str(step_number), {}).get("checks", {})
    return [
        check
        for check in step.get("checks", [])
        if (states.get(str(check["index"])) or {}).get("status") != "passed"
    ]


def _check_of(steps: list[dict[str, Any]], step_number: int, index: int) -> dict[str, Any]:
    step = next((s for s in steps if s["number"] == step_number), None)
    if step is None:
        raise SopProgressError(f"流程里没有第 {step_number} 步")
    check = next((c for c in step.get("checks", []) if c["index"] == index), None)
    if check is None:
        raise SopProgressError(f"第 {step_number} 步没有第 {index + 1} 个检查点")
    return check


def judge_agent_claim(check: dict[str, Any], claim: dict[str, Any]) -> dict[str, Any]:
    """Agent 报上来的一条，按规则定最终状态。

    claim：status（passed/failed/unknown/pending）、evidence、note，
    以及 fde-server 核对过的 verified（True 通过 / False 不通过 / None 没核对）和 verify_detail。
    """
    status = claim.get("status")
    if status not in STATUSES:
        raise SopProgressError("检查点状态只能是 passed、failed、unknown、pending")
    evidence = str(claim.get("evidence") or "").strip()[:EVIDENCE_LIMIT]
    note = str(claim.get("note") or "").strip()[:EVIDENCE_LIMIT]
    verified = claim.get("verified")
    detail = str(claim.get("verify_detail") or "")[:500]
    reason = ""
    final = status
    if check["type"] in MACHINE_TYPES:
        if status == "passed":
            if verified is True:
                final = "passed"
            elif verified is False:
                final, reason = "failed", detail or "平台核对没通过"
            else:
                final, reason = "unknown", "平台没核对到，不能算通过"
    else:
        # 确认类、判断类 Agent 说了不算：报通过也只到待确认
        if status == "passed":
            final, reason = "pending", "需要人来确认，Agent 只能报待确认"
    if final == "passed" and not evidence:
        final, reason = "unknown", "没有证据的「通过」降为无法判断"
    return {
        "status": final,
        "claimed": status,
        "evidence": evidence,
        "note": note,
        "verified": verified,
        "verify_detail": detail,
        "reason": reason,
    }


def apply_report(
    engine: Engine,
    *,
    work_item_id: str,
    step: int,
    results: list[dict[str, Any]],
    agent: str,
    turn_id: str = "",
) -> dict[str, Any]:
    """Agent 收尾时的检查点回报。只认当前这一步。"""
    with engine.begin() as conn:
        row = binding_row(conn, work_item_id)
        if row is None:
            raise SopProgressError("这个任务没按流程执行", status=404)
        progress = load_progress(row)
        steps = frozen_steps(engine, row, progress)
        current = row.sop_step or 1
        if step != current:
            raise SopProgressError(
                f"任务现在在第 {current} 步，只能回报这一步的检查点",
                status=409,
                code="SOP_STEP_MISMATCH",
            )
        states = progress.setdefault("steps", {}).setdefault(str(step), {}).setdefault("checks", {})
        judged = []
        for claim in results:
            index = claim.get("index")
            if type(index) is not int:
                raise SopProgressError("index 要写检查点序号（从 0 开始）")
            check = _check_of(steps, step, index)
            outcome = judge_agent_claim(check, claim)
            previous = states.get(str(index)) or {}
            if previous.get("by", "").startswith("user:") and previous.get("status") == "passed":
                # 人已经确认过的，Agent 不能改回去
                judged.append(
                    {
                        "index": index,
                        **outcome,
                        "status": "passed",
                        "reason": "已由人确认，保持通过",
                    }
                )
                continue
            states[str(index)] = {
                "status": outcome["status"],
                "evidence": outcome["evidence"],
                "note": outcome["note"],
                "verified": outcome["verified"],
                "verify_detail": outcome["verify_detail"],
                "by": f"agent:{agent}",
                "turn_id": turn_id,
                "at": _iso(),
            }
            judged.append({"index": index, **outcome})
        _history(
            progress,
            by=f"agent:{agent}",
            action="report",
            step=step,
            summary={item["index"]: item["status"] for item in judged},
        )
        advanced_to = _auto_advance(steps, progress, step)
        values: dict[str, Any] = {
            "progress_json": json.dumps(progress, ensure_ascii=False),
            "updated_at": _now(),
        }
        if advanced_to:
            values["sop_step"] = advanced_to
        conn.execute(
            update(work_item_assets).where(work_item_assets.c.id == row.id).values(**values)
        )
    return {
        "work_item_id": work_item_id,
        "step": step,
        "results": judged,
        "unfinished": len(unfinished_checks(steps, progress, step)),
        "advanced_to": advanced_to,
    }


def _auto_advance(steps: list[dict[str, Any]], progress: dict[str, Any], step: int) -> int | None:
    current = next((s for s in steps if s["number"] == step), None)
    if (
        current is None
        or not current.get("auto")
        or not current.get("checks")
        or any(c["type"] not in MACHINE_TYPES for c in current["checks"])
        or unfinished_checks(steps, progress, step)
    ):
        return None
    following = [s["number"] for s in steps if s["number"] > step]
    if not following:
        return None
    progress["current_step"] = following[0]
    _history(progress, by="platform", action="auto_advance", step=following[0], **{"from": step})
    return following[0]


def human_decide(
    engine: Engine,
    *,
    work_item_id: str,
    step: int,
    index: int,
    decision: str,
    note: str,
    actor: str,
) -> dict[str, Any]:
    """人确认、退回或清空某个检查点。任何类型都能由人定。"""
    if decision not in {"confirm", "reject", "reset"}:
        raise SopProgressError("decision 只能是 confirm、reject、reset")
    with engine.begin() as conn:
        row = binding_row(conn, work_item_id)
        if row is None:
            raise SopProgressError("这个任务没按流程执行", status=404)
        progress = load_progress(row)
        steps = frozen_steps(engine, row, progress)
        _check_of(steps, step, index)
        states = progress.setdefault("steps", {}).setdefault(str(step), {}).setdefault("checks", {})
        previous = states.get(str(index)) or {}
        if decision == "reset":
            states.pop(str(index), None)
        else:
            states[str(index)] = {
                **{
                    k: previous.get(k)
                    for k in ("evidence", "verified", "verify_detail")
                    if k in previous
                },
                "status": "passed" if decision == "confirm" else "failed",
                "note": note.strip()[:EVIDENCE_LIMIT],
                "by": f"user:{actor}",
                "at": _iso(),
                **(
                    {"agent_status": previous.get("status")}
                    if previous.get("by", "").startswith("agent:")
                    else {}
                ),
            }
        _history(progress, by=f"user:{actor}", action=decision, step=step, index=index)
        conn.execute(
            update(work_item_assets)
            .where(work_item_assets.c.id == row.id)
            .values(progress_json=json.dumps(progress, ensure_ascii=False), updated_at=_now())
        )
    return {
        "work_item_id": work_item_id,
        "step": step,
        "index": index,
        "status": states.get(str(index), {}).get("status", ""),
    }


# ---- 默认流程 ----


def default_for(
    conn: Connection, *, department_code: str, customer_code: str, engagement_slug: str
) -> Any:
    """这个项目生效的默认流程：项目 > 客户 > 部门 > 全公司，取最具体的一条。"""
    codes = {
        "engagement": engagement_slug,
        "customer": customer_code,
        "department": department_code,
        "company": "",
    }
    for scope in _SPECIFIC_FIRST:
        code = codes[scope]
        if scope != "company" and not code:
            continue
        row = conn.execute(
            select(sop_defaults).where(sop_defaults.c.scope == scope, sop_defaults.c.code == code)
        ).first()
        if row is not None:
            return row
    return None


def ensure_default(
    engine: Engine,
    *,
    work_item_id: str,
    engagement_slug: str,
    engagement: dict[str, Any],
    created_at: datetime | None,
) -> bool:
    """默认流程设好之后才新建的任务，第一次被查看或执行时自动绑上。返回这次绑上了没有。

    设默认流程之前就有的任务不动（存量不迁移）；选过"不按流程做"的也不动。
    """
    if created_at is None:
        return False
    with engine.connect() as conn:
        if binding_row(conn, work_item_id) is not None or binding_row(
            conn, work_item_id, "sop_none"
        ):
            return False
        default = default_for(
            conn,
            department_code=engagement.get("department_code", ""),
            customer_code=engagement.get("customer_code", ""),
            engagement_slug=engagement_slug,
        )
        if default is None or usable_asset(conn, default.asset_id) is None:
            return False
    set_at = default.created_at
    if set_at.tzinfo is None:
        set_at = set_at.replace(tzinfo=timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if created_at < set_at:
        return False
    steps, _, _ = _snapshot(engine, default.asset_id)
    if not steps:
        return False
    try:
        bind(
            engine,
            work_item_id=work_item_id,
            engagement_slug=engagement_slug,
            asset_id=default.asset_id,
            step=steps[0]["number"],
            actor="default",
            source=f"default:{default.scope}" + (f":{default.code}" if default.code else ""),
        )
    except Exception:  # noqa: BLE001 - 并发下另一边先绑上了也没关系
        return False
    return True


def set_default(engine: Engine, *, scope: str, code: str, asset_id: str, actor: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            delete(sop_defaults).where(sop_defaults.c.scope == scope, sop_defaults.c.code == code)
        )
        conn.execute(
            sop_defaults.insert().values(
                scope=scope, code=code, asset_id=asset_id, created_by=actor
            )
        )


def clear_default(engine: Engine, *, scope: str, code: str) -> bool:
    with engine.begin() as conn:
        return bool(
            conn.execute(
                delete(sop_defaults).where(
                    sop_defaults.c.scope == scope, sop_defaults.c.code == code
                )
            ).rowcount
        )


# ---- 给页面和 Agent 看的状态 ----


def public_state(engine: Engine, row: Any) -> dict[str, Any]:
    """任务页用：冻结的步骤 + 每个检查点的状态 + 版本 + 最近的记录。"""
    progress = load_progress(row)
    steps = frozen_steps(engine, row, progress)
    current = row.sop_step or 1
    states = progress.get("steps") or {}
    with engine.connect() as conn:
        asset = conn.execute(
            select(assets.c.title, assets.c.version, assets.c.kind_spec_json).where(
                assets.c.asset_id == row.asset_id
            )
        ).first()
    out_steps = []
    for step in steps:
        saved = (states.get(str(step["number"])) or {}).get("checks", {})
        checks = [
            {**check, **(saved.get(str(check["index"])) or {"status": ""})}
            for check in step.get("checks", [])
        ]
        if step["number"] == current:
            phase = "current"
        elif step["number"] < current:
            phase = "done" if all(c.get("status") == "passed" for c in checks) else "passed_over"
        else:
            phase = "todo"
        out_steps.append({**step, "checks": checks, "phase": phase})
    latest = asset.version if asset else ""
    history = progress.get("history") or []
    bind_entry = next((h for h in reversed(history) if h.get("action") == "bind"), {})
    return {
        "bound": True,
        "asset_id": row.asset_id,
        "title": asset.title if asset else None,
        "layer": json.loads(asset.kind_spec_json or "{}").get("layer", "") if asset else "",
        "sop_step": current,
        "sop_version": row.sop_version,
        "latest_version": latest,
        "outdated": bool(asset) and bool(row.sop_version) and latest != row.sop_version,
        "source": bind_entry.get("source", "manual"),
        "steps": out_steps,
        "unfinished": len(unfinished_checks(steps, progress, current)),
        "history": history[-20:],
    }


def agent_step(engine: Engine, row: Any, title: str, *, budget: int = 8192) -> dict[str, Any]:
    """给 fde-server 拼提示词：当前一步的说明，以及核对检查点要用的结构化内容。"""
    progress = load_progress(row)
    steps = frozen_steps(engine, row, progress)
    current = row.sop_step or 1
    step = next((s for s in steps if s["number"] == current), None)
    if step is None:
        return {"found": False}
    text = sop_module.render_prompt(
        title=title,
        version=row.sop_version,
        steps=steps,
        current=current,
        progress=progress,
        work_item_id=row.work_item_id,
        budget=budget,
    )
    return {
        "found": True,
        "step": current,
        "total_steps": len(steps),
        "step_title": step["title"],
        "sop_version": row.sop_version,
        "checks": step.get("checks", []),
        "skills": step.get("skills", []),
        "prompt_md": text,
    }
