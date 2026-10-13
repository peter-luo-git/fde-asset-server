# implementer_agent_directory：真实平台 Agent 推荐接线

- 工作树：`/opt/fde/.run/worktrees/quickstart-function-gaps-asset`；分支 `codex/quickstart-function-gaps`；输入基线 `423d840`。
- 贡献：`platform/identity.py`、`tests/platform/test_platform_identity.py`，以及 Root 明确追加授权的 `api/app.py`、`api/routes_recommend.py`。无 Git 写操作、部署、模型调用或派生。
- `PlatformDirectory.agents()` 与用户/项目共享服务密钥鉴权目录和默认30秒缓存。生产模式忽略保留兼容的 `local` 参数，不返回演示预设；有效空数组正常返回空目录。
- 缺少/错误 `agents`、无效 JSON、坏目录结构、错误密钥、网络故障均失败关闭。缓存过期后不继续使用旧目录。Agent 仅消费 `title/description/role/owner/department_code`，不保存上游多余运行字段。
- 统一 `PlatformUnavailable` 转安全503，不回显内部异常、URL或响应正文。原认证401/403处理不变。
- `send/link` 使用目录原始归属授权；`compute/explain` 保留既有只读匹配上下文覆盖，不因此获得写入权限。部门主管不能因空部门相等管理目标，也不能管理无主 Agent；内建无主 Agent 仅管理员管理。

红绿证据：原Asset venv缺pytest；Server venv缺pptx，先得到13个夹具错误。使用已有Server测试工具与Asset现有site-packages组合只读运行，不安装或修改环境。接线前 `7 failed, 20 passed`；统一handler前真实 `500→503` 红测 `2 failed`；目录解析边界红测 `3 failed`；空部门/伪造部门授权红测 `2 failed`（分别列入无主内建部门视图、send越过403进入422）。最终身份+推荐流程 `33 passed, 1 warning`，已有普通负责人/部门推送/管理员行为回归通过。警告为既有 Starlette/pytest-asyncio 弃用提示。

完整检查：两次 `make check` 静态阶段均通过。首轮 pytest 出现 `tests/asset/test_app_deploy.py::test_real_container_runs_and_serves` 的 `E`（由当前 `.pytest_cache/v/cache/lastfailed` 精确核对），且启动于最终权限测试写入前，精确中断本人旧pytest后尝试重跑；中断时 pytest `tmp_path` teardown 另有 `KeyError: StashKey`，原Docker错误堆栈未输出，根因未定位。第二次检查因 Root 明确要求统一资源下串行验证而精确中断本人进程，不能声明全仓通过；最终完整 `make check` 交 Root 串行执行。

命令：`PYTHONPATH=src:/opt/fde/fde-asset-server/.venv/lib/python3.12/site-packages /opt/fde/fde-server/.venv/bin/python -m pytest -q tests/platform/test_platform_identity.py tests/recommend/test_recommend_flow.py`。完整检查使用相同模块搜索路径、已有Server工具 `RUN`；第二次用独占 `/dev/shm/fde-agent-directory-final-20261013` 作为 pytest basetemp（停止后精确清理）；未静默跳过Docker。

证据目录：`/root/.codex/agent-state/fde/quickstart-initialization/implementer_agent_directory/`，包括 `asset-handler-red.log`、`asset-malformed-red.log`、`asset-permission-red.log`、`asset-target-final.log`、`asset-make-check.log`、`asset-make-check-final.log`。

经验：身份依赖错误不仅发生于登录依赖，也会发生于路由读取目录，须有安全的统一错误边界。推荐匹配上下文可覆盖不等于目标归属可覆盖；写授权须保留目录权威来源，空归属不构成管理权限。

## Docker 测试清理竞态后继

Root单独复现前述 `test_real_container_runs_and_serves`：业务断言通过，但 teardown 在验证owner后执行 `docker rm -f <当前ID>`，与容器 `--rm` 异步删除相撞，返回 `removal of container ... is already in progress`。根因证据：`/opt/fde/.run/quickstart-intranet-acceptance-20261013/asset-container-repro.log`；这替代前述“原错误尚未定位”的当时结论，保留失败历史。

Root额外仅授权 `tests/docker_resources.py` 与 `tests/test_docker_resources.py`。仅对精确当前容器ID的“已不存在/移除中”错误执行最多10次inspect确认gone，相邻检查0.1秒；原命令timeout不变。每次仍复核owner，权限错误、其他/前缀相似ID、owner变化或一直存在均报错。不会删其他容器、镜像或卷，没有修改业务代码。

TDD：新旧unit首轮 `6 failed, 7 passed, 1 warning`；修复后 `13 passed, 1 warning`。真实Docker单用例 `1 passed, 1 warning`（17.14秒，无teardown ERROR），目标Ruff/format与diff检查通过。全仓 `make check` 仍交Root串行，不由本次单用例通过推导全量完成。证据日志为同Agent状态目录下 `docker-resource-red.log`、`docker-resource-green.log`、`docker-real-green.log`。

可复用经验：Docker `--rm` 的移除是异步过程，cleanup须确认精确owner资源最终缺失；不能仅吞掉“already in progress”，也不能把任意“No such”子串当作该ID已经消失。
