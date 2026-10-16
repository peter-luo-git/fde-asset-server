# fde-asset-server

文档版本：0.7（2026-10-16）。资产包版本核对及任务资产权限协议见下文；候选验证及现场资产服务切换记录保留原适用范围。

FDE 协同平台的**资产中心服务**：索引、目录、沉淀、评审、分发与复用计量。

## 平台Agent推荐目录后继（2026-10-13）

platform身份模式通过 `X-FDE-Service-Key`读取Server内部目录，Agent与用户/项目共用默认30秒TTL；只消费 `key/title/description/role/owner/department_code` 六安全字段，不使用本地演示Agent回退或运行配置。合法空agents数组保持空；目录过期后重新请求，密钥/网络/响应或旧Server缺agents失败时安全503，不继续使用过期目录、不回显内部URL/异常正文，原未登录/失效会话401/403保持。

公开coding预设由Server按配置公司及当前可用状态筛选，作者须同公司活跃，部门来自活跃主部门；无主内建Agent仅管理员管理。普通负责人或有明确同部门的主管沿原规则管理，空owner/空部门不构成主管权限；send/link写授权只信目录归属，compute/explain既有只读上下文覆盖不授予写权限。升级应先使Server提供新目录再更新Asset，回退使用兼容的配对版本，不能依靠演示目录维持授权；无新迁移或资产数据重写，也不宣称完整目录多租户已解决。

Root串行完整make check退出0，445通过/2条件跳过/3 warning，真实模型rerank和外部PostgreSQL存储搬迁条件未执行，迁移head为0008_app_health_source；配对Server的v3隔离本机严格API已141项全部通过，最终制品/canonical来源仍待Root收口，详见[文审记录](operations/agent-contributions/quickstart-initialization/reviewer_refresh_docs.md)。真实Docker测试清理只在精确owner容器出现“已不存在/移除中”时有限复查确认缺失，权限错误、错误ID、owner变化或仍存在保持失败；镜像、卷及其他资源保护不变。

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

当前请求还可携带 `agent_key` 和 `known_revision`：未指定 `revision` 的最新包请求仍先核对身份和项目成员，再核对选中资产、挂载名与仓库提交组成的版本键；匹配时返回 `{revision, unchanged: true}`，省略 `files`，否则返回完整资产包。版本键缓存仅在内存，重启或未命中时重新组包；显式 `{revision}` 历史复读仍使用原服务密钥路径，不受此优化影响。

Root已构建`18023df`候选镜像，在现有数据副本验证迁移20个旧资产ID全部保留且仍有效；真实Server Reader读取10文件、2份规范及revision复读通过，快照含2份规范且可prepare。两份seed资产的owner为`department:data-intel`，平台当前缺少该部门，被安全校验拒绝；兼容不重写owner、不放宽门禁，不表示全部资产可用。独立定向检查14通过/1 PostgreSQL跳过，详情见[兼容QA](operations/agent-contributions/asset-latest-compat/qa_agent_environment.md)。此为候选/副本验证，不是现场部署、完整迁移验收或真实模型启动验收。

升级前保护完整数据与配置备份。既有旧布局迁移只搬当前主分支内容，旧仓归档于`repos-legacy/`，Git历史不并入新库，在评审草稿退回草稿需重新提交；旧ID保持不等于历史或审批状态无损。回退应使用匹配旧镜像与升级前完整数据/配置，不能只降镜像沿用已转换目录；副本验证不替代备份恢复演练。本轮现场切换与恢复验收由Root另行登记。

现场后继：Root已备份原数据卷及镜像配置，将资产服务切换至`fde-asset-server:18023df`，沿用现有SQLite/本地仓配置，不主动改为PostgreSQL/Gitea。现场健康检查200，真实Server Reader读取10文件/2份规则成功，原缺失路由404已消除。此结论证明资产读取恢复，不替代完整模型轮或备份恢复演练；详细边界见[Root记录](operations/agent-contributions/asset-latest-compat/root.md)。

### 任务资产关联与 SOP 调用权限

任务讨论与执行日志由 Server 的任务/会话记录保存；资产中心保存可复用 SOP 及任务与资产的关联，不把讨论原消息自动转成资源资产。绑定 SOP 时，请求体为 `{asset_id, role: "sop_step", sop_step}`；步骤必须是流程里实际存在的整数。`engagement_slug` 可省略，提供时必须与任务真实所属项目一致，不能据此获得任务权限。

platform 模式下，每次任务资产的列表、绑定、更换、删除和步骤摘要请求都会使用服务密钥访问 Server 的 `GET /internal/identity/work-items/{work_item_id}/access?username=...&write=...`，核验当前调用者的任务读写权限并取得真实 `engagement_slug`。任务授权结果不缓存：读者不能执行绑定、删除或 `lock=true`；任务不可读或平台隐藏的写拒绝返回404，本地 viewer 或上游显式禁止返回403，平台连接或响应异常返回503。资产可见性仍单独核对；列表不返回不可见资产，步骤摘要不泄露其内容。旧关联的空项目字段只在任务已通过可信核验后兼容，更新时补全；非空但与真实归属冲突的关联拒绝访问。

Agent 内部 `GET /internal/v1/work-items/{work_item_id}/sop-step` 保留 `X-FDE-Service-Key`，同时必须提供 `username` 与 `engagement_slug` 查询参数；服务重新核验调用者的任务权限、真实项目与资产可见性。没绑定、流程已下架或不可见、步骤不存在时返回 `{found: false, work_item_id}`，不含流程标题或提示词；项目不匹配或任务无权访问返回404，缺少调用者参数返回422。

dev 模式只接受 `directory.json` 中 `work_items` 显式登记的任务，形式为 `{"任务ID": {"engagement_slug": "项目标识"}}`，并逐次检查本地用户成员关系；任意未登记任务不能绑定资产，管理员也不能绕过任务存在性。演示种子登记 `w-9`，自动化测试使用各自隔离的任务映射。

配对升级时，先使 Server 提供工作项权限桥，再更新 Asset 与传入调用者参数的 Server SOP Reader；这些变更需作为兼容版本一同验收。旧 Server 不提供权限桥时，任务资产操作会拒绝访问；旧 Reader 不传参数时会收到422。此变更不新增数据库迁移，不共享数据库，也不改变资产包版本缓存或 Skill 激活策略。

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-16 | 0.7 | 补充任务资产真实归属与逐次读写授权、SOP调用者上下文、dev任务映射及配对升级顺序；讨论记录仍归Server管理。 |
| 2026-10-16 | 0.6 | 补充最新资产包的 agent_key、known_revision/unchanged 响应与内存缓存边界；保留身份/项目成员逐次核对和历史版本复读路径。 |
| 2026-10-12 | 0.5 | 登记真实uv.lock与后继锁/隔离构建门禁；纠正首轮共享PATH检测的时点说明，直接pin和共享环境保持。 |
| 2026-10-12 | 0.4 | 独立文审补Makefile的条件锁检查与真实容器构建/跳过前提；保留原验证、资源和部署边界。 |
| 2026-10-12 | 0.3 | 登记完整检查入口与真实Docker测试的独立资源、localhost动态端口及镜像/volume保留边界；不改变产品契约或部署行为。 |
| 2026-10-12 | 0.2 | Root追加现场版本切换、健康与真实Reader验证，保留副本/模型/恢复边界。 |
| 2026-10-11 | 0.1 | 补最新资产版本与平台内部bundle对齐、候选副本验证及备份回退限制；保留缺失owner安全拒绝，未宣称现场或模型验收。 |
