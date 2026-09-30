"""端到端：真实 uvicorn + 真实 git 仓库 + SQLite，跑完 12 步主链路。

这不是单元测试的补充，而是资产中心 v0.1 的验收：任何一步失败都视为不通过。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.asset_demo import run_demo


@pytest.fixture(scope="module")
def demo_results(tmp_path_factory) -> list:
    data_dir: Path = tmp_path_factory.mktemp("e2e") / "data"
    return run_demo(data_dir, verbose=False)


def test_all_twelve_steps_pass(demo_results) -> None:
    failed = [f"第 {r.number} 步 {r.name}：{r.error}" for r in demo_results if not r.ok]
    assert not failed, "\n".join(failed)
    assert len(demo_results) == 12


@pytest.mark.parametrize(
    "number,keyword",
    [
        (2, "无效资产"),
        (3, "7 种类型"),
        (4, "合并链路"),
        (5, "占位"),
        (7, "422"),
        (8, "高危"),
        (9, "403"),
        (10, "四个目录齐全"),
        (11, "复用通知"),
        (12, "线索"),
    ],
)
def test_step_reports_expected_fact(demo_results, number: int, keyword: str) -> None:
    facts = " ".join(next(r for r in demo_results if r.number == number).facts)
    assert keyword in facts
