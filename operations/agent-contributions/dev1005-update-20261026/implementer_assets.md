# implementer_assets · dev1005 合并（2026-10-26）

## 范围与依据

- 仓库：`fde-asset-server`，本地主线 `b7c7759`，远端 `upstream/dev1005`=`9a812e3`，共同祖先 `96a3096`。
- 依用户裁决：冲突与重复能力采用远端；本地独有且不冲突能力保留。DSH 不在本仓修改范围。
- 直接核对的公开源码：[peter-luo-git/fde-asset-server 的 9a812e3](https://github.com/peter-luo-git/fde-asset-server/tree/9a812e3)，查阅日期 2026-10-26，适用于本次 dev1005 合并；采用其 SOP 执行、默认继承、技能市场与整包机制，没有复制其他来源代码或引入第二套状态机。
- 读取 Root 提供的 `/tmp/fde-dev1005-design-20261026/reviewer_assets.txt` 和 `design-comparison.txt`，并用实际 Git 树复核。
- 状态文件位于授权写根外，已请 Root 代记，未绕过路径限制。未提交、暂存、推送、部署，未运行全量测试或全量 E2E。

## 合并行为

- 采用远端 SOP 业务规则：不参与推荐或通用包、不能直链项目/Agent、不能新设必选，旧必选记录无效；任务流程采用远端冻结版本、检查点进度与默认流程实现。
- 推荐服务同一事务中先复核当前资产可见性，再执行远端 SOP 禁关联规则；保留本地关联重放/接受推荐可见性校验、项目实例当前权限刷新与幂等解除。API 将不可见/不存在映射为404，业务拒绝映射为422。
- 本地 Git helper 保留前台自动维护，并接入远端 `core.quotepath=off`，中文技能资源通过相同调用路径读取。
- 采用远端新增技能市场、包检查、来源与试跑、依赖声明和迁移；本地专用重排设置、协议校验和安全诊断不冲突，保留。没有恢复已由主线删除的旧目录实现。
- `README.md` 同步 SOP 退出通用包、默认流程、冻结/推进/核验证据、个人及点名 Skill 挑选规则与迁移头 `0013_skill_packages`。
- 旧 `test_ask` 的 SOP 兜底断言改为 SOP 不参与推荐；新增中文路径实际列举/读取回归，以及可见/不可见 SOP 的直接关联和接受推荐交叉回归。

## 验证

- 系统解释器 `PYTHONPATH=src python3 -m unittest tests.platform.test_git_maintenance -v`：2通过；真实本地/Gitea fetch 维护均未遗留后台子进程。
- `/tmp/fde-update-asset-venv/bin/python -m pytest -q tests/test_contract.py::test_contract_covers_the_main_surfaces tests/platform/test_local_git_repo.py tests/platform/test_git_maintenance.py tests/asset/test_skill_deps.py -k 'not test_shared_skill_with_new_deps_needs_reviewer_confirmation and not test_invalid_requirements_block_submit and not test_personal_skills_with_deps_are_limited'`：16通过、3定向排除、1依赖弃用警告。
- 首轮另包括 SOP 解析/编写检查6用例，均通过；当时未排除的3个技能依赖服务测试在PG夹具 setup 报 OperationalError，总计21通过/3环境错误，非业务断言失败。随后已切换 Root 提供的独占测试PG `127.0.0.1:55433`；沙箱限制本地 socket，提升执行访问该测试库，无业务库访问。
- 相对 `HEAD` 的45个改动 Python 文件执行 `/tmp/fde-update-asset-venv/bin/ruff check` 与 `ruff format --check`：全部通过（包含远端新增与合并路径）。
- `python3 -m compileall -q src tests`：通过（编译检查，不执行测试）。`git diff --check`：通过。
- `/tmp/fde-update-asset-venv/bin/python -m alembic heads`：`0013_skill_packages (head)`；未执行真实库迁移。
- 初次使用项目生成器仅跳过数据库初始化以消除契约冲突。随后 Root 提供独占测试PG `127.0.0.1:55433`，用 `temporary_database()` 创建本例子库后真实执行 `python scripts/export_openapi.py` 和 `python scripts/export_openapi.py --check`：103路径、契约与代码一致；无mock。生成物包含本地解除能力和远端全部实际注册路由，补入远端旧快照遗漏的 showcase 三路由。
- 真实独占PG相关测试已执行：`tests/recommend/test_recommend_flow.py tests/asset/test_sop_execution.py tests/asset/test_skill_packages.py tests/asset/test_skill_deps.py tests/asset/test_showcase.py tests/asset/test_mandatory.py tests/asset/test_snapshot_builder.py tests/asset/test_ask.py tests/platform/test_agent_integration.py tests/platform/test_work_item_asset_access.py tests/platform/test_platform_identity.py tests/test_contract.py`。结果 **172 passed, 1 warning in 113.53s**，日志 `/tmp/fde-update-assets-related.log`；包含新增4项SOP/可见性组合回归，全部通过。这是合并涉及路径的相关集，不是全量测试。

## 边界

- Root 后续核实 Server 最新远端 `9abcacce` 已包含 `orchestrator/skill_deps.py` 预装配套，并已导入整合；本轮尚未做真实在线 pip 安装至 Agent 运行的端到端验收，Asset 检查通过不单独证明该链路已验收。
- README 历史现场、全量测试与部署记录保留原时点，不能作为本轮通过证据。
- OpenAPI 已通过真实生成器同步，Root 已完成 Specs 契约同步。冲突解决的暂存与最终文审由 Root 收口。
