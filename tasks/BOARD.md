# 切片看板（fde-asset-server）

状态：todo / doing / review / done / env-pending
规则：一个切片半天内、生产代码 ≤ 300 行；`make check` 绿 = 完成；评审最多 2 轮。

**整体状态（2026-10-06）**：v0.1 的 21 个切片全部 done；10-01 之后按《资产中心-推荐与应用资产设计》§3.5 的四批排期又做了 15 个切片（见下方「v0.1 之后」）。
后端全量 **343 通过 / 3 跳过**（跳过的 3 个需要 docker 与 docker compose）、ruff 与 `alembic heads` 通过。
还没做的和被 fde-server 卡住的，统一记在 fde-specs《资产中心-后续工作清单》。

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

## v0.1 之后（2026-10-01 起）

编号沿用《资产中心-推荐与应用资产设计》里的切片号；没有编号的用 X- 开头。

| ID | 切片 | 状态 | 验收命令 | 落地位置 |
|---|---|---|---|---|
| Q3 | 质量等级按复用自动评级（铜/银/金） | **done** | `pytest -q tests/asset/test_grading.py` | `modules/asset/grading.py` |
| X-1 | 草稿附件原件、按字段改元数据、评审流程可见 | **done** | `pytest -q tests/asset/test_candidate_attachments.py tests/asset/test_candidate_review.py` | `modules/harvest/service.py`、`api/routes_harvest.py` |
| X-2 | 目录按负责人筛选（个人 / 部门） | **done** | `pytest -q tests/asset/test_owner_filter.py` | `modules/asset/catalog.py` |
| R-1…R-3 | 推荐中心：算、推、收件箱、接收即关联 | **done** | `pytest -q tests/recommend` | `modules/recommend/service.py`、`api/routes_recommend.py` |
| F5+F7 | 反馈闭环、拒绝原因回流、负责人待办、系统配置 | **done** | `pytest -q tests/asset/test_feedback_and_settings.py` | `modules/asset/feedback.py`、`platform/settings_store.py` |
| A-1…A-4 | 应用类资产 A 档：登记 + 探活 | **done** | `pytest -q tests/asset/test_application_assets.py` | `modules/app/health.py`、`manifest.py` 的 `Application` |
| B-1…B-4 | 容器化演示 B 档：单容器 + compose、上传审核、手动启停 | **env-pending** | `pytest -q tests/asset/test_app_deploy.py tests/asset/test_compose_deploy.py` | `modules/app/{deploy,compose,bundle}.py`、`platform/runner/`；等 E5、E6 |
| F1a | 内容理解重排：rerank 模型全量打分 + 门槛截断 + 前 3 条写理由 | **done** | `pytest -q tests/asset/test_matching.py tests/asset/test_rerank.py tests/asset/test_ask.py` | `modules/asset/{matching,rerank}.py`、`platform/llm/`；2026-10-04 真实调通，13 份资产 0.2–0.3 秒 |
| X-3 | 推荐结果缓存 | **done** | `pytest -q tests/platform/test_cache.py` | `platform/cache.py` |
| F4+F12 | 资产体检（四条规则）+ 跨项目空白识别 L9 | **done** | `pytest -q tests/asset/test_checkup.py tests/asset/test_leads.py` | `modules/asset/checkup.py`、`modules/leads/rules.py` |
| F6+F13 | 变更通知（章节级 diff）+ 订阅，共用站内收件箱 | **done** | `pytest -q tests/asset/test_notify.py` | `modules/notify/service.py`、`api/routes_notify.py` |
| F8a | 关系：自动建链 + 按可见性读一层邻居 | **done** | `pytest -q tests/asset/test_relations.py` | `modules/asset/relations.py` |
| F9 | 客户级作用域 | **done** | `pytest -q tests/platform/test_customer_scope.py` | `visibility.py`、迁移 `0006_customer_scope` |
| X-4 | 旧库启动时自动补齐新增的列 | **done** | `pytest -q tests/platform/test_schema_upgrade.py` | `core/db.py` 的 `add_missing_columns()` |
| F1b | 按问题检索（后端）：权限过滤后全量语义打分，不做字面粗筛 | **done** | `pytest -q tests/asset/test_ask.py` | `modules/asset/ask.py`、`GET /api/v1/assets/ask` |
| F1b-web | 按问题检索（前端搜索框）：默认按问题找，可切回按关键词筛 | **done** | `npm run test:asset`、`npm run qa:asset-browser` | fde-web `pages/AssetCatalogPage.tsx` |
| F8b | 关系图谱：子图接口（1–3 层、60 节点上限、不可见资产不当跳板）+ 前端 SVG 图 | **done** | `pytest -q tests/asset/test_relations.py`；`npm run test:asset` | `modules/asset/relations.py` 的 `graph()`、`GET /api/v1/assets/{id}/graph`；fde-web `components/AssetRelationGraph.tsx` |
| X-5 | 演示服务启动时加载 `.env.local`；测试用 `FDE_ASSET_SKIP_ENV_FILE` 跳过 | **done** | `npm run test:asset-e2e`（加 `FDE_E2E_SEMANTIC=1` 验真实模型） | `settings.load_local_env()`、`scripts/serve_seeded.py` |
| X-6 | 草稿标识自动生成（`case-20261005-a3f2`），可手改；上传历史文档不再一律落成 legacy-import | **done** | `pytest -q tests/asset/test_draft_naming.py` | `harvest/service.generate_name()` |
| X-7 | 从线索起草后线索标成已起草，草稿来源记为线索 | **done** | `pytest -q tests/asset/test_leads.py` | `harvest/service.create_draft()` 的 `lead_id` |
| X-8 | 开发模式身份列表接口，供页面右下角切换演示身份 | **done** | `pytest -q tests/platform/test_dev_identities.py` | `GET /api/v1/dev/identities`；fde-web `components/DevIdentitySwitcher.tsx` |
| X-9 | 重建索引时内容没变就不动更新时间；详情页显示入库时间与最近更新 | **done** | `pytest -q tests/asset/test_indexer.py` | `indexer._upsert()` |
| X-10 | 沿用旧数据启动时补齐名单文件里后来才有的部分；老王兼 finance 部门主管 | **done** | `pytest -q tests/platform/test_directory_refresh.py` | `scripts/seed_assets.refresh_directory()` |
| X-11 | 后台定时任务：有新提交才重建索引、定时扫线索、定时探活 | **done** | `pytest -q tests/platform/test_scheduler.py` | `scheduler.py`、`main_asset_worker.py`、`serve_seeded.py --with-worker` |
| X-12 | 对外契约 `contracts/asset-v1.yaml` 由代码导出，测试拦住不一致 | **done** | `pytest -q tests/test_contract.py` | `scripts/export_openapi.py` |
| F2 | Agent 检索工具（资产侧）：MCP 接口 `search_assets` / `get_asset` | **env-pending** | `pytest -q tests/platform/test_mcp.py` | `api/routes_mcp.py`；待 fde-server 挂载并在真实 dsh 上验证 |
| X-13 | 删草稿（线索回到工作台）、资产下架/废弃/恢复、应用可被引用、评审通知 | **done** | `pytest -q tests/asset/test_small_gaps.py` | `harvest/service.delete_draft()`、`modules/asset/lifecycle.py`、`notify/service.notify_review()` |
| F3 | 启动资产包 | 不做 | — | 2026-10-04 决定暂不做，理由见《资产中心-后续工作清单》§一 |
| F11 | 度量看板 | todo | — | 2026-10-04 决定这一轮先不做 |

> 前端对应的页面在 `fde-web` 的 `feat/asset-center-web` 分支（最新提交 `b41ff6e`），仍未合并到 main。

## 下一步

1. 等 fde-server 开工：成员关系接口、使用上报、SOP 步骤注入、挂载 MCP（《资产中心-后续工作清单》§二）；
2. 用真实资产重新校语义相关度门槛（同上 §四 P2）；
3. 盘点出来还没做的 7 个缺口见同上 §1.1。
