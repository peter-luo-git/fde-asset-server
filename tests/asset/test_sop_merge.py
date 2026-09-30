"""SOP 三层合并与步骤摘要。"""

from __future__ import annotations

from sqlalchemy import select

from fde_asset.core.db import assets
from fde_asset.modules.asset.sop import merged_steps, parse_steps, step_summary


def _sop_id(context, name: str = "bank-core-migration") -> str:
    with context.engine.connect() as conn:
        return conn.execute(select(assets.c.asset_id).where(assets.c.name == name)).scalar_one()


def test_parse_steps_picks_checkpoints_and_outputs() -> None:
    steps = parse_steps("1. 准备\n   说明\n   - 检查点：环境就绪\n   - 产出：清单\n2. 执行\n")
    assert steps[0].checkpoints == ["环境就绪"] and steps[0].outputs == ["清单"]
    assert steps[1].number == 2 and steps[1].title == "执行"


def test_merge_l1_and_l2(indexed) -> None:
    merged = merged_steps(indexed.engine, _sop_id(indexed))
    assert merged["layers"] == ["L1:delivery-standard", "L2:bank-core-migration"]
    numbers = [s["number"] for s in merged["steps"]]
    assert numbers == [1, 2, 3, 4, 5]
    overridden = {s["number"] for s in merged["steps"] if s["overridden_by"]}
    assert overridden == {2, 3}


def test_l1_alone_has_no_override(indexed) -> None:
    merged = merged_steps(indexed.engine, _sop_id(indexed, "delivery-standard"))
    assert merged["layers"] == ["L1:delivery-standard"]
    assert all(not s["overridden_by"] for s in merged["steps"])


def test_step_summary_contains_checkpoints(indexed) -> None:
    summary = step_summary(indexed.engine, _sop_id(indexed), 3)
    assert summary["found"] and summary["total_steps"] == 5
    assert "连续三轮差异为 0" in summary["prompt_md"]
    assert "不适用" in summary["prompt_md"]


def test_step_summary_missing_step(indexed) -> None:
    assert step_summary(indexed.engine, _sop_id(indexed), 99)["found"] is False


def test_step_summary_respects_budget(indexed) -> None:
    summary = step_summary(indexed.engine, _sop_id(indexed), 3, budget=120)
    assert summary["truncated"] and len(summary["prompt_md"].encode("utf-8")) <= 160
