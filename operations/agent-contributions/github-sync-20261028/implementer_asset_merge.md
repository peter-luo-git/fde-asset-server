# implementer_asset_merge · Asset dev1005 合并（2026-10-28）

角色：实现与相关离线验证；不充当独立复审。工作树 `/home/hbw/projects/FDE/.worktrees/github-sync-20261028/fde-asset-server`，分支 `codex/github-sync-20261028`；本地起点 `697fc591ece6dcaebbf63b588f682d5a35dbe174`，合入 `peter-luo-git` 的 `9e39638f1ef127f7b9572c6a992ba68c7f330b9a`（7提交）。Root负责日志越权部分、独立复核、暂存、提交及main集成。未派生、暂存、提交、修改main、推送或部署。

## 冲突策略与兼容

- `contracts/asset-v1.yaml`：保留本地解除关联、404声明和远端草稿附件文字版/重试路由；真实路由生成结果一致，未删本地接口。
- `src/fde_asset/api/routes_recommend.py`：并列保留本地严格解除请求、实时目标归属授权及远端项目可见性标记。项目关联新增范围提示前先检查当前主体可见性，隐藏/缺失资产仍统一404，避免422提示泄露范围；服务事务内原可见性复核继续保留。
- `src/fde_asset/modules/recommend/service.py`：并列保留幂等unlink和远端 `visible_to_project` / `scope_label`；项目判定补齐快照已有的 deleted/valid/restricted/lifecycle排除，保护新增原件接口。
- `tests/recommend/test_recommend_flow.py`：合并双方测试，并将当前主体可见性拒绝矩阵同时覆盖Agent和项目目标。
- 远端知识附件文字版、图片理解队列、草稿导入限制、选中部门默认负责方及原件接口均保留；本地引用/工作项权限代码未被替换。
- `routes_assets.py` 的图片理解retry复用 `lifecycle.may_manage`；`routes_harvest.py` 的retry复用草稿文件编辑的作者本人+draft约束，读权限不等于重排模型任务权限，GET继续允许原可读主体。
- `tests/platform/test_agent_integration.py` 增加受限、无效、草稿、弃用、归档、删除及其他项目的原件拒绝矩阵。新原件接口沿用服务密钥、项目成员、知识类型与已登记附件校验。
- 远端 `pyproject.toml` 新增 `pymupdf==1.28.2` 未同步锁；使用只读既有uv0.13.1重新生成 `uv.lock`，仅新增该包，45包解析一致。

对生产API的影响：既有解除/引用权限保持；项目负责人可见、但项目不可带入的资产关联返回422；当前主体不可见仍404。公开附件文字版和重试接口需前端配套；内部 `/internal/v1/agent-attachment` 不进入公开OpenAPI，需Server工具配套。数据库head新增 `0015_attachment_texts`；未接触生产库或执行生产迁移。

## 来源与机制核对

本轮是固定Git历史集成，没有另行设计Agent框架或复制外部应用源码。2026-10-28以合入的7个提交及本地 `snapshot.select_assets` / `visibility_clause` 为直接实现依据；远端已实现能力按原执行器、资产事务和权限边界适配，不引入第二套状态。

`pymupdf` 固定1.28.2从官方PyPI JSON及其wheel获取（`https://pypi.org/pypi/pymupdf/1.28.2/json`），实测wheel SHA256 `397d6715c1f0df7548a92d0afd8ce370fc48fa47aeefac16be2bc04a16a8227f`；仅解压在本任务 `/tmp` 环境。包METADATA声明 `Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License`，来源 `https://github.com/pymupdf/pymupdf`；保留远端固定依赖，未复制外部源码，不作许可证适用性判断。

## 验证

使用 `verification-before-completion`；兼容问题先按 `systematic-debugging` / `test-driven-development` 记录RED再修复。按用户本轮仅相关测试要求，不运行AGENTS默认全量 `make check`、全量pytest/E2E或真实模型。

临时Python：`/tmp/fde-asset-sync-20261028-venv/bin/python`，只读继承既有锁定依赖，新增pymupdf仅在本临时环境。Ruff：`/home/hbw/projects/FDE/.run/full-e2e-20261019/qa_asset/source/.venv/bin/ruff`；uv：`/home/hbw/projects/FDE/.run/full-e2e-20261019/uv`。未运行共享环境安装或 `uv sync`。

实际命令与结果：

