# fde-asset-server

文档版本：1.0（2026-10-27）。本轮同步 `peter-luo-git/fde-asset-server` 的 `dev1005`（`e4d2bdb`）源码与契约；下文历史测试、现场切换和 `tasks/ENV_BLOCKERS.md` 的上游环境记录保留原时点，不代表当前服务器已升级为 PostgreSQL、启用回调或完成模型验收。

FDE 协同平台的**资产中心服务**：索引、目录、沉淀、评审、分发与复用计量。

## 平台Agent推荐目录后继（2026-10-13）

2026-10-19 项目实例关联后继：配套 Server 目录纳入有效项目实例及当前负责人，`GET /api/v1/recommend/targets` 在 platform 模式实时刷新目录，新实例无需等待TTL才可选。`pa-*` 目标的写入与接受推荐重新读取当前目录，转交后旧负责人/旧收件人不保留授权；缺失目标返回404，管理员也不能写入已失效实例；刷新失败不以旧缓存授予写权限。既有非项目实例推荐接受兼容语义保持。

同日可见性补强：直接关联与接受推荐在写入事务内沿用 `visibility_clause` 复核当前资产，检查先于幂等返回。不存在、已删除或对当前主体不可见（含不满足restricted规则）统一404，不泄露存在性；管理员也不能绕过restricted规则。拒绝不新增关联或推进推荐状态，既有关联不因此删除。解除仍只校验当前目标管理权，允许清理自身可管理目标的旧关联，不要求资产仍可见；不修改全局资产可见性规则或资产正文。

新增 `POST /api/v1/recommend/unlink`，输入严格为 `{target_type,target_id,asset_id}`，类型为 `engagement` 或 `agent`，仅目标当前负责人或管理员可解除（无权403）。按三元组删除关联，首次返回 `{unlinked:true}` 并记 `asset.unlinked`，重复返回 `{unlinked:false}` 且不重复记事件；保留资产正文、其他目标关联；非 SOP 资产可重新关联。解除后清推荐缓存，无新迁移。请求字段和路径由真实路由经 `scripts/export_openapi.py` 生成 `contracts/asset-v1.yaml`，child文审不修改生成物。本轮只运行合并相关检查，不运行全量测试、全量 E2E 或部署；旧测试/部署记录不作本轮通过证据。

platform身份模式通过 `X-FDE-Service-Key`读取Server内部目录，Agent与用户/项目共用默认30秒TTL；只消费 `key/title/description/role/owner/department_code` 六安全字段，不使用本地演示Agent回退或运行配置。合法空agents数组保持空；目录过期后重新请求，密钥/网络/响应或旧Server缺agents失败时安全503，不继续使用过期目录、不回显内部URL/异常正文，原未登录/失效会话401/403保持。

公开coding预设由Server按配置公司及当前可用状态筛选，作者须同公司活跃，部门来自活跃主部门；无主内建Agent仅管理员管理。普通负责人或有明确同部门的主管沿原规则管理，空owner/空部门不构成主管权限；send/link写授权只信目录归属，compute/explain既有只读上下文覆盖不授予写权限。升级应先使Server提供新目录再更新Asset，回退使用兼容的配对版本，不能依靠演示目录维持授权；无新迁移或资产数据重写，也不宣称完整目录多租户已解决。

Root串行完整make check退出0，445通过/2条件跳过/3 warning，真实模型rerank和外部PostgreSQL存储搬迁条件未执行，迁移head为0008_app_health_source；配对Server的v3隔离本机严格API已141项全部通过，最终制品/canonical来源仍待Root收口，详见[文审记录](operations/agent-contributions/quickstart-initialization/reviewer_refresh_docs.md)。真实Docker测试清理只在精确owner容器出现“已不存在/移除中”时有限复查确认缺失，权限错误、错误ID、owner变化或仍存在保持失败；镜像、卷及其他资源保护不变。

## 能力范围

