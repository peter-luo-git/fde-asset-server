# 最新基线全测修复 — Asset 实现贡献

文档版本：0.2（2026-10-12）。Agent：`implementer_specs_asset_fixes`；模型：`gpt-6.1-sol`；结论：**通过**。Root负责独立文审、验收和Git写操作。

## 实际修改

- 工作树：`/opt/fde/.run/worktrees/full-test-20261012-asset`；基线：`d511a7ab1e239b708df874eb2f236f817a511195`；分支：`codex/full-test-fixes-20261012`。
- 使用 `systematic-debugging` 重审旧全测和最新Makefile，确认两条真实Docker用例使用固定镜像名、无归属隔离并在结束时 `rmi -f`，无法满足本轮保护既有资源约束。按Root授权修改测试资源隔离，未改业务源码或跨仓契约。
- 新增 `tests/docker_resources.py` 和4条资源边界回归，新增fixture，两条真实用例使用每例UUID镜像/项目/容器、独占bridge、动态localhost发布、每容器128m/0.25CPU和归属标签；创建前拒绝已有名称，清理逐份复核归属，保留镜像及volume。
- 清理只精准忽略 `No such container: <已列举ID>` 竞态。单容器沿用真实产品启动/停止，compose启动/状态/日志沿用真实实现，测试专用down按owned标签清理容器以避免产品 `down -v` 操作；未改产品默认值或其部署down行为。
- README升至0.3补检查入口与资源保留边界；LOG追加本轮事实。未派生、暂存、提交、推送、发布、访问原数据库或真实模型。

## 红绿与环境证据

证据目录：`/opt/fde/.run/full-test-fixes-20261012/specs-asset/`。

- 资源边界红：`docker-boundary-red.log`，3条因隔离helper尚不存在而失败；实现后3通过。真实定向第一轮2失败/3通过/1清理错误：internal网络下localhost发布无法访问，stop `--rm` 后列举/inspect出现精准消失竞态，见 `docker-targeted.log/xml`。
- 竞态回归红：`docker-cleanup-red.log`，精确missing-container失败；改为独占标准bridge、补精准消失处理后，最终资源边界4条+真实Docker2条合计 **6 passed，23.19s**，见 `docker-targeted-final.log/xml`。最终owned容器/网络为空，镜像按约束保留。
- 只读共享venv缺pytest/ruff；系统ensurepip不可用。仅私有artifact venv安装 `ruff==0.9.2`、`pytest==8.3.4`、`pytest-asyncio==0.25.2`，读共享Assetsite-packages，PYTHONPATH固定当前worktree/src，不改共享依赖或仓库锁。

## 完整原生检查

`make check` 未排除任何pytest项，RUN仅指向私有测试工具与只读依赖，环境禁dotenv/真实模型配置，默认各用例临时SQLite；完整命令元数据见 `asset-make-check-command.json`，输出 `asset-make-check.log`，JUnit `asset-make-check.xml`，summary `asset-make-check-summary.json`。

- Ruff、format（137文件）、compileall通过。
- pytest **423 passed / 2 skipped / 3 warnings**，588.92s；两条跳过为真实模型网关与PostgreSQL环境。
- Alembic只有 `0008_app_health_source (head)`；整条make **exit0，594.34s**。
- 前轮共享PATH无uv，因此Makefile条件分支中的 `uv lock --check` 当时未执行；Root后继独立补验确认缺锁，已按下节生成真实锁并补齐门禁。警告为现有BlockingPortal与tarfile弃用、pytest-asyncio默认loop scope提示，未删除门禁或放宽断言。

## PostgreSQL条件补验

Root与Server child协调独占PG窗口后，仅验证 `tests/platform/test_storage.py::test_sqlite_data_is_copied_into_an_empty_postgres`，未重复全量。

