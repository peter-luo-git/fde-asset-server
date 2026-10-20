# Asset测试体系更新

- 日期2026-10-20，基线0f3da9c；用户授权更新旧测试、删除失配入口，相关验证后合并本地main。
- Root实施：模型/Docker验收显式开关，默认移除模型环境，不再自动读.env.local；真实执行失败不得skip；契约生成使用fixture独占PG；60秒固定34项冒烟，拒绝缺前提/空/skip/旧报告。
- reviewer_test_refresh先独立审查并发现旧报告、超时数据库残留；Root修旧报告，child追加实施精确owned清单及父finally清理。45秒pytest执行预留强杀/清理预算，Server外层60秒。真实强杀PG验证owned库删除、foreign对照库保留，测试资源已清。
- 相关策略/runner/rerank测试28通过、1显式模型跳过；应用/Compose/资源50通过、2显式Docker跳过；初轮固定PG冒烟34通过/7.20秒。最终双冒烟由Server提交门槛执行，独立报告保存任务根。
- 研究：2026-10-20官方pytest goodpractices https://docs.pytest.org/en/stable/explanation/goodpractices.html 采用显式发现/隔离，适用锁定pytest8.3.4；Playwright https://playwright.dev/docs/test-webserver 参考显式目标和前提机制，Asset沿用既有pytest/PG不引入新框架或复制源码。
- 文档：README更新开关、预算、清单资源归属；历史LOG成绩保留原时点。未改业务源码、依赖、契约；未全量、真实模型、真实Docker部署、推送。

- 最终Server提交hook真实调用本入口：34 passed，pytest7.39秒、入口8.96秒，小于60秒，无skip；同门槛Server236项/554.91秒亦通过。最终全部相关策略28pass/1显式模型skip；无需或未获授权运行的真实模型/Docker/full不算通过。