- 7 种资产类型：`Rule` 规范 · `Sop` 标准作业程序 · `Skill` 技能 · `Solution` 方案 · `Implementation` 实施 · `Case` 问题 · `Experience` 经验
- 作用域：公司 / 部门 / 项目（客户级 v0.2）
- 附件：PDF / Word / Excel / PPT 存原件 + 提取文本用于检索
- 分发：非 SOP 资产按必选、目标关联与项目自身资产挑选；规范全文注入、技能工具发现、知识分层按需（L0/L1/L2）。SOP 只用于任务执行，由平台注入当前步骤，不作为参考资产入包。

设计与计划见 `specs/docs/architecture/资产中心实现方案与开发计划.md`。

## 本地运行（需要 PostgreSQL；无需 Gitea / 模型凭据）

先准备独立的开发数据库。已有 PostgreSQL 时直接配置；`make test-db` 可用 Docker 启动本机测试服务器，地址见 `.env.example`。配置文件需导出到启动进程环境，单独复制 `.env` 不会自动加载。

```bash
uv sync --dev
cp .env.example .env        # 按需修改路径
set -a
. ./.env
set +a
make dev                    # 初始化本地裸仓库与目录，并启动 asset-api
make worker                 # 另一个终端：索引轮询与快照生成
make demo                   # 12 步演示；另配独立空库的 FDE_ASSET_DATABASE_URL
```

另一个终端也须加载同一套环境。`FDE_ASSET_DATABASE_URL` 必填，只接受 PostgreSQL；缺失时启动失败，不回退 SQLite。旧库升级涉及 `0009_asset_mandatory`、`0010_visibility_requests`、`0011_checkup_findings`、`0012_sop_execution`、`0013_skill_packages`、`0014_skill_deps_status`，当前源码的迁移头为 `0014_skill_deps_status`；检查迁移头不等于目标数据库已完成升级。

数据默认落在：

```
~/.fde/repos/company-assets.git        资产裸仓库（唯一可信源）
~/.fde/repos/dept-<代号>-assets.git    部门级
PostgreSQL（FDE_ASSET_DATABASE_URL）     索引与使用记录（2026-10-18 起只用 PostgreSQL）
~/.fde/data/assets/snapshots/<sha>/    会话快照（供 orchestrator 只读挂载）
```