- 首轮fixture setup缺共享依赖 `psycopg`，未进入迁移；ownedPG已清理，保留 `asset-owned-pg-*-setup-failure.*`。
- Root授权仅私有venv安装仓库已声明的 `psycopg[binary]==3.2.3`，包元数据/import预检通过；源码和锁文件不变。
- 最终 **1 passed，4.85s**。使用现存 `postgres:17.11-alpine` 精确镜像ID，256m、独立测试账号/临时库、192m tmpfs数据、动态 `127.0.0.1:32822`；inspect核验无volume挂载。容器/网络精确清理成功、0错误，镜像和volume未删除，窗口已释放给Server。
- `asset-owned-pg.log/xml`、`asset-owned-pg-summary.json` 保存真实结果与资源参数，不记录临时密码。只证明这项迁移条件，未声称全套pytest在PostgreSQL上重跑。

## Root独立补验后的uv锁修复

Root使用私有uv执行 `uv lock --check --offline`，发现缺少 `uv.lock`。首轮红证据 `asset-uv-lock-check.log` 完整保留；工具不在共享PATH不能作为缺失该工程门禁的依据。

- 仅新增真实 `uv.lock`，由私有uv0.13.0从 `https://pypi.org/simple` 解析生成，650行、45packages、锁格式version1/revision5。原pyproject字节、项目0.1.0版本、16runtime及3dev精确直接依赖全部保持；TOML与逐项pin验证通过，未手工伪造或放宽依赖。
- `UV_CACHE_DIR=/opt/fde/.run/full-test-fixes-20261012/specs-asset/uv-cache`、`UV_PYTHON_DOWNLOADS=never`，使用既有 `/usr/bin/python3` 3.12.3；不执行uv sync、不改共享venv。uv规范化Python范围为 `==3.12.*`，对应原 `>=3.12,<3.13`。附加检查最初错误地比较这两种表达的字符串，已改为范围语义核验，锁文件保持真实生成字节。
- `uv lock --check --python /usr/bin/python3` 和带 `--offline` 的检查分别exit0；日志 `asset-uv-lock-check-online.log`、`asset-uv-lock-check-offline.log`，源/19pins/包数量/Python范围/锁sha256见 `asset-uv-lock-summary.json`。
- 隔离 `uv build --python /usr/bin/python3 --out-dir <artifact>/asset-build --no-sources` exit0；wheel版本和16个运行依赖与原清单一致，sdist中的uv.lock/pyproject与工作树逐字相同，见 `asset-uv-build.log`、`asset-uv-build-summary.json`。产物只在artifact内。
- 私有uv进入PATH后的 `make -n check` 确认包含条件 `uv lock --check`，见 `asset-uv-make-dry-run.log`。本轮只增加锁及记录，按Root指令不重复423项已通过pytest，分别登记先前全量与补齐锁/构建门禁。受影响文档交独立 `docs-impact-control` reviewer重新核验，README/他人贡献不由本轮实现者修改。

## 限制与可复用经验

没有真实模型、真实Gitea、原服务联调或全套PG验收；HTTP契约仍由仓库原有受控测试覆盖。本次不改变产品演示网络出网限制（ENV_BLOCKERS E5）或 `LocalComposeRunner.down` 的生产行为。

可复用处理：真实容器测试应在启动前拒绝已有资源名，用独占UUID名称与归属标签，动态localhost发布；只清理自己核验的容器/网络，保留镜像/volume。`--rm` 资源在列举后可自动消失，只对包含精确已列举容器ID的No such错误作幂等处理，其余错误仍抛出。internal bridge在当前Docker环境不能支持该localhost发布主链；独占标准bridge保持真实HTTP验收及既有无凭据边界。缺依赖时先保留错误、核对精确已声明版本，再只补私有测试环境，不改共享venv。

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-12 | 0.2 | 根据Root无锁红证据生成真实uv.lock，补在线/离线锁检查与隔离构建/元数据一致性验证；保留先前全量及未执行条件门禁的历史事实。 |
| 2026-10-12 | 0.1 | 登记测试资源隔离、红绿证据、完整make check、单项PG补验与资源清理限制。 |
