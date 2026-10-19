# Asset 审批列表与生成契约修复贡献

- Agent：`implementer_asset_sync_guards`（`gpt-6.1-sol`）。
- 工作树：`/opt/fde/.run/worktrees/github-dev1005-asset-20261019`。
- 基线：Root 将 `origin/dev1005`（`45a3606`）合并到本地 `132de0d`，当前合并暂存由 Root 管理；本 Agent 未执行任何 Git 写操作。
- 范围：审批列表 P0、生成契约正文接口、指定回归、P1 后续切片日志；Root 后继授权仅格式化远端新增 `tests/platform/test_gitea_push_hook.py`。

## 改动与根因

`GET /api/v1/visibility-requests` 原来允许任意 `view` 字符串。服务层对未知值查询全部 open 申请，却只对 `view == "todo"` 执行审批人过滤。真实 PostgreSQL 回归证明无权查看资产的 `zhao` 可借 `view=all` 得到标题和申请备注。

路由现在只接受 `todo`、`mine`、`decided`，非法值返回 422；服务层在查询前同样拒绝未知值并抛出 `RequestError`（422）。现有 `mine` 仅本人申请、`todo` 仅本人有权审批且非本人申请、`decided` 仅本人处理的语义保持不变，未改审批角色判定或扩大审批权。

新增三个真实数据库回归分别覆盖 API 非法值、直接服务层非法值和合法三视图的请求人/审批人范围，包含本人申请不能进入自己的 todo、不同部门审批范围和拒绝后的历史列表。

仅执行现有 `scripts/export_openapi.py` 生成 `contracts/asset-v1.yaml`，补齐已实现的 `GET /api/v1/assets/{asset_id}/body` 并导出合法视图枚举，未手改生成契约。Root 负责同步 Specs。

## 验证证据

原始日志目录：`/opt/fde/.run/message-history-repair-sync-20261019/implementer_asset_sync_guards/`。

- `tdd-red.log`：修复前 **2 failed / 1 passed**，失败分别是 API 实际泄漏、服务层未拒绝；属于预期红灯。
- `tdd-green-configured.log`：`python -m pytest -q tests/asset/test_versions_and_requests.py tests/test_contract.py` **13 passed / 1 warning**，退出 0。
- `export-openapi.log`：生成器成功导出 **80 个路径**，退出 0。
- `make-check.log`：lint/149 文件格式/compileall 通过；磁盘 TMPDIR 下 pytest 111 passed/3 warnings 后，经 Root 授权只对核实的本轮 pytest 发 SIGINT，退出 2、runner 清理容器；属于未完成检查，不计通过。
- `make-check-tmpfs.log`：按 Root 授权使用本轮 `/dev/shm` 唯一临时子目录运行 `make check RUN=env`，**退出 0**：ruff 通过、149 文件格式通过、compileall 通过、**509 passed / 1 skipped / 3 warnings**（975.89 秒）、Alembic `0011_checkup_findings (head)`。启动前容量 3.2 GiB 可用，结束容器和准确临时目录均已清理。
- 唯一跳过：`tests/asset/test_rerank.py:120` 真实模型测试（没有配置模型网关，且本轮显式 `FDE_ASSET_LLM_LIVE_TEST=0`）；3 项警告为已有 anyio 和 tarfile 弃用提示，另有 pytest-asyncio 配置弃用提示。未调用真实模型。
- `git diff --check` 已通过；Root 后继授权的 `test_gitea_push_hook.py` 格式化仅改变折行。
- Root 独立补验 `uv lock --check --offline --python /usr/bin/python3` 退出 0、44 packages（`/opt/fde/.run/message-history-repair-sync-20261019/asset-lock-check.log`），使用 `/opt/fde/.tools/uv-x86_64-unknown-linux-gnu/uv` 和本轮独立缓存，无 sync/venv 修改。此项由 Root 执行，非本 Agent 的 `make check` 条件门禁。

测试只读复用 `/opt/fde/.run/smoke-ci-20261014/asset-venv`，其 pytest 8.3.4、ruff 0.9.2、pytest-asyncio 0.25.2、psycopg 3.2.3、FastAPI 0.115.6 和 SQLAlchemy 2.0.36 等与当前锁定版本一致；没有安装/升级依赖，没有改共享虚拟环境或项目锁文件。

每轮使用随机命名、有本任务标签的 `postgres:17.11-alpine` 临时容器，`--pull=never`、无共享卷、PGDATA tmpfs；PostgreSQL 仅监听 `127.0.0.1` 随机端口，用例各自建删 `asset_test_*` 库，runner `finally` 删除本轮容器。显式跳过环境文件，清除继承的 `FDE_ASSET_*` 后仅设置本轮隔离数据库配置；隔离工作树没有 `.env.local`，未读取真实配置或调用真实模型。

最终只读核验：本轮 `fde.task=message-history-repair-sync-20261019-asset-guards` 标签容器数量为 0；`/dev/shm/asset-sync-guards-d35bc89734c5-93f2yt4e` 已不存在。未创建工作树 `.venv` 软链接，无需删除虚拟环境资源。

## 失败尝试与限制

- Docker 默认 bridge 首次启动失败，原因是 `docker0` 不存在；原错误和环境失败见 `environment-failures.log`。已删除准确命名的失败容器，Root 批准改用 host 网络并限定 PG 只监听 localhost，没有修改 Docker 全局网络。
- 共享运行虚拟环境缺 pytest；没有改它，改只读复用既有完整 Asset 测试环境。
- 首次修复后定向运行 **12 passed / 1 failed**（`tdd-green.log`），唯一失败是 PostgreSQL-only 的契约导出需要 `FDE_ASSET_DATABASE_URL`；测试 runner 补成本轮临时 PG 后，13 项重跑全绿。生成器单独无数据库配置的原始错误也已核实，没有改生成器来绕过要求。
- `mandatory.py:add` 的并发 `IntegrityError` 捕获后没有 savepoint/事务回滚，继续查询可能命中 PostgreSQL aborted transaction。依仓库规则作为 P1 仅写 `tasks/LOG.md` 一行，留给后续独立并发切片，本轮未修复也未宣称验收。
- 未改 `identity.py`、Server、Specs、main；未提交、推送、部署或重启现有服务。Root 后续执行最终文档审核、集成和提交。

## 交付结论

**通过**。P0 修复与生成契约已获定向和完整检查验证；P1 并发事务问题仍属已登记的后续切片，不在本轮实现范围。Root 负责最终实际 diff 验收、保留其他 Agent 文档变更、集成并本地提交。
