# 切片看板（fde-asset-server）

状态：todo / doing / review / done / env-pending
规则：一个切片半天内、生产代码 ≤ 300 行；`make check` 绿 = 完成；评审最多 2 轮。

**整体状态（2026-09-30 夜）**：v0.1 的 21 个切片全部 done。
`make check` 绿（**133 个测试**）、`scripts/asset_demo.py` 十二步全绿、alembic upgrade/downgrade 双向验证通过。

| ID | 切片 | 状态 | 验收命令 | 落地位置 |
|---|---|---|---|---|
| S-00 | 服务拆分与骨架 | **done** | `make check` | `settings.py`、`api/app.py`、`main_asset_api.py`、`main_asset_worker.py` |
| S-01 | `bloopers` 改名 `harvest_cleanup_receipts` | **done** | `make check` | 新库直接用新名，无历史表要改 |
| S-02 | `AssetRepoPort` + `LocalGitRepo` | **done** | `pytest -q tests/platform/test_local_git_repo.py` | `platform/repo/{ports,local_git}.py` |
| S-03 | `asset.yaml` 骨架模型 + kind 注册表 | **done** | `pytest -q tests/asset/test_manifest.py` | `modules/asset/manifest.py` |
| S-04 | 七种类型章节校验与模板 | **done** | `pytest -q tests/asset/test_manifest.py` | `manifest.py` 的 `KIND_RULES`；模板在 `modules/harvest/templates.py` |
| S-05 | 索引器流水线（公司/部门/项目三类仓库） | **done** | `pytest -q tests/asset/test_indexer.py` | `modules/asset/indexer.py` |
| S-06 | 目录查询与资产 REST 接口 | **done** | `pytest -q tests/platform/test_asset_permissions.py` | `modules/asset/catalog.py`、`api/routes_assets.py` |
| S-07 | SOP 三层合并与步骤抽取 | **done** | `pytest -q tests/asset/test_sop_merge.py` | `modules/asset/sop.py` |
| S-08 | `[[kind/name]]` 解析与 resolve | **done** | `pytest -q tests/platform/test_wiki_ref.py` | `catalog.resolve_ref()` |
| S-09 | 新建沉淀：七种模板草稿 | **done** | `pytest -q tests/e2e/test_asset_flow.py` | `modules/harvest/{templates,service}.py` |
| S-10 | 问题单复盘 → Case 草稿 | **done** | `pytest -q tests/e2e/test_asset_flow.py` | `harvest/service.draft_from_issue()` |
| S-11 | 敏感信息扫描与提交前校验 | **done** | `pytest -q tests/platform/test_scan_rules.py` | `harvest/service.run_checks()` |
| S-12 | 候选提交与合并回写 | **done** | `pytest -q tests/e2e/test_asset_flow.py` | `harvest/service.{submit,decide}()` |
| S-13 | 快照落盘与 INDEX.md | **done** | `pytest -q tests/asset/test_snapshot_builder.py` | `modules/asset/snapshot.py` |
| S-14a | SOP 步骤摘要接口（资产侧） | **done** | `pytest -q tests/asset/test_sop_merge.py` | `sop.step_summary()`、`routes_work_items.py` |
| S-15a | 使用事件接收与落库（资产侧） | **done** | `pytest -q tests/asset/test_usage_detection.py` | `modules/asset/usage.py` |
| S-16 | 复用计量、护照与事件全链 | **done** | `pytest -q tests/asset/test_usage_detection.py` | `catalog.passport()`、`usage.record_usages()` |
| S-17 | 权限与可见性回归 | **done** | `pytest -q tests/platform/test_asset_permissions.py`（27 例） | `modules/asset/visibility.py` |
| S-18 | 附件与多格式文本提取 | **done** | `pytest -q tests/asset/test_attachments.py` | `manifest.py` 附件模型 + 提取器 |
| S-19 | 资产工作台接口（含自由添加） | **done** | `pytest -q tests/e2e/test_asset_flow.py` | `api/routes_harvest.py` 的 `/workbench` |
| S-20 | 沉淀线索挖掘 | **done** | `pytest -q tests/asset/test_leads.py` | `modules/leads/rules.py`（L1–L8） |

## 配套切片（其他仓库）

| ID | 仓库 | 切片 | 状态 | 验收命令 |
|---|---|---|---|---|
| S-W1 | fde-web | 资产前端重构为 `src/features/asset/` 自包含目录 | **done** | `npm run test:asset` |
| S-W2 | fde-web | 资产页面从 mock 切到真实 asset-v1 契约 | **done** | `npm run test:asset-e2e`（11 步契约 E2E） |
| S-W3 | fde-web | 资产工作台页面 | **done** | `npm run qa:asset-browser`（12 步浏览器 E2E） |
| S-14b | fde-server | orchestrator 注入 SOP 步骤摘要并回填检查点 | **blocked** | 等待"两处实现怎么合"的决定（交接说明 Q1） |
| S-15b | fde-server | 会话事件识别技能加载与知识读取，上报资产服务 | **blocked** | 同上 |

> S-W1/W2/W3 的代码在 `fde-web` 的 `feat/asset-center-web` 分支（提交 `444e71d`），尚未合并到 main。
> fde-server 按指令一行未改，两个配套切片只做了接口契约设计，见《资产中心-组织与共享设计》§六。

## 下一步

按《资产中心-v0.1交接说明》§六的顺序：先定 Q1（与 fde-server 现有 asset/harvest 模块的关系）→ Q2（成员关系接口）→ Q4（Gitea 仓库落地）→ Q3（质量等级规则）。
