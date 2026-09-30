# 切片看板（fde-asset-server）

状态：todo / doing / review / done / env-pending
规则：一个切片半天内、生产代码 ≤ 300 行；`make check` 绿 = 完成；评审最多 2 轮。

| ID | 切片 | 状态 | 分支 | 验收命令 | 环境阻塞 | 备注 |
|---|---|---|---|---|---|---|
| S-00 | 服务拆分与骨架 | doing | main | `make check` | — | 骨架已建，待迁代码 |
| S-01 | `bloopers` 改名 `harvest_cleanup_receipts` | todo | | `make check` | — | |
| S-02 | `AssetRepoPort` + `LocalGitRepo` | todo | | `pytest -q tests/platform/test_local_git_repo.py` | — | |
| S-03 | `asset.yaml` 骨架模型 + kind 注册表 | todo | | `pytest -q tests/asset/test_manifest.py` | — | |
| S-04 | 七种类型章节校验与模板 | todo | | `pytest -q tests/asset/test_kind_rules.py` | — | |
| S-05 | 索引器流水线（公司/部门/项目三类仓库） | todo | | `pytest -q tests/asset/test_indexer.py` | — | |
| S-06 | 目录查询与资产 REST 接口 | todo | | `pytest -q tests/platform/test_asset_routes.py` | — | |
| S-07 | SOP 三层合并与步骤抽取 | todo | | `pytest -q tests/asset/test_sop_merge.py` | — | |
| S-08 | `[[kind/name]]` 解析与 resolve | todo | | `pytest -q tests/platform/test_wiki_ref.py` | — | |
| S-09 | 新建沉淀：七种模板草稿 | todo | | `pytest -q tests/asset/test_harvest_drafts.py` | — | |
| S-10 | 问题单复盘 → Case 草稿 | todo | | `pytest -q tests/asset/test_issue_retro_draft.py` | — | |
| S-11 | 敏感信息扫描与提交前校验 | todo | | `pytest -q tests/platform/test_scan_rules.py` | — | |
| S-12 | 候选提交与合并回写 | todo | | `pytest -q tests/asset/test_candidate_submit.py` | — | |
| S-13 | 快照落盘与 INDEX.md | todo | | `pytest -q tests/asset/test_snapshot_builder.py` | — | |
| S-14a | SOP 步骤摘要接口（资产侧） | todo | | `pytest -q tests/asset/test_sop_injection.py` | — | 配对 S-14b |
| S-15a | 使用事件接收与落库（资产侧） | todo | | `pytest -q tests/asset/test_usage_detection.py` | — | 配对 S-15b |
| S-16 | 复用计量、护照与事件全链 | todo | | `pytest -q tests/asset/test_passport_and_reuse.py` | — | |
| S-17 | 权限与可见性回归 | todo | | `pytest -q tests/platform/test_asset_permissions.py` | — | |
| S-18 | 附件与多格式文本提取 | todo | | `pytest -q tests/asset/test_attachments.py` | — | |
| S-19 | 资产工作台接口（含自由添加） | todo | | `pytest -q tests/platform/test_workbench.py` | — | |
| S-20 | 沉淀线索挖掘 | todo | | `pytest -q tests/asset/test_leads.py` | — | |

## 配套切片（其他仓库）

| ID | 仓库 | 切片 |
|---|---|---|
| S-14b | fde-server | orchestrator 注入 SOP 步骤摘要并回填检查点 |
| S-15b | fde-server | 会话事件识别技能加载与知识读取，上报资产服务 |
| S-W1 | fde-web | 资产前端重构为 `src/features/asset/` 自包含目录 |
| S-W2 | fde-web | 资产页面从 mock 切到 asset-v1 契约 |
| S-W3 | fde-web | 资产工作台页面 |
