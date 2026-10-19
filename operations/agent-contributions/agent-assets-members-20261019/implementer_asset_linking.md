# implementer_asset_linking — Asset 关联解除与实时归属

- 任务：`agent-assets-members-20261019`；角色：后端实现。
- 运行时逻辑 ID：`/root/implementer_asset_linking`；平台未提供独立 UUID。
- 工作树：`/opt/fde/.run/worktrees/agent-assets-members-asset-20261019`。
- 分支：`codex/agent-assets-members-20261019`；基线：`96a3096e37c5745a3bd9c8069f88e6208cece7cd`。
- 状态日志：`/root/.codex/agent-state/fde/agent-assets-members-20261019/implementer_asset_linking/AGENT.md`。
- 开始：2026-10-19；结束：完整验证与隔离资源清理完成。交接：Root。

## 实际改动

| 文件 / 定位 | 目的 |
| --- | --- |
| `src/fde_asset/api/routes_recommend.py` | 增加 typed `POST /api/v1/recommend/unlink`；校验目标当前负责人或管理员；项目实例写入实时刷新平台目录、必须仍有有效目标；接受推荐也重新检查当前实例归属；关联和接受时资产不存在/不可见统一404。 |
| `src/fde_asset/modules/recommend/service.py` / `unlink`、`link`、`decide` | 在单个事务中删除指定目标类型、目标 ID、资产 ID 三元组关联；重复解除返回 false；保留资产和其他目标关联，真实删除时记录 `asset.unlinked`。直接关联与接受推荐使用既有 `visibility_clause(principal)` 在当前事务、写入与幂等返回前检查资产可见性。 |
| `src/fde_asset/platform/identity.py` | 增加绕过短缓存的目录刷新入口，依赖失败不使用旧缓存授予写权限；目标选择 GET 同样刷新，使新创建实例立即可被选择。 |
| `contracts/asset-v1.yaml` | 用仓库导出器生成新增解除接口与请求字段契约。 |
| `tests/recommend/test_recommend_flow.py` | 真实 PG 持久化验证：本人解除、他人拒绝、其他目标保留、重复解除幂等、解除后可重新关联；API和直接服务层不可见/不存在/restricted/管理员受限/已删除拒绝、关联重放复核、接受拒绝保留sent。 |
| `tests/platform/test_platform_identity.py` | 模拟平台 HTTP 契约，真实资产数据库验证归属转移后旧 owner 关联/解除/接受推荐被拒绝、当前 owner 可接受、缺失项目实例管理员也不能修改。 |
| `tasks/LOG.md` | 按 Root 合同追加实现验收结果一行，保留 docs 的两行审查历史；没有新增评审文件。 |

## 验证证据

- 解除接口红灯：缺少路由返回404；添加后推荐/目录定向首轮 `36 passed / 1 failed`，失败为契约测试未配置 `FDE_ASSET_DATABASE_URL`，已在后继运行补本任务独立 PG。
- 缓存转交红灯 `2 failed`（旧负责人 link/unlink 错误200），修复后 `2 passed`。
- 接受推荐红灯（旧收件人错误200），修复后与转交集合共 `3 passed`。
- 缺失实例管理员写入红灯 `2 failed`（错误200），已增加当前目录存在性检查。
- 新建目标选择红灯：已有热缓存下 `pa-new` 缺失；已使 GET targets 读取最新目录。
- 目录/解除阶段定向三文件 `43 passed, 1 warning in 165.75s`（`test_platform_identity.py`、`test_recommend_flow.py`、`test_contract.py`）；runner exit0，对应独占临时目录已清理。后续 Root 发现既有服务层漏校验资产可见性，已扩大必要修复；该43项不代表后继最终代码验收。
- 可见性补充红灯：`8 failed, 13 deselected, 1 warning in 35.97s`；日志 `/opt/fde/.run/agent-assets-members-20261019/asset-visibility-red.log`。不存在、不可见、restricted（含管理员）、删除、关联幂等重放及接受推荐原实现均错误返回200。
- 可见性补充沿用既有规则，不调整 audience 或 restricted 策略；最终51项定向 `51 passed, 1 warning in 120.48s`，runner exit0且已清理临时目录。命令为既有 asset-venv 的 `python -m pytest -q` 三文件，设置本任务隔离 `FDE_ASSET_DATABASE_URL`、`TMPDIR` 与 `PYTEST_ADDOPTS=--basetemp=...`。
- 最终 `make check` exit0：`524 passed, 1 skipped, 3 warnings in 1406.46s`；Ruff lint、149文件格式和 compileall 通过，唯一 head `0011_checkup_findings`。skip 为 `tests/asset/test_rerank.py:120` 未配置模型网关，3警告为已有 Starlette/anyio 与 tarfile 弃用警告；没有执行真实模型验收。
- 最终实际命令由下述 runner 以 argv 启动 `make check RUN=`；子进程 `PATH` 指向既有 asset-venv，设置任务隔离 `FDE_ASSET_DATABASE_URL`、`TMPDIR` 与 `PYTEST_ADDOPTS`，没有 uv 时 Makefile 条件锁校验未触发；本轮未改依赖或锁文件。
- 先前运行因新增必要权限变更或切换获批内存临时目录而主动中断，不计通过；可见性修复前的中断原日志另保留为 `asset-full-pre-visibility-interrupted.log`。
- Root 授权在 `/dev/shm` 唯一目录运行，runner 为 `/opt/fde/.run/agent-assets-members-20261019/run_asset_checks.py`，使用 argv 数组和 `TemporaryDirectory` 上下文清理；定向日志 `asset-targeted-tmpfs.log`，完整日志 `asset-full-tmpfs.log`，无新建 mount 或全局缓存设置修改。
- 测试使用现有 `/opt/fde/.run/smoke-ci-20261014/asset-venv`，无依赖安装。隔离 PG 容器 `fde-agent-assets-test-pg-host-20261019` 仅监听 `127.0.0.1:55432`，测试结束按精确名称停止并自动移除；已核验该名称无容器，两个本任务 tmpfs 前缀下均无遗留目录，未接触业务数据库。
- 默认 bridge 缺少 `docker0`，首次容器启动失败后 `--rm` 已清理；改用 host 网络并限制 PG listen_addresses 为 `127.0.0.1`。

## 边界

- 无迁移、无 Git 写操作、无真实服务修改或部署；推荐数据库历史与资产正文保留。
- 非 `pa-*` 推荐接受权限语义保持兼容；项目实例只使用当前目录事实。
- Root 负责文档影响审查、E2E、集成、提交、部署及真实服务验收。
