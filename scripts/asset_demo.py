"""资产中心端到端演示：真实 HTTP 服务 + 真实 git 仓库 + SQLite，不依赖 Docker / Gitea / 模型。

12 步全绿 = 资产中心 v0.1 验收通过。既可以直接运行，也被 tests/e2e 复用。
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fde_asset.api.app import create_app  # noqa: E402
from fde_asset.settings import AssetSettings  # noqa: E402
from scripts.seed_assets import make_docx, make_pdf, seed  # noqa: E402


@dataclass
class StepResult:
    number: int
    name: str
    ok: bool
    facts: list[str] = field(default_factory=list)
    error: str = ""


class DemoFailure(AssertionError):
    pass


def _free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LiveServer:
    """在后台线程里跑真实 uvicorn，端到端测试走真实 HTTP。"""

    def __init__(self, settings: AssetSettings) -> None:
        self.port = _free_port()
        config = uvicorn.Config(
            create_app(settings), host="127.0.0.1", port=self.port, log_level="warning"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        base = f"http://127.0.0.1:{self.port}"
        for _ in range(100):
            try:
                if httpx.get(f"{base}/health/live", timeout=1).status_code == 200:
                    return base
            except httpx.HTTPError:
                time.sleep(0.1)
        raise DemoFailure("服务未能启动")

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def _client(base: str, user: str) -> httpx.Client:
    return httpx.Client(base_url=base, headers={"X-FDE-User": user}, timeout=30)


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise DemoFailure(message)


def run_demo(data_dir: Path, *, verbose: bool = True) -> list[StepResult]:
    settings = AssetSettings(data_dir=data_dir)
    settings.ensure_dirs()
    seed(settings)
    results: list[StepResult] = []

    with LiveServer(settings) as base:
        chen, li, zhao, admin = (_client(base, u) for u in ("chen", "li", "zhao", "admin"))
        state: dict[str, Any] = {}

        def step(number: int, name: str, body: Callable[[], list[str]]) -> None:
            try:
                facts = body()
                results.append(StepResult(number, name, True, facts))
                if verbose:
                    print(f"[{number:2}] ✅ {name}")
                    for fact in facts:
                        print(f"        {fact}")
            except Exception as exc:  # noqa: BLE001 - 演示脚本需要汇总所有失败
                results.append(
                    StepResult(number, name, False, error=f"{type(exc).__name__}: {exc}")
                )
                if verbose:
                    print(f"[{number:2}] ❌ {name}\n        {type(exc).__name__}: {exc}")

        # 1 种子仓库
        def step1() -> list[str]:
            repos = sorted(p.name for p in settings.repos.glob("*.git"))
            _check(len(repos) == 4, f"应有四类仓库（公司/部门/客户/项目），实际 {repos}")
            return [
                f"本地裸仓库：{', '.join(repos)}",
                "八种类型的种子资产已写入（含应用资产、pdf / docx / xlsx / pptx 附件）",
            ]

        # 2 索引
        def step2() -> list[str]:
            response = admin.post("/api/v1/admin/assets/reindex")
            _check(response.status_code == 200, f"索引失败 {response.text}")
            report = {item["repo"]: item for item in response.json()["repos"]}
            _check(
                report["company-assets"]["indexed"] == 9,
                f"公司级入库数异常：{report['company-assets']}",
            )
            _check(report["company-assets"]["invalid"] == 2, "应识别出 2 个无效资产")
            invalid = admin.get("/api/v1/assets/invalid").json()["items"]
            codes = sorted({item["code"] for item in invalid})
            _check(
                "summary_missing" in codes and "section_missing_company" in codes,
                f"错误类型不符：{codes}",
            )
            return [
                f"公司级 {report['company-assets']['indexed']} 个、部门级 {report['dept-data-intel-assets']['indexed']} 个、"
                f"项目级 {report['policy-import']['indexed']} 个",
                f"无效资产 {len(invalid)} 条，原因：{', '.join(codes)}（缺一句话结论 / 公司级缺回滚预案）",
            ]

        # 3 目录：按类型与作用域筛选
        def step3() -> list[str]:
            total = chen.get("/api/v1/assets", params={"limit": 100}).json()
            kinds = sorted({item["kind"] for item in total["items"]})
            _check(len(kinds) == 8, f"应能看到 8 种类型，实际 {kinds}")
            dept = chen.get("/api/v1/assets", params={"scope": "department"}).json()
            case = chen.get("/api/v1/assets", params={"kind": "Case", "q": "序列"}).json()
            _check(case["total"] >= 1, "按类型加关键词应能搜到问题资产")
            state["case_id"] = case["items"][0]["asset_id"]
            return [
                f"小陈可见 {total['total']} 个资产，覆盖 {len(kinds)} 种类型",
                f"部门级 {dept['total']} 个（含跨部门支援可见）",
                f"搜索「序列」命中：{case['items'][0]['title']}",
            ]

        # 4 SOP 三层合并与步骤摘要
        def step4() -> list[str]:
            sop = chen.get("/api/v1/assets", params={"kind": "Sop", "q": "银行"}).json()["items"][0]
            steps = chen.get(f"/api/v1/assets/{sop['asset_id']}/sop-steps").json()
            _check(len(steps["layers"]) == 2, f"应合并 L1+L2，实际 {steps['layers']}")
            overridden = [s for s in steps["steps"] if s["overridden_by"]]
            _check(overridden, "L2 应覆盖 L1 的步骤")
            summary = chen.get(f"/api/v1/assets/{sop['asset_id']}/sop-steps/3").json()
            _check(summary["checkpoints"], "第 3 步应有检查点")
            state["sop_id"] = sop["asset_id"]
            return [
                f"合并链路：{' → '.join(steps['layers'])}，共 {len(steps['steps'])} 步",
                f"被 L2 覆盖的步骤：{[s['number'] for s in overridden]}",
                f"第 3 步检查点：{summary['checkpoints']}",
            ]

        # 5 引用解析与权限占位
        def step5() -> list[str]:
            visible = chen.get(
                "/api/v1/assets/resolve", params={"ref": "case/oracle-to-pg-sequence-gap"}
            ).json()
            _check(visible["status"] == "ok", "公司级资产应可解析")
            hidden = zhao.get(
                "/api/v1/assets/resolve", params={"ref": "case/import-over-10k-rows-timeout"}
            ).json()
            _check(hidden["status"] == "asset_not_visible", "无权限应返回占位")
            _check("title" not in hidden, "占位不得返回标题")
            missing = zhao.get(
                "/api/v1/assets/resolve", params={"ref": "case/not-exist-at-all"}
            ).json()
            _check(
                missing == hidden | {"ref": missing["ref"]}
                or missing["status"] == hidden["status"],
                "不存在与无权限应不可区分",
            )
            recorded = chen.post(
                "/api/v1/assets/references",
                json={
                    "text": "按 [[case/oracle-to-pg-sequence-gap]] 处理，参考 [[impl/oracle-to-pg-cutover]]",
                    "source_type": "message",
                    "source_id": "msg-1001",
                    "engagement_slug": "policy-import",
                },
            ).json()
            _check(len(recorded["recorded"]) == 2, "两条引用都应记录")
            return [
                f"可见引用渲染：{visible['title']}",
                "无权限引用返回占位，且与“不存在”的响应一致（防探测）",
                f"记录引用 {len(recorded['recorded'])} 条（来源：频道消息 msg-1001）",
            ]

        # 6 七种模板新建沉淀
        def step6() -> list[str]:
            created = []
            for kind in [
                "Rule",
                "Sop",
                "Skill",
                "Solution",
                "Implementation",
                "Case",
                "Experience",
            ]:
                response = chen.post(
                    "/api/v1/harvest-candidates",
                    json={
                        "kind": kind,
                        "name": f"demo-{kind.lower()}",
                        "title": f"演示{kind}",
                        "scope": "engagement",
                        "engagement_slug": "policy-import",
                    },
                )
                _check(response.status_code == 200, f"{kind} 模板创建失败：{response.text}")
                created.append(kind)
            return [f"八种类型模板均可生成草稿：{', '.join(created)}"]

        # 7 问题单复盘 → Case 草稿（S2 必填根因）
        def step7() -> list[str]:
            blocked = chen.post(
                "/api/v1/harvest-candidates/from-issue",
                json={
                    "work_item_id": "15",
                    "title": "导入超过 1 万行报错",
                    "severity": "S2",
                    "description": "导入 12000 行报 statement timeout",
                    "root_cause": "",
                    "prevention": "",
                },
            )
            _check(blocked.status_code == 422, "S2 未填根因应被拒绝")
            created = chen.post(
                "/api/v1/harvest-candidates/from-issue",
                json={
                    "work_item_id": "15",
                    "title": "导入超过 1 万行报错",
                    "severity": "S2",
                    "description": "导入 12000 行报 statement timeout",
                    "root_cause": "单事务过大导致语句超时",
                    "prevention": "默认按 2000 行分批提交",
                    "resolution": "改为分批提交并重试当前批次",
                    "engagement_slug": "policy-import",
                    "customer_code": "CUST-A",
                },
            ).json()
            state["issue_candidate"] = created["candidate_id"]
            return ["S2 未填根因被拒（422）", f"补齐后生成 Case 草稿：{created['name']}"]

        # 8 扫描阻断与提交
        def step8() -> list[str]:
            candidate_id = state["issue_candidate"]
            candidate = chen.get(f"/api/v1/harvest-candidates/{candidate_id}").json()
            files = dict(candidate["files"])
            files["README.md"] = (
                files["README.md"]
                + "\n本问题来自测试保险公司现场，联系口令 password = hunter2000\n"
            )
            chen.patch(f"/api/v1/harvest-candidates/{candidate_id}", json={"files": files})
            checks = chen.post(f"/api/v1/harvest-candidates/{candidate_id}/checks").json()
            _check(checks["high_risk"] >= 2, f"应命中客户名称与口令两类高危：{checks}")
            blocked = chen.post(
                f"/api/v1/harvest-candidates/{candidate_id}/submit", json={"scope": "company"}
            )
            _check(blocked.status_code == 409, "有高危不得提交")
            files["README.md"] = candidate["files"]["README.md"].replace(
                "## 判断要点\n<为什么看着对其实不对>",
                "## 判断要点\n报错指向超时，容易误判为数据库性能问题，实际是提交粒度问题",
            )
            manifest = (
                candidate["files"]["asset.yaml"]
                .replace('    suitable: ""', "    suitable: 批量导入类任务")
                .replace('    notSuitable: ""', "    notSuitable: 流式接口")
            )
            chen.patch(
                f"/api/v1/harvest-candidates/{candidate_id}",
                json={"files": {"README.md": files["README.md"], "asset.yaml": manifest}},
            )
            submitted = chen.post(
                f"/api/v1/harvest-candidates/{candidate_id}/submit", json={"scope": "company"}
            )
            _check(submitted.status_code == 200, f"清理后应可提交：{submitted.text}")
            state["review_id"] = submitted.json()["review_id"]
            return [
                f"写入客户名称与口令后扫描出 {checks['high_risk']} 条高危，提交被拒（409）",
                f"清理并补齐适用边界后提交成功，分支 {submitted.json()['branch']}",
            ]

        # 9 评审合并并重新索引
        def step9() -> list[str]:
            denied = chen.post(
                f"/api/v1/reviews/{state['review_id']}/decide", json={"approve": True}
            )
            _check(denied.status_code == 403, "普通成员不应能合并公司级资产")
            merged = li.post(
                f"/api/v1/reviews/{state['review_id']}/decide", json={"approve": True}
            ).json()
            _check(merged["status"] == "merged", f"评审员应能合并：{merged}")
            _check(merged["index"]["indexed"] >= 10, f"合并后应重新索引：{merged['index']}")
            found = chen.get("/api/v1/assets", params={"q": "1 万行", "kind": "Case"}).json()
            _check(found["total"] >= 1, "新资产应出现在目录里")
            state["new_case_id"] = found["items"][0]["asset_id"]
            return [
                "普通成员合并公司级资产被拒（403）",
                f"资产评审员合并成功，重新索引 {merged['index']['indexed']} 个资产",
                f"目录里出现新资产：{found['items'][0]['title']}",
            ]

        # 10 快照与知识索引
        def step10() -> list[str]:
            first = chen.post(
                "/api/v1/snapshots",
                json={
                    "department_code": "data-intel",
                    "engagement_slug": "policy-import",
                    "industries": ["insurance"],
                },
            ).json()
            again = chen.post(
                "/api/v1/snapshots",
                json={
                    "department_code": "data-intel",
                    "engagement_slug": "policy-import",
                    "industries": ["insurance"],
                },
            ).json()
            _check(again["reused"], "同一组合应复用快照目录")
            path = Path(first["path"])
            for sub in ("rules", "skills", "sops", "knowledge"):
                _check((path / sub).exists(), f"快照缺少 {sub}/")
            index_text = (path / "knowledge" / "INDEX.md").read_text(encoding="utf-8")
            _check(
                "[[case/oracle-to-pg-sequence-gap]]" in index_text, "知识索引应包含公司级问题资产"
            )
            skills = sorted(p.name for p in (path / "skills").iterdir())
            _check(
                "policy-date-parse" in skills and "pg-vacuum-tuning" in skills, "三层技能都应进快照"
            )
            state["snapshot_sha"] = first["sha"]
            return [
                f"快照 {first['sha']}，四个目录齐全，第二次调用复用（reused={again['reused']}）",
                f"三层合并后的技能：{skills}",
                f"knowledge/INDEX.md {first['index_bytes']} 字节，裁剪={first['index_truncated']}",
            ]

        # 11 使用识别、护照与复用通知
        def step11() -> list[str]:
            usages = chen.post(
                "/api/v1/assets/usages",
                json={
                    "items": [
                        {
                            "ref": "skill/insurance-policy-import",
                            "event": "loaded",
                            "session_id": "ses-201",
                            "engagement_slug": "core-migration",
                            "work_item_id": "301",
                            "delivered_pr": True,
                            "snapshot_sha": state["snapshot_sha"],
                        },
                        {
                            "ref": "case/oracle-to-pg-sequence-gap",
                            "event": "read",
                            "session_id": "ses-201",
                            "engagement_slug": "core-migration",
                            "work_item_id": "301",
                            "delivered_pr": True,
                        },
                        {
                            "ref": "case/not-exist",
                            "event": "read",
                            "session_id": "ses-201",
                            "engagement_slug": "core-migration",
                        },
                    ]
                },
            ).json()
            _check(
                usages["accepted"] == 2 and len(usages["unknown"]) == 1,
                f"使用事件落库异常：{usages}",
            )
            _check(len(usages["notifications"]) >= 1, "首次跨项目使用且交付 PR 应产生复用通知")
            repeat = chen.post(
                "/api/v1/assets/usages",
                json={
                    "items": [
                        {
                            "ref": "skill/insurance-policy-import",
                            "event": "loaded",
                            "session_id": "ses-201",
                            "engagement_slug": "core-migration",
                        },
                    ]
                },
            ).json()
            _check(repeat["duplicated"] == 1, "同一会话同一事件应幂等")
            skills = chen.get("/api/v1/assets", params={"kind": "Skill", "limit": 50}).json()[
                "items"
            ]
            skill = next(s for s in skills if s["name"] == "insurance-policy-import")
            passport = chen.get(f"/api/v1/assets/{skill['asset_id']}/passport").json()
            _check(passport["stamps"], "护照应有印章")
            return [
                f"识别 loaded / read 两类使用，未知引用 {len(usages['unknown'])} 条不阻塞",
                f"复用通知：{usages['notifications'][0]['title']} → {usages['notifications'][0]['engagement_slug']}",
                f"护照印章 {len(passport['stamps'])} 枚，出生地 {passport['birthplace']['engagement_slug']}，"
                f"复用项目数 {passport['stats']['reuse_engagement_count']}",
            ]

        # 12 工作台：历史文档导入与线索
        def step12() -> list[str]:
            pdf = make_pdf(["Legacy migration playbook", "Step 1 freeze writes", "Step 2 verify"])
            upload = chen.post(
                "/api/v1/harvest-candidates/from-upload",
                json={
                    "kind": "Implementation",
                    "title": "历史迁移手册",
                    "filename": "legacy.pdf",
                    "content_base64": base64.b64encode(pdf).decode(),
                    "scope": "company",
                    "legacy_note": "2024 年某城商行项目",
                },
            ).json()
            _check(
                upload["extraction"]["status"] == "ok" and upload["extraction"]["chars"] > 20,
                f"PDF 文本提取失败：{upload['extraction']}",
            )
            docx = make_docx("历史方案", ["双轨并行", "三轮比对"])
            upload2 = chen.post(
                "/api/v1/harvest-candidates/from-upload",
                json={
                    "kind": "Solution",
                    "title": "历史方案文档",
                    "filename": "legacy.docx",
                    "content_base64": base64.b64encode(docx).decode(),
                    "scope": "company",
                },
            ).json()
            _check(upload2["extraction"]["status"] == "ok", "docx 文本提取失败")
            refreshed = chen.post("/api/v1/workbench/leads/refresh").json()
            board = chen.get("/api/v1/workbench").json()
            rules = sorted({lead["rule"] for lead in board["leads"]})
            _check(board["leads"], "工作台应有线索")
            _check(len(board["add_options"]) == 4, "自由添加应有四种方式")
            other = zhao.get("/api/v1/workbench").json()
            _check(not other["leads"], "线索只给本人看")
            ignored = chen.post(
                f"/api/v1/workbench/leads/{board['leads'][0]['lead_id']}/ignore"
            ).json()
            _check(ignored["status"] == "ignored", "线索应可忽略")
            return [
                f"历史 PDF 导入：提取 {upload['extraction']['chars']} 字，生成草稿 {upload['name']}",
                f"历史 docx 导入：提取 {upload2['extraction']['chars']} 字",
                f"线索 {refreshed['created']} 条，命中规则 {rules}",
                f"草稿 {len(board['drafts'])} 份，忽略线索后状态 {ignored['status']}；他人看不到我的线索",
            ]

        for number, (name, body) in enumerate(
            [
                ("初始化本地仓库与八种类型种子资产", step1),
                ("索引三类仓库并暴露无效资产", step2),
                ("目录筛选：类型 / 作用域 / 关键词", step3),
                ("SOP 三层合并与单步骤摘要", step4),
                ("[[引用]] 解析、权限占位与引用记录", step5),
                ("七种模板新建沉淀", step6),
                ("问题单复盘生成 Case 草稿", step7),
                ("敏感信息扫描阻断与提交", step8),
                ("评审权限、合并与重新索引", step9),
                ("快照三层合并与知识索引", step10),
                ("使用识别、护照与复用通知", step11),
                ("资产工作台：历史文档导入与线索", step12),
            ],
            start=1,
        ):
            step(number, name, body)

        for client in (chen, li, zhao, admin):
            client.close()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="资产中心端到端演示")
    parser.add_argument("--data-dir", default="./data/demo", help="演示数据目录（会被清空）")
    parser.add_argument("--step", default="all")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser()
    if data_dir.exists():
        import shutil

        shutil.rmtree(data_dir)
    print(f"演示数据目录：{data_dir}\n")
    results = run_demo(data_dir)
    failed = [r for r in results if not r.ok]
    print("\n" + "=" * 60)
    print(f"通过 {len(results) - len(failed)}/{len(results)} 步")
    if failed:
        for item in failed:
            print(f"  ❌ 第 {item.number} 步 {item.name}：{item.error}")
        return 1
    print("十二步全绿 = 资产中心 v0.1 本地验收通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
