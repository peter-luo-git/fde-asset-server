# 切片日志

| 日期 | 切片 | 提交 | check | 评审 | 备注 |
|---|---|---|---|---|---|
| 2026-10-19 | agent-assets-members-20261019 实现验收 | 基线96a3096；待Root提交 | 通过（完整make check：524通过/1模型网关条件跳过/3已有警告；head0011_checkup_findings） | implementer_asset_linking实现；Root已读实际diff | typed POST recommend/unlink幂等解除指定目标三元组、保留资产与其他关联；pa目标读取和写入实时目录、转交/撤权/缺失目标拒绝；link与accept同事务在写入及幂等返回前复用现有visibility_clause，缺失/不可见统一404且admin仍受restricted限制。最终定向51通过、完整1406.46秒退出0；独立tmpfs与本任务PG已清理，旧中断及红灯日志保留；贡献与命令见operations/agent-contributions/agent-assets-members-20261019/implementer_asset_linking.md。无Git写、业务库变更或部署，三服务部署/E2E由Root收口。 |
| 2026-10-19 | agent-assets-members-20261019 可见性补强文审 | 基线96a3096；待Root提交 | 需修改（补强最终make check及部署E2E待收口） | reviewer_label_docs，docs-impact-control | 核对link/accept同事务visibility检查先于幂等返回、缺失/不可见统一404及admin restricted拒绝；unlink允许清理当前可管理目标旧关联；更新README与待验收说明，无新增review文件或业务/Git写，保留前轮失败历史。 |
| 2026-10-19 | agent-assets-members-20261019 文审 | 基线96a3096；待Root提交 | 需修改（最终make check及部署/E2E待Root收口） | reviewer_label_docs，docs-impact-control | 只更新既有README：当前pa目录/转交后授权、POST recommend/unlink三元组幂等/保留资产与其他关联；无新增review文件、业务/Git写或生成契约修改，git diff --check与写后重读；final2日志pytest KeyError保留，最终证据由Root补录。 |
| 2026-09-30 | S-00 | — | — | — | 仓库骨架创建：进程入口、配置、健康检查、Makefile、看板 |
| 2026-09-30 | S-01 ~ S-20 | `132c543` | 绿（133 测试） | 自检 | 资产服务 v0.1 全量：索引、目录、SOP 分层、快照、沉淀、评审、线索、护照；十二步演示全绿 |
| 2026-09-30 | S-W1/W2/W3 | fde-web `444e71d` | 绿（310 测试） | 自检 | 前端 `src/features/asset/` 自包含 feature + 两层端到端（契约 11 步、浏览器 12 步） |
| 2026-09-30 | 交接 | fde-specs `7bcb46b` | — | — | 《资产中心-组织与共享设计》《资产中心-v0.1交接说明》 |
| 2026-10-01 | Q3、X-1、X-2 | `ce0e30b` `bfe6593` `4b44056` `0f29411` | 绿 | 自检 | 质量等级按复用自动评级；草稿附件与评审流程可见；目录按负责人筛选 |
| 2026-10-01 | R-1…R-3、F5+F7 | `5418233` `80da7b7` `6e9c174` | 绿 | 自检 | 匹配引擎、推荐中心后端、反馈闭环与系统配置 |
| 2026-10-01 | A-1…A-4、B-1…B-4 | `3801239` `33979b1` | 绿 | 自检 | 应用类资产 A 档；容器化演示 B 档（待 E5、E6 环境验收） |
| 2026-10-01 | F1a、F4+F12 | `cf549ec` `2724312` | 绿 | 自检 | 结构化粗排 + 模型精排；资产体检与跨项目空白识别 |
| 2026-10-02 | F1a、X-3 | `536cf09` `9c04c72` | 绿 | 自检 | 排序改交 rerank 模型、理由另由生成模型写；推荐结果缓存 |
| 2026-10-02 | B（compose） | `9d10de1` `d594af9` | 绿 | 自检 | 多容器演示 |
| 2026-10-02 | F8a、F6+F13、F9 | `8662b7c` `58a9dc4` `ae32332` | 绿 | 自检 | 关系自动建链；订阅与变更通知；客户级作用域 |
| 2026-10-04 | X-4 | `ce8ac56` | 绿（288 通过 / 3 跳过） | 自检 | 旧库启动时自动补列；起因是加了 `customer_code` 后沿用的演示库报 no such column |
| 2026-10-04 | F1a 验收、F1b（后端） | 同下一行 | 绿（299 通过 / 3 跳过） | 自检 | rerank 真实调通（修了地址写到 `/rerank` 时拼成 `/rerank/rerank` 的 404）；按问题检索与推荐都改成权限过滤后全量语义打分，新增门槛配置 `search_min_relevance` |
| 2026-10-04 | F1b-web、F8b、X-5 | 紧随 `ce8ac56` 的一次提交；fde-web 同日提交 | 绿（后端 304 通过 / 3 跳过；前端契约 E2E 33、浏览器 E2E 41 步，接真实模型 42 步） | 自检 | 目录搜索框接按问题找；关系子图接口与图谱；演示服务此前读不到 `.env.local`，已修 |
| 2026-10-05 | X-6 ~ X-10 | 2026-10-06 的一次提交；fde-web 同日提交 | 绿（后端 319 通过 / 3 跳过；前端全量 418；契约 E2E 35、浏览器 E2E 48 步，接真实模型 36 / 49） | 自检 | 标识自动生成；线索起草后收掉；演示身份切换；更新时间不再随重启漂移；旧名单自动补齐 |
| 2026-10-06 | X-11 ~ X-13、F2（资产侧） | 2026-10-06 的第二次提交；fde-web 同日提交 | 绿（后端 343 通过 / 3 跳过；前端全量 432；契约 E2E 39、浏览器 E2E 51 步，接真实模型 40 / 52） | 自检 | 后台定时任务、契约文件、MCP 检索工具、删草稿、下架废弃、应用引用、目录分页、评审通知 |
| 2026-10-06 | X-14 ~ X-16 | 2026-10-06 的第三次提交；fde-web 同日提交 | 绿（后端 353 通过 / 3 跳过；前端全量 437；契约 E2E 40、浏览器 E2E 51 步，接真实模型 41 / 52） | 自检 | 客户级草稿此前走不通，已修；推荐理由按需生成（实测 15–30 秒，超时放到 90 秒）；工作台粘贴文字；修掉测试导入时泄漏模型配置的问题 |
| 2026-10-06 | X-17 ~ X-23 | 2026-10-06 的第四次提交；fde-web 同日提交 | 绿（后端 377 通过 / 3 跳过；前端全量 461；契约 E2E 44、浏览器 E2E 60 步，接真实模型 45 / 61） | 自检 | 快照跟着关联走；部门视图区分自己与同事负责的；订阅多选与新资产通知；已评审记录；修订草稿负责人可打开；应用理顺与登记；借用户电脑探活；页面地址统一到 /assets 下 |
| 2026-10-11 | asset-latest-compat | 基线 18023df；待Root提交 | 通过（定向14通过/1 PostgreSQL跳过） | qa_agent_environment | agent_bundle HTTP40hex/base64、2规范全文/旧ID内容、范围与revision复读、legacy附件/storage；无模型/现场变更，详细证据见独立贡献 |
| 2026-10-12 | full-test-fixes-20261012 | 基线d511a7a；待Root提交 | 通过（原生make check：423通过/2条件跳过；PG迁移单项1通过） | implementer_specs_asset_fixes；待Root文审 | 两条真实Docker测试独立UUID资源、动态localhost及归属清理，保留镜像/volume；4边界回归、真实Docker2条定向通过；无模型/原服务/原库变化，完整证据见本轮贡献 |
| 2026-10-12 | full-test-fixes-20261012 文审 | 基线d511a7a；待Root提交 | 通过（文档窄验证；未重跑make check） | implementer_runtime_deploy_fixes；待Root验收 | docs-impact-control核对最终diff，README0.4补条件锁检查与Docker构建/跳过前提；产品代码和测试未改，报告见本轮文审贡献 |
| 2026-10-12 | full-test-fixes-20261012 锁后继文审 | 基线d511a7a；待Root提交 | 通过（online/offline锁检查与隔离build；文审窄验证） | implementer_specs_asset_fixes实现；implementer_runtime_deploy_fixes文审；待Root验收 | 首轮共享PATH无uv未跑条件门禁，Root缺锁红证据保留；真实45包uv.lock补齐，19直接pin/原pyproject不变，未sync共享venv或重跑423项；README0.5及文审贡献记录后继证据 |
| 2026-10-16 | discussion-task-20261016 | 基线2ae58f2（含dev1005 b74740a）；待Root提交 | 通过（完整make check：482通过/2条件跳过/3警告；head 0008_app_health_source） | reviewer_resource_relation实现；Root整合与文审 | 任务资产读写/摘要/Agent SOP按可信工作项归属逐次授权，兼容旧空slug并拒绝伪造/错项目；严格整数真实步骤与不可见资产保护，生成契约及README0.7同步。定向33通过；共享磁盘首次外部143及后继I/O受控中止日志保留，TMPDIR独立tmpfs完整检查05:48:24–05:53:18 UTC退出0；模型网关/PostgreSQL搬迁条件跳过，PATH无uv未触发条件锁检查，未Git写/部署/真模型。 |
| 2026-10-19 | message-history-repair-sync-20261019 P1后续切片 | 待后续切片 | 未实现/未验收 | implementer_asset_sync_guards | `mandatory.py:add` 捕获并发唯一键 `IntegrityError` 后未使用 savepoint/回滚便继续 SELECT，真实 PostgreSQL 事务可能已中止；依仓库评审规则转后续独立切片补并发回归，本轮仅修审批列表P0和生成契约。 |
