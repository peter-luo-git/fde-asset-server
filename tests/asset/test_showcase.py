"""资产示范库：管理员一键安装预置的 SOP 和技能，已装的跳过；任何看得到的资产都能复制为草稿；
技能市场默认来源换成 DeepSeek Harness，以前系统自动配的 Anthropic 来源自动替换。"""

from __future__ import annotations

from sqlalchemy import select

from fde_asset.core.db import skill_sources
from fde_asset.modules.asset import sop
from tests.conftest import as_user


def test_project_development_workflow_preserves_delivery_guidance() -> None:
    """迁移 Runtime 原正文契约到资产目录；只验证送达指引，不冒充模型遵循验收。"""
    import re

    from fde_asset.modules.asset import showcase_content

    item = next(item for item in showcase_content.SHOWCASE if item["name"] == "project-development")
    workflow = item["files"]["references/project-development-workflow.md"]
    requirements = {
        "需求变化先读并更新原任务": r"已确认的需求变化.*fde_task_get.*fde_task_update",
        "验收标准与乐观版本": r"acceptance_md.*expected_version",
        "建议不成为用户验收": r"讨论建议、疑问和未采纳选项不写成用户确认的验收标准",
        "完成后退回沿用原任务": r"完成后退回.*沿用原任务及历史记录",
        "历史PR不能代本轮交付": r"历史 PR 的合并或批准不代表本轮交付通过",
        "本轮重新评审与验收": r"等待新的评审与发起人验收",
        "正式文档交付复用或登记任务": r"正式 Spec、PRD、设计报告、PPT.*复用相关任务.*创建一次",
        "文档任务仓库可空": r"省略 `repo_id` 或传 null",
        "实际仓库清单与创建权限": r"fde_project_repository_list.*fde_project_repository_create.*不授予建仓权限",
        "会话工作目录": r"sessions/<键>/.*project_workspace.session",
        "打开实际仓库并使用返回路径": r"fde_workspace_open_repo\(repository_id\).*checkout_relative_path",
        "保护其他目录": r"不要修改 `repos/` 下的仓库副本和 `sessions/` 下别的目录",
        "移除工作树保留边界": r"只有用户范围包含清理.*git worktree remove",
        "不预读整套技能": r"根据当前任务加载适用的流程及专业 Skill，不预先读取整套正文",
        "正文适用用户当前指令和权限": r"用户当前指令和平台权限边界保持优先",
        "设计交付来源分类": r"公开答复、方案文档和需求摘要.*用户明确事项.*Agent建议／实现选项.*待确认问题",
        "任务和PR沿用归属": r"任务描述、验收标准和 PR 说明",
        "确认只到逐项需求": r"已确认只修饰.*逐项需求.*引用旧版时保留每项的原归属",
        "记录读回和发布复核": r"fde_work_record_read.*发布前核对.*修正后复核",
        "讨论记录按真实来源保存": r"fde_work_record_write.*讨论／待确认",
        "同时核对作者与来源": r"原话、作者身份与来源消息必须一起核对",
        "未知作者不猜姓名": r"无法核实作者时只引用原话和消息标识，不猜姓名",
        "独立读取安排": r"独立读取放在同一模型响应",
        "写入审批保留依赖": r"写入、审批和需要前序结果才能确定参数的动作继续按依赖顺序执行",
    }
    for label, pattern in requirements.items():
        assert re.search(pattern, workflow, re.DOTALL), label


def test_project_development_is_optional_and_copies_complete_workflow(client) -> None:
    """示范流程仅显式安装；完整正文随技能入库和复制，不依赖 Runtime 内置文件。"""
    import hashlib

    admin = as_user(client, "admin")
    listed = {item["name"]: item for item in admin.get("/api/v1/showcase").json()["items"]}
    assert "project-development" in listed
    example = listed["project-development"]
    assert example["kind"] == "Skill"
    assert example["title"] == "项目开发交付（示范，可选）"
    assert example["installed"] is False and example["asset_id"] is None
    assert set(example["files"]) == {
        "SKILL.md",
        "references/project-development-workflow.md",
    }

    installed = admin.post("/api/v1/showcase/install").json()
    assert installed["failed"] == [], installed
    assert "project-development" in installed["installed"]
    listed_after = {item["name"]: item for item in admin.get("/api/v1/showcase").json()["items"]}
    asset_id = listed_after["project-development"]["asset_id"]
    assert listed_after["project-development"]["installed"] is True and asset_id
    repeated = admin.post("/api/v1/showcase/install").json()
    assert repeated["installed"] == [] and repeated["updated"] == []
    assert "project-development" in repeated["skipped"]

    chen = as_user(client, "chen")
    copied = chen.post(f"/api/v1/assets/{asset_id}/copy", json={})
    assert copied.status_code == 200, copied.text
    draft = copied.json()
    assert draft["scope"] == "user" and draft["name"].startswith("project-development-copy-")
    assert f"name: {draft['name']}" in draft["files"]["SKILL.md"]
    # 迁移前源技能仅去除 frontmatter 后的完整 UTF-8 正文指纹，防止丢段或只复制短入口。
    workflow = draft["files"]["references/project-development-workflow.md"]
    assert hashlib.sha256(workflow.encode("utf-8")).hexdigest() == (
        "f00a14a83f68abb873ddfd0c3f9c036446863cf296e9a540d46290bbbfbd0e62"
    )


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
