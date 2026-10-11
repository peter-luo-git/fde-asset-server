# 最新资产服务兼容独立QA

- Agent: qa_agent_environment；模型 gpt-6.1-sol；Status: completed；结论：**通过（本轮最小功能测试范围）**。
- 工作树 `/opt/fde/.run/worktrees/asset-latest-compat`，baseline `18023dfaad161bddf7e67095fd8277c111e9babe`，branch `codex/asset-latest-compat`。适用最新共享与本仓AGENTS，使用verification-before-completion；仅新增独立测试、LOG追加、本贡献及状态/临时证据，不改业务或依赖环境、不Git写/部署/模型/派生。

## 验证及独立新增测试

- `tests/platform/test_agent_bundle_compat.py` 新增3项必要缺失回归。真实TestClient `/internal/v1/agent-bundle`验证revision为40小写hex、files严格base64、文件与总字节限额；公司两份规范全文/排序原样，旧Skill资产ID与catalog对应、内容sha256/size一致。
- 仅fixture临时资产库变更规范并重索引后，新revision必须改变；以原revision复读整payload必须完全一致。测试操作落在pytest tmp目录，未写现场Git/数据。
- 服务密钥缺失/错误404，非成员项目403，未知用户404，缺项目422；公司+当前项目资产支持，非支持Implementation不混入Agent包，内部路由不入公开OpenAPI。
- legacy_layout真实转换保持旧规则/Skill ID和全文字节，company visibility保持；附件真实FileStore以hash存储、原字节/size完整且不重留在资产Git目录。既有catalog6项覆盖成员/非成员、当前项目缩窄与main-file hash；storage4项覆盖去重/旧blob搬迁/附件权限/空字符。

## 实际命令与结果

原 `/opt/fde/fde-asset-server/.venv` 没有pytest，首次 `python -m pytest` exit1（No module named pytest）。没有更新依赖：asset Python维持自身site-packages优先，测试进程末尾追加 `/opt/fde/fde-server/.venv/lib/python3.12/site-packages` 只补pytest；关闭插件自动加载、强制SQLite、禁止dotenv，避免接现场数据库或模型。

```sh
FDE_ASSET_SKIP_ENV_FILE=1 FDE_ASSET_TEST_DATABASE_URL='' PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src /opt/fde/fde-asset-server/.venv/bin/python -c 'import sys; sys.path.append("/opt/fde/fde-server/.venv/lib/python3.12/site-packages"); import pytest; raise SystemExit(pytest.main(["tests/platform/test_agent_catalog.py","tests/platform/test_storage.py","-q","--tb=short"]))'
```

**10 passed / 1 skipped，37.78s，exit0**。PostgreSQL迁移未配置数据库而跳过，不伪称已验证。

```sh
FDE_ASSET_SKIP_ENV_FILE=1 FDE_ASSET_TEST_DATABASE_URL='' PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src /opt/fde/fde-asset-server/.venv/bin/python -c 'import sys; sys.path.append("/opt/fde/fde-server/.venv/lib/python3.12/site-packages"); import pytest; raise SystemExit(pytest.main(["tests/platform/test_agent_bundle_compat.py","tests/asset/test_sharing.py::test_agent_bundle_is_laid_out_for_the_platform","-q","--tb=short"]))'
```

**4 passed，15.23s，exit0**。两组各2warning：BlockingPortal别名弃用、禁插件后asyncio_mode未知；本轮全同步用例不依赖该插件。

日志证据 `/opt/fde/.run/asset-latest-compat/qa/targeted-results.txt`；首次编辑后仓根/分支/status与新增测试落盘已核，diffcheck通过。

## 范围与限制

- 只读对比Server `AssetBundleReader` 的revision40hex/files字符串/严格base64/4096files/64MiB契约，最新真实HTTP输出符合。未跨仓import业务代码或共享数据库。
- Root另行在最新候选镜像+现场副本以真实Server Reader读10文件/2规范、revision复读及迁移20旧ID保持，属于Root独立实证，我没有将它算成自己的测试。
- 没有全量、真实模型、在线服务或PostgreSQL迁移验证；旧迁移历史Git提交保留不在本次功能合同内，上游迁移只搬当前主分支内容。fixture字节/标识保持通过不能单独代替生产备份与恢复验证。

## 格式收尾复验

- Root要求补仓ruff format检查。首次 `/opt/fde/fde-server/.venv/bin/ruff format --check tests/platform/test_agent_bundle_compat.py` exit1，`1 file would be reformatted`。仅对该文件执行ruff format，输出 `1 file reformatted`，未改任何业务/他人文件。
- 再次format --check：`1 file already formatted`、exit0；ruff check：`All checks passed!`、exit0；git diff --check exit0。
- 复用上述隔离测试命令，仅pytest参数换为 `["tests/platform/test_agent_bundle_compat.py","-q","--tb=short"]`，**3 passed，2既有warnings，18.29s，exit0**。未复跑其它组/全量，无模型或现场变化。
- `README.md` 当前Root修改保留未触碰；本轮格式化与贡献追加已回读，状态completed。