Git 子进程回收（2026-10-23 源码修复）：本地仓库与 Gitea 缓存的 Git 调用统一使用命令级 `maintenance.autoDetach=false` 和 `gc.autoDetach=false`，保留自动维护并等待其前台完成，不改仓库或用户的持久配置。这样维护进程不会脱离 `subprocess.run` 后成为 Python PID 1 下未回收的子进程。机制依据为 [Git 2.47 的自动维护配置](https://git-scm.com/docs/git-maintenance/2.47.0.html)；2026-10-23 在 Git 2.47.3 上完成默认后台维护与前台维护对照。部署端还须配套容器 init 回收其他孤儿子进程；仅更新源码不改变已有容器。

## 本地验证

`make check` 顺序执行 Ruff、格式检查、编译、完整 pytest 与 Alembic heads；检测到 `uv` 时最后还执行 `uv lock --check`。测试只用 PostgreSQL：每个用例在测试库服务器上建临时库、跑完删掉，测试账号须有建库权限；服务器默认是 `make test-db` 起的本机容器（127.0.0.1:55432），别处用 `FDE_ASSET_TEST_DATABASE_URL` 指定。契约测试只使用真实路由声明生成 OpenAPI，跳过数据库上下文初始化；独立契约生成命令仍需提供指向隔离测试库的 `FDE_ASSET_DATABASE_URL`。

默认测试不读取 `.env.local`，并移除继承的模型配置。真实模型专项需本轮授权后显式运行 `pytest --live-model tests/asset/test_rerank.py::test_real_model_reranks_and_explains`，只从明确传入的进程环境取配置；已启用却缺配置、服务失败或退回关键词排序均失败。真实容器专项需 `--docker-integration -m docker`；未启用记为跳过，启用后的构建/运行失败不能转成skip。静态容器权限检查始终执行。

按问题检索 `/api/v1/assets/ask` 使用专用重排配置 `FDE_ASSET_RERANK_BASE_URL`、`FDE_ASSET_RERANK_API_KEY`、`FDE_ASSET_RERANK_MODEL_NAME`，向配置地址的 `/rerank` 发送问题和权限过滤后的文档。它不使用生成式推荐的 `FDE_ASSET_LLM_*` / `FDE_ASSET_REASON_MODEL` 兜底；上面的 live-model 用例验证生成式挑选和解释，不能证明专用重排或 Web semantic 验收通过。真实语义验收须明确配置支持重排协议的模型，不能把聊天模型路由直接当成重排能力。

管理员通过现有 `GET/PUT /api/v1/settings` 的 `rerank_model_name`（资产重排模型）选择打分模型，按问题检索、精确推荐和 MCP `search_assets` 都读取该值；留空沿用 `FDE_ASSET_RERANK_MODEL_NAME`。模型名必须是字符串、最多 255 个字符且不含控制字符，地址和密钥继续只从上述环境变量读取。精确推荐缓存按所选重排模型区分。原 `rerank_model` 设置展示为“推荐理由生成模型”，仍只用于 `/api/v1/recommend/explain` 的生成模型覆盖，留空沿用默认生成模型，与重排选择相互独立。

专用响应接受 `results/index/relevance_score` 或兼容的 `data/document_index/score`，要求索引为有效且不重复的整数、分数为0–1有限数；明确的空列表仍可表示成功的空排名。chat-shaped JSON、缺评分列表或无效条目不会被标为 semantic；调用或解析失败保留关键词兜底，日志仅记录异常类型和HTTP状态，不记录URL、密钥、问题或资产正文。离线协议回归为 `pytest tests/asset/test_rerank_http_contract.py tests/asset/test_rerank.py`，其中未授权的live-model用例仍为skip。

固定无模型冒烟入口为 `python scripts/smoke.py --python /absolute/locked/python --report /absolute/asset-smoke.xml`，必须明确提供 `FDE_ASSET_TEST_DATABASE_URL`，不会猜测共享业务库。硬上限60秒（pytest最多45秒，余量用于强杀与父进程清理），覆盖健康、契约、资产类型、专用重排协议与安全降级日志（合成HTTP，不调用模型）、扫描、项目可见性与资产包授权；缺前提退出2，失败/超时/零用例/跳过不能通过。每次冒烟在0700私有清单中先登记精确随机库名；即使用例被强杀，父进程finally仍按本轮清单清理，拒绝外来条目，不扫描删除共享前缀。旧报告先清除，必须由本次运行重新生成。它不代替完整pytest、真实模型或真实Docker验收。

**SQLite 已去掉（2026-10-18）**：服务、迁移、测试都不再支持 SQLite。只保留启动时的一次性搬迁（`core/sqlite_import.py`）：PostgreSQL 业务表均为空且数据目录里还有旧的 `asset.db` 时搬入数据，成功后将原文件改名为带 `.migrated-to-postgres` 后缀的备份。升级前保护数据库、资产文件与配置，搬迁结果仍需独立核验；本轮源码同步不表示当前服务器已执行搬迁。旧的 `scripts/rename_user.py` 已删除。

仓库保留真实生成的 [uv.lock](uv.lock)，固定 Python 3.12 范围内的依赖解析；原 `pyproject.toml` 的16个运行依赖和3个开发依赖直接 pin、项目版本保持。2026-10-12 首轮完整检查使用既有只读依赖环境，423项通过/2项条件跳过，当时共享 PATH 未找到 uv，未执行条件锁检查。Root 后继补验发现缺锁后，使用私有 uv0.13.0、PyPI 与私有缓存生成锁；在线/离线 `uv lock --check` 和独立产物目录中的 `uv build --no-sources` 均通过，wheel依赖及sdist内的锁/清单与源码一致。本轮未执行 `uv sync` 或修改共享虚拟环境；先前全量结果不表示已在新锁同步出的环境重跑全部测试，详细证据见[实现贡献](operations/agent-contributions/full-test-fixes-20261012/implementer_specs_asset_fixes.md)。

真实容器用例要求可用 Docker，多容器用例还要求 `docker compose`；构建使用 `busybox` 基础镜像，并显式传入 `--network none --pull=false`，构建失败时该用例按现有规则跳过。完整结果须同时查看通过与跳过项；未执行的 Docker、模型用例不能当作已通过，普通 PostgreSQL 测试连接失败也不能当作条件跳过。

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

目标组包带入可见且有效的非 SOP 必选资产、关联给项目或 Agent 的资产和项目自身资产；没有关联时不以整层资产兜底。必选范围为公司、部门、客户、项目、个人，由管理员或对应范围负责人设置；个人范围由本人管理，仅在可信服务端请求 `personal: true` 的私聊组包中生效。私聊还自动带入该用户的可见个人 Skill；当前 SOP 步骤点名的可见 Skill 即使未关联也带入，同名优先项目、再个人技能。SOP 及其上层流程不再入包，不能直接关联项目或 Agent、不能新设必选；旧必选记录保留但 `effective=false`，请改设默认流程，旧关联可通过解除接口清理。演示种子设置公司级规范必选和公司默认流程。

Root已构建`18023df`候选镜像，在现有数据副本验证迁移20个旧资产ID全部保留且仍有效；真实Server Reader读取10文件、2份规范及revision复读通过，快照含2份规范且可prepare。两份seed资产的owner为`department:data-intel`，平台当前缺少该部门，被安全校验拒绝；兼容不重写owner、不放宽门禁，不表示全部资产可用。独立定向检查14通过/1 PostgreSQL跳过，详情见[兼容QA](operations/agent-contributions/asset-latest-compat/qa_agent_environment.md)。此为候选/副本验证，不是现场部署、完整迁移验收或真实模型启动验收。

升级前保护完整数据与配置备份。既有旧布局迁移只搬当前主分支内容，旧仓归档于`repos-legacy/`，Git历史不并入新库，在评审草稿退回草稿需重新提交；旧ID保持不等于历史或审批状态无损。回退应使用匹配旧镜像与升级前完整数据/配置，不能只降镜像沿用已转换目录；副本验证不替代备份恢复演练。本轮现场切换与恢复验收由Root另行登记。

历史现场后继（`18023df` 时点）：Root已备份原数据卷及镜像配置，将资产服务切换至`fde-asset-server:18023df`，沿用现有SQLite/本地仓配置，不主动改为PostgreSQL/Gitea。现场健康检查200，真实Server Reader读取10文件/2份规则成功，原缺失路由404已消除。此结论证明当时资产读取恢复，不替代完整模型轮或备份恢复演练，也不代表本轮 PostgreSQL-only 源码已部署；详细边界见[Root记录](operations/agent-contributions/asset-latest-compat/root.md)。

### 资产修订、范围申请与后台维护

`POST /api/v1/assets/{asset_id}/edit` 为负责人或对应范围评审人起修订稿，评审通过后写回原资产并增加版本；同一资产只保留一份未入库修订稿，应用登记信息仍走应用市场。`GET /api/v1/assets/{asset_id}/body` 按资产可见性读取仓库主文件正文，版本列表由 `GET /api/v1/assets/{asset_id}/versions` 提供。调整可见范围时，无该层评审权的新增项转为申请，批准后才加入；草稿可携带额外 `visibility`，入库时分别应用或转申请。`GET /api/v1/visibility-requests` 的 `view` 仅接受 `todo`、`mine`、`decided`，默认 `todo`；未知值返回422，服务层同样拒绝，不能借视图名绕过待审权限过滤。

`make worker` 新增每日体检与清理任务：体检记录问题并通知负责人，由人决定是否调整资产或收回可见范围；人员目录不可用时不将人员问题记为已解决。资产包存档保留30天内及最新20份，清理其余旧包后按旧版本复读可能返回404；附件只有未被任何资产（含已删除）或草稿引用、且超过7天时才清理。

Gitea 推送同步需配置 `FDE_ASSET_GITEA_WEBHOOK_URL`、Gitea 地址与服务密钥，服务启动时尝试注册 `/internal/v1/gitea-push`，以派生密钥核验 `X-Gitea-Signature`。注册失败或未配置时沿用轮询，`FDE_ASSET_GITEA_SYNC_SECONDS` 默认改为300秒；本轮没有修改运行环境的 Gitea 白名单或回调。对外契约由 `scripts/export_openapi.py` 生成到 `contracts/asset-v1.yaml`，同步至 Specs 的 `contracts/openapi/asset-v1.yaml`，不要手改生成输出。

### 技能依赖状态与人文示范技能

已入库 Skill 声明的依赖通过服务密钥保护的 `GET /internal/v1/skill-deps/wanted` 去重提供给 Server worker；worker 用 `POST /internal/v1/skill-deps/status` 回报 `ready`、`failed`、`installing` 或 `missing`、安装字节数和原因。依赖键沿用 `fde-skill-deps/v1` 与 `cp311-debian12` 算法；状态保存在 `0014_skill_deps_status` 新表，评审检查结果可展示安装状态。个人带依赖技能仍最多10个，新增安装大小合计1 GiB上限，同一依赖键只计算一次；该大小按 worker 已回报记录计算，未知大小不代表安装完成。真实在线依赖安装到 Agent 执行的端到端链路尚需独立验收。

示范库新增节气诗笺、苏东坡视角和成语锦囊。管理员安装仍走现有草稿、检查和入库流程；源文件中的 MIT 许可及诗文/历法来源声明随完整技能目录保留。节气诗笺脚本使用预生成日历和古诗数据，不在运行时安装历法库；离线脚本测试不等同模型实际使用验收。

### 任务资产关联与 SOP 调用权限

任务讨论与执行日志由 Server 的任务/会话记录保存；资产中心保存可复用 SOP、冻结的任务流程版本与检查点进度，不把讨论原消息自动转成资源资产。绑定 SOP 时，请求体为 `{asset_id, role: "sop_step", sop_step}`；步骤必须是流程里实际存在的整数。绑定后冻结步骤与版本，新版通过任务的 `/sop/refresh` 显式刷新。当前检查点未完成时推进返回409，需显式 `force: true` 并留痕；只有设置自动推进且全部机器检查通过才自动进入下一步。SOP 不能以 `reference` 角色关联。`engagement_slug` 可省略，提供时必须与任务真实所属项目一致，不能据此获得任务权限。

通过 `PUT /api/v1/sop-defaults` 为公司、部门、客户或项目设默认流程，最具体范围优先；只为设定后创建、且平台提供创建时间的任务在首次读取或执行时自动绑定。任务可删除 `/sop-step` 明确退出，防止默认流程再次绑定。检查点分命令、交付物、确认和判断：Agent 对机器检查的通过声明需平台 `verified` 和证据；确认与判断由人确认，Agent 声明通过只进入待确认状态。流程推进不替代任务最终验收。

项目看板可读取 `GET /api/v1/engagements/{engagement_slug}/sop-board` 批量获取已绑定任务的冻结步骤摘要、当前检查点通过/待确认/失败计数；接口要求项目成员或管理员，未绑定任务不出现在结果中。人工确认或退回检查点时保留原 Agent 回报的 `turn_id`，便于定位该轮回复。

platform 模式下，每次任务资产的列表、绑定、更换、删除和步骤摘要请求都会使用服务密钥访问 Server 的 `GET /internal/identity/work-items/{work_item_id}/access?username=...&write=...`，核验当前调用者的任务读写权限并取得真实 `engagement_slug`。任务授权结果不缓存：读者不能执行绑定、删除或 `lock=true`；任务不可读或平台隐藏的写拒绝返回404，本地 viewer 或上游显式禁止返回403，平台连接或响应异常返回503。资产可见性仍单独核对；列表不返回不可见资产，步骤摘要不泄露其内容。旧关联的空项目字段只在任务已通过可信核验后兼容，更新时补全；非空但与真实归属冲突的关联拒绝访问。

Agent 内部 `GET /internal/v1/work-items/{work_item_id}/sop-step` 保留 `X-FDE-Service-Key`，同时必须提供 `username` 与 `engagement_slug` 查询参数；服务重新核验调用者的任务权限、真实项目与资产可见性。没绑定、流程已下架或不可见、步骤不存在时返回 `{found: false, work_item_id}`，不含流程标题或提示词；项目不匹配或任务无权访问返回404，缺少调用者参数返回422。

dev 模式只接受 `directory.json` 中 `work_items` 显式登记的任务，形式为 `{"任务ID": {"engagement_slug": "项目标识", "created_at": "ISO-8601创建时间"}}`（未提供创建时间时不自动绑定默认流程），并逐次检查本地用户成员关系；任意未登记任务不能绑定资产，管理员也不能绕过任务存在性。演示种子登记 `w-9`，自动化测试使用各自隔离的任务映射。

配对升级时，先使 Server 提供工作项权限桥，再更新 Asset 与传入调用者参数的 Server SOP Reader；这些变更需作为兼容版本一同验收。旧 Server 不提供权限桥时，任务资产操作会拒绝访问；旧 Reader 不传参数时会收到422。本轮新增流程进度/默认流程及技能包检查/来源表迁移，不共享数据库。技能市场、整包导入和依赖声明沿用远端实现；依赖实际安装与 Runtime 的发现机制需要配套 Server 支持，Asset 检查通过不能单独证明依赖已安装。

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-27 | 1.0 | 同步 dev1005 e4d2bdb：依赖安装状态/个人容量、SOP看板与轮次保留、人文示范技能；核对0014迁移和相关契约。 |
| 2026-10-26 | 0.9 | 同步 dev1005 9a812e3 的任务流程、技能供应与新迁移；保留独立的关联权限复核和解除能力，仅执行相关验证。 |
| 2026-10-19 | 0.8 | 同步 PostgreSQL 必配与测试前置、0009～0011迁移、必选组包、修订与范围视图拒绝、每日保留策略及推送回调；保留历史现场和上游环境边界。 |
| 2026-10-16 | 0.7 | 补充任务资产真实归属与逐次读写授权、SOP调用者上下文、dev任务映射及配对升级顺序；讨论记录仍归Server管理。 |
| 2026-10-16 | 0.6 | 补充最新资产包的 agent_key、known_revision/unchanged 响应与内存缓存边界；保留身份/项目成员逐次核对和历史版本复读路径。 |
| 2026-10-12 | 0.5 | 登记真实uv.lock与后继锁/隔离构建门禁；纠正首轮共享PATH检测的时点说明，直接pin和共享环境保持。 |
| 2026-10-12 | 0.4 | 独立文审补Makefile的条件锁检查与真实容器构建/跳过前提；保留原验证、资源和部署边界。 |
| 2026-10-12 | 0.3 | 登记完整检查入口与真实Docker测试的独立资源、localhost动态端口及镜像/volume保留边界；不改变产品契约或部署行为。 |
| 2026-10-12 | 0.2 | Root追加现场版本切换、健康与真实Reader验证，保留副本/模型/恢复边界。 |
| 2026-10-11 | 0.1 | 补最新资产版本与平台内部bundle对齐、候选副本验证及备份回退限制；保留缺失owner安全拒绝，未宣称现场或模型验收。 |
