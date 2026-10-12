# fde-asset-server

文档版本：0.5（2026-10-12）。候选验证及现场资产服务切换分别见下文；前序能力与演示范围保持。

FDE 协同平台的**资产中心服务**：索引、目录、沉淀、评审、分发与复用计量。

## 能力范围

- 7 种资产类型：`Rule` 规范 · `Sop` 标准作业程序 · `Skill` 技能 · `Solution` 方案 · `Implementation` 实施 · `Case` 问题 · `Experience` 经验
- 作用域：公司 / 部门 / 项目（客户级 v0.2）
- 附件：PDF / Word / Excel / PPT 存原件 + 提取文本用于检索
- 分发：规范强制注入、技能工具发现、SOP 步骤注入、知识分层按需（L0/L1/L2）

设计与计划见 `specs/docs/architecture/资产中心实现方案与开发计划.md`。

## 本地运行（无需 Docker / Gitea / 模型凭据）

```bash
uv sync --dev
cp .env.example .env        # 按需修改路径
make dev                    # 初始化本地裸仓库与种子内容，并启动 asset-api
make worker                 # 另一个终端：索引轮询与快照生成
make demo                   # 端到端演示：12 步全绿即 v0.1 验收通过
```

数据默认落在：

```
~/.fde/repos/company-assets.git        资产裸仓库（唯一可信源）
~/.fde/repos/dept-<代号>-assets.git    部门级
~/.fde/data/asset.db                   索引与使用记录（SQLite）
~/.fde/data/assets/snapshots/<sha>/    会话快照（供 orchestrator 只读挂载）
```

## 本地验证

`make check` 顺序执行 Ruff、格式检查、编译、完整 pytest 与 Alembic heads；检测到 `uv` 时最后还执行 `uv lock --check`。默认测试使用各用例的临时 SQLite 数据。真实模型与 PostgreSQL 条件测试需要另行提供相应环境，默认条件缺失时跳过。

仓库保留真实生成的 [uv.lock](uv.lock)，固定 Python 3.12 范围内的依赖解析；原 `pyproject.toml` 的16个运行依赖和3个开发依赖直接 pin、项目版本保持。2026-10-12 首轮完整检查使用既有只读依赖环境，423项通过/2项条件跳过，当时共享 PATH 未找到 uv，未执行条件锁检查。Root 后继补验发现缺锁后，使用私有 uv0.13.0、PyPI 与私有缓存生成锁；在线/离线 `uv lock --check` 和独立产物目录中的 `uv build --no-sources` 均通过，wheel依赖及sdist内的锁/清单与源码一致。本轮未执行 `uv sync` 或修改共享虚拟环境；先前全量结果不表示已在新锁同步出的环境重跑全部测试，详细证据见[实现贡献](operations/agent-contributions/full-test-fixes-20261012/implementer_specs_asset_fixes.md)。

真实容器用例要求可用 Docker，多容器用例还要求 `docker compose`；构建使用 `busybox` 基础镜像，并显式传入 `--network none --pull=false`，构建失败时该用例按现有规则跳过。完整结果须同时查看通过与跳过项，不能将未执行的 Docker、模型或 PostgreSQL 条件当作已通过。

两条真实 Docker 用例使用 `fde-test-<uuid>:latest` 唯一镜像标签、独立项目/容器/bridge 网络和动态 `127.0.0.1` 端口，单容器内存/CPU限制为 `128m`/`0.25`；资源名称已存在即拒绝覆盖。结束时按本轮归属标签复核并清理容器与网络，保留镜像和所有volume，方便审计和避免删除既有资源。该测试网络的出网边界仍遵循 [环境阻塞 E5](tasks/ENV_BLOCKERS.md)，测试不加载真实模型凭据；产品部署参数与运行器行为保持原有约定。

## 与其他仓库的关系

| 仓库 | 关系 |
|---|---|
| `fde-specs` | 规格与契约来源，以 submodule 挂在 `specs/` |
| `fde-server` | 消费方：取快照与 SOP 步骤摘要、上报使用事件；提供成员关系查询 |
| `fde-web` | 前端，资产页面在 `src/features/asset/` |

### 平台 Agent 资产包与版本对齐

Server `7548733`的`AssetBundleReader`消费资产服务内部`POST /internal/v1/agent-bundle`；本轮以最新版本`18023df`替代缺少该路由的旧`4fda0ae`候选，避免将路由404当作模型故障。服务间使用`X-FDE-Service-Key`，缺失/错误返回404；内部接口不进入公开OpenAPI。首次请求`{username,engagement_slug}`按用户/项目范围组包，非成员且非管理员返回403；`{revision}`复读已存包，依赖服务密钥，非法或不存在版本返回404。响应`revision`为40位小写hex，`files`为路径到base64字符串的映射，另含assets/skipped；revision标识组包内容，不等同资产Git提交。Server严格解码、核对版本及4096文件/64MiB上限，再经`CompanyAssetSnapshots.prepare`校验为只读快照，不共享数据库或跨仓import。

Root已构建`18023df`候选镜像，在现有数据副本验证迁移20个旧资产ID全部保留且仍有效；真实Server Reader读取10文件、2份规范及revision复读通过，快照含2份规范且可prepare。两份seed资产的owner为`department:data-intel`，平台当前缺少该部门，被安全校验拒绝；兼容不重写owner、不放宽门禁，不表示全部资产可用。独立定向检查14通过/1 PostgreSQL跳过，详情见[兼容QA](operations/agent-contributions/asset-latest-compat/qa_agent_environment.md)。此为候选/副本验证，不是现场部署、完整迁移验收或真实模型启动验收。

升级前保护完整数据与配置备份。既有旧布局迁移只搬当前主分支内容，旧仓归档于`repos-legacy/`，Git历史不并入新库，在评审草稿退回草稿需重新提交；旧ID保持不等于历史或审批状态无损。回退应使用匹配旧镜像与升级前完整数据/配置，不能只降镜像沿用已转换目录；副本验证不替代备份恢复演练。本轮现场切换与恢复验收由Root另行登记。

现场后继：Root已备份原数据卷及镜像配置，将资产服务切换至`fde-asset-server:18023df`，沿用现有SQLite/本地仓配置，不主动改为PostgreSQL/Gitea。现场健康检查200，真实Server Reader读取10文件/2份规则成功，原缺失路由404已消除。此结论证明资产读取恢复，不替代完整模型轮或备份恢复演练；详细边界见[Root记录](operations/agent-contributions/asset-latest-compat/root.md)。

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-12 | 0.5 | 登记真实uv.lock与后继锁/隔离构建门禁；纠正首轮共享PATH检测的时点说明，直接pin和共享环境保持。 |
| 2026-10-12 | 0.4 | 独立文审补Makefile的条件锁检查与真实容器构建/跳过前提；保留原验证、资源和部署边界。 |
| 2026-10-12 | 0.3 | 登记完整检查入口与真实Docker测试的独立资源、localhost动态端口及镜像/volume保留边界；不改变产品契约或部署行为。 |
| 2026-10-12 | 0.2 | Root追加现场版本切换、健康与真实Reader验证，保留副本/模型/恢复边界。 |
| 2026-10-11 | 0.1 | 补最新资产版本与平台内部bundle对齐、候选副本验证及备份回退限制；保留缺失owner安全拒绝，未宣称现场或模型验收。 |