1. `python3 /tmp/fde-assets-sync-20261028-gate.py red`：预期 **7 failed / 10 passed**，两条项目link越过404与五条原件错误放行；日志 `/tmp/fde-assets-sync-20261028-verification/red/related-tests.log`。此前一次环境预检报缺pytest，补上只读依赖pth后重跑，环境错误未作RED证据。
2. `python3 /tmp/fde-assets-sync-20261028-gate.py green`：**109 passed / 0 skipped / 1 warning**，71.90秒。相关文件为 `tests/recommend/test_recommend_flow.py`、`tests/asset/test_attachment_vision.py`、`tests/asset/test_attachments.py`、`tests/asset/test_snapshot_builder.py`、`tests/asset/test_draft_naming.py`、`tests/asset/test_candidate_attachments.py`、`tests/platform/test_agent_integration.py`、`tests/platform/test_agent_bundle_compat.py`、`tests/platform/test_schema_upgrade.py`、`tests/test_contract.py`。既有Starlette anyio弃用警告，pytest-asyncio另提示默认fixture loop scope未显式设置。
3. 同一green入口依次执行 `python -m alembic upgrade 0014_skill_deps_status`、`upgrade head`、`attachment_texts` 12列/created_at非空核验、`downgrade 0014_skill_deps_status`、再 `upgrade head`：均exit0。
4. 同一入口 `python scripts/export_openapi.py --check`：契约与代码一致；`python -m alembic heads`：唯一 `0015_attachment_texts (head)`，均exit0。
5. `ruff check` / `ruff format --check` 对本次19个变更Python文件（含新迁移）均exit0。`git diff --check HEAD` exit0；四个冲突文件无冲突标记。索引仍UU，按约定交Root暂存确认。
6. `uv lock --cache-dir /tmp/fde-asset-sync-20261028-uv-cache` 生成锁；`uv lock --check --offline --cache-dir /tmp/fde-asset-sync-20261028-uv-cache` exit0（45包）。最初空缓存离线检查无法解析，生成锁后离线复查通过。

测试PG为每次唯一任务容器，缓存镜像 `postgres:16-alpine`（实际ID `sha256:deb28ee471183cb0c303113dac9b2433c51eeeeb678def7af9a9ec5820367815`），随机127.0.0.1端口、tmpfs数据目录、随机测试凭据、精确归属标签，finally复核标签后只删除本任务容器。red/green结果JSON均 `owned_pg_removed: true`；未使用默认端口或现有业务容器。入口可由Root重复执行，全部命令均以仓库工作树为cwd。密码未写入日志或贡献。

## 独立审查后retry权限修复

Root转来Web/Asset跨仓审查：Web已入库资产按 `asset.can_manage` 显示retry，草稿非draft为只读；Asset原POST仅有读校验。复核发现 `get_candidate` 允许管理员、评审员读取他人草稿，不能当写授权；复用现有 `put_skill_file` 的状态+作者校验（非draft409，非作者403），而非扩大通用草稿读权限。资产retry在可见性404后查询资产并调用现有 `lifecycle.may_manage`，无权403。所有拒绝都在 `include_target` / `enqueue(force=True)` 之前。未调整图片重试指纹。

新增测试对 `{}` 重排与 `{target}` 强制包含两条分支，验证可读用户仍能GET但不能POST、评审员/管理员读取他人草稿不能重试、作者在submitted/merged/rejected不能重试；同时验证管理者、管理员和草稿作者正向路径。拒绝时status维持done且include_json不变。

- `python3 /tmp/fde-assets-sync-20261028-gate.py retry-red`：**12 failed / 6 passed / 8 deselected**，15.52秒；错误均原200代替403/409，日志 `retry-red/related-tests.log`。
- `python3 /tmp/fde-assets-sync-20261028-gate.py retry-green`：仅跑 `tests/asset/test_attachment_vision.py`、`tests/asset/test_candidate_attachments.py`、`tests/test_contract.py`，**37 passed / 0 skipped / 1 warning**，24.11秒；无需重跑此前109相关集。日志 `/tmp/fde-assets-sync-20261028-verification/retry-green/related-tests.log`。
- 两轮专用PG结果均 `owned_pg_removed: true`。三个affected Python文件Ruff check/format最终exit0；新状态校验不改变路由schema，真实契约一致性测试通过。README补充retry403/409边界。

## 文档影响

README update范围由Root授权；读取 `docs-impact-control` 及impact-guide，仅据当前实现更新必要内容，不作为独立文审。README至1.4：远端SHA、0015迁移头/依赖锁、知识附件文字/图片理解队列、原件接口、导入限制、默认负责方、项目关联404/422及字段。既有历史测试和部署证据保留。公开契约按生成结果一致性测试核验；Specs为独立仓，由Root按契约同步边界处理。没有新增配置项或部署动作，`.env.example`、历史环境阻塞与旧验收记录不改。仓无独立Markdown检查命令；窄验证为实现/路由逐项核对及 `git diff --check HEAD`。

## 边界与待复核

结论：相关离线检查通过，交Root独立审查；不等于全量、真实图片模型、生产升级或部署验收。临时PG容器均已清理，临时依赖和日志留给Root复用。

源码推断（未验证/未扩展修复）：远端 `done_marker` / snapshot指纹只记录已完成附件SHA列表；同SHA重新理解完成而期间未组包时，旧known_revision可能命中。已通知Root供独立审查。任何后继修复需单独相关回归；本轮没有把推断记为已证实问题。
