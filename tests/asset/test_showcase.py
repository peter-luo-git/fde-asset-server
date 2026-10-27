"""资产示范库：管理员一键安装预置的 SOP 和技能，已装的跳过；任何看得到的资产都能复制为草稿；
技能市场默认来源换成 DeepSeek Harness，以前系统自动配的 Anthropic 来源自动替换。"""

from __future__ import annotations

from sqlalchemy import select

from fde_asset.core.db import skill_sources
from fde_asset.modules.asset import sop
from tests.conftest import as_user


def test_install_showcase_then_copy_as_draft(client) -> None:
    admin = as_user(client, "admin")
    listed = admin.get("/api/v1/showcase").json()
    assert listed["can_install"] and {i["kind"] for i in listed["items"]} == {"Sop", "Skill"}
    assert not any(i["installed"] for i in listed["items"])
    result = admin.post("/api/v1/showcase/install").json()
    assert result["failed"] == [], result["failed"]
    assert len(result["installed"]) == len(listed["items"])
    again = admin.post("/api/v1/showcase/install").json()
    assert again["installed"] == [] and len(again["skipped"]) == len(listed["items"])
    # 平台更新了示范内容：按修订入库，编号不变、版本加一
    from fde_asset.modules.asset import showcase_content

    item = next(i for i in showcase_content.SHOWCASE if i["name"] == "fcp-english-sentence-coach")
    before = {i["name"]: i["asset_id"] for i in admin.get("/api/v1/showcase").json()["items"]}
    original = dict(item["files"])
    item["files"]["references/常见错误.md"] += "| 新增 | x | y |\n"
    try:
        upgraded = admin.post("/api/v1/showcase/install").json()
    finally:
        item["files"].clear()
        item["files"].update(original)
    assert upgraded["updated"] == ["fcp-english-sentence-coach"], upgraded
    asset_id = before["fcp-english-sentence-coach"]
    assert admin.get(f"/api/v1/assets/{asset_id}").json()["version"] == "1.1.0"

    chen = as_user(client, "chen")
    items = {i["name"]: i for i in chen.get("/api/v1/showcase").json()["items"]}
    assert all(i["installed"] and i["asset_id"] for i in items.values())
    assert chen.post("/api/v1/showcase/install").status_code == 403
    # 全公司可见，SOP 能拿来执行，步骤点名了配套技能
    doc_sop = items["fcp-tech-doc-sop"]["asset_id"]
    steps = chen.get(f"/api/v1/assets/{doc_sop}/sop-steps").json()["steps"]
    assert steps[0]["skills"] == ["fcp-tech-doc-writer"]
    assert {c["type"] for s in steps for c in s["checks"]} >= {"artifact", "command", "confirm"}

    # 复制技能：默认个人专用，整目录带过去，name 换成新标识
    skill = items["fcp-markdown-slides"]["asset_id"]
    draft = chen.post(f"/api/v1/assets/{skill}/copy", json={}).json()
    assert draft["scope"] == "user" and draft["name"].startswith("fcp-markdown-slides-copy-")
    assert "scripts/outline_to_slides.py" in draft["files"]
    assert f"name: {draft['name']}" in draft["files"]["SKILL.md"]
    assert "示范资产" not in draft["files"]["asset.yaml"]
    # 复制 SOP：不能个人专用，默认放到本部门
    assert chen.post(f"/api/v1/assets/{doc_sop}/copy", json={"scope": "user"}).status_code == 422
    sop_draft = chen.post(f"/api/v1/assets/{doc_sop}/copy", json={}).json()
    assert sop_draft["scope"] == "department" and "（副本）" in sop_draft["title"]
    assert not [f for f in sop.lint(sop_draft["files"]["SOP.md"]) if f["level"] == "error"]


def test_old_default_skill_source_is_replaced(client) -> None:
    ctx = client.app.state.context
    with ctx.engine.begin() as conn:
        conn.execute(
            skill_sources.insert().values(
                name="旧来源", url="https://github.com/anthropics/skills", created_by="seed"
            )
        )
    urls = [i["url"] for i in client.get("/api/v1/skill-market/sources").json()["items"]]
    assert urls == ["https://github.com/deepseek-ai/deepseek-harness"]
    with ctx.engine.connect() as conn:
        assert len(conn.execute(select(skill_sources)).fetchall()) == 1


def test_solar_term_poems_script_reports_term_festival_and_source_poems(tmp_path) -> None:
    """节气诗笺：脚本只用标准库；日期落在节气、节日上要对，古诗原文来自数据文件、不编造。"""
    import json
    import subprocess
    import sys

    from fde_asset.modules.asset import showcase_content

    files = showcase_content._folder("fcp-solar-term-poems")
    for relative, text in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def today(day: str) -> dict:
        output = subprocess.run(
            [sys.executable, "-I", str(tmp_path / "scripts/today.py"), day],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return json.loads(output)

    frost = today("2026-10-26")
    assert (frost["solar_term"], frost["is_term_day"], frost["days_into_term"]) == (
        "霜降",
        False,
        3,
    )
    assert (frost["next_term"], frost["days_to_next_term"], frost["season"]) == ("立冬", 12, "秋")
    assert any(p["title"] == "山行" and p["author"] == "杜牧" for p in frost["poems"])
    mid_autumn = today("2026-09-25")
    assert mid_autumn["festival"] == "中秋" and mid_autumn["poems"][0]["title"].startswith(
        "十五夜望月"
    )
    assert today("2025-12-21")["is_term_day"] is True
    assert "error" in today("2040-01-01")
    library = json.loads(files["references/poems.json"])["poems"]
    # 二十四节气每个至少一首，全部写明作者和朝代
    assert (
        len(
            [
                k
                for k in library
                if len(k) == 2
                and k not in {"春节", "元宵", "端午", "七夕", "中秋", "重阳", "除夕", "腊八"}
            ]
        )
        == 24
    )
    assert all(
        p["author"] and p["dynasty"] and p["lines"] for poems in library.values() for p in poems
    )
