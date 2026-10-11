# 最新资产服务兼容接入 — Root

文档版本：0.2（2026-10-12）。

- 目标：按用户要求使用GitHub `peter-luo-git/fde-asset-server` 的最新dev1005版本，恢复Agent必读公司规范及项目资产的启动链路。
- 项目工作树：`/opt/fde/.run/worktrees/asset-latest-compat`，基线 `18023dfaad161bddf7e67095fd8277c111e9babe`；远端fetch核实该分支最新提交，远端main仍为265a075，沿用用户先前指定dev1005。
- 根因：旧现场镜像4fda0ae提供catalog但不提供bundle，后端7548733调用bundle得到404；同时Worker记录CompanyAssetGitError，失败发生于准备资产快照而非模型或ACP帧上限。
- 实现：复用上游18023df已有bundle及旧布局转换，不重复开发接口，不放宽权限/快照校验。测试子Agent补独立回归用例；文审子Agent同步README。
- Root操作：构建镜像fde-asset-server:18023df；短暂停旧asset服务后备份1.7MiB数据卷并建立副本，恢复旧服务；候选容器仅挂副本并复用受保护环境，不修改原凭据。副本验证通过后更新`/opt/fde/.run/dev.env`的资产镜像并重建asset服务。
- 验证：副本及现场通过真实Server AssetBundleReader，10个文件、2份规范，固定revision复读一致；副本迁移前20个资产ID均保留且原有效资产仍有效。CompanyAssetSnapshots.prepare成功，2份规则载入。两份旧种子Skill/Case属于当前平台不存在的department:data-intel，诊断ASSET_METADATA_INVALID；不伪造owner，未扩大资产权限。
- 备份：私有`/opt/fde/.run/asset-latest-compat/asset-data-before.tar.gz`、环境回滚`dev.env.before`。回滚需停止新版后恢复数据备份及旧镜像，不能只切镜像忽略目录迁移。数据及环境文件不进入Git。
- 贡献：qa_agent_environment独立14项通过/1 PostgreSQL迁移跳过；researcher_server_chat负责文档影响审核与更新；Root取证、真实副本/现场验证、构建和本地集成。
- 现场上线验证：真实Casdoor登录后调用项目coder-default的online治理接口，HTTP200；同项目presence持久状态从error恢复为idle，新建运行镜像为已更新的默认DSH镜像。未发送消息、未重试历史失败轮、未调用模型。前端实际截图保存在私有运行证据目录，截图不作Git文件。
- 浏览器验证过程：初次系统Node18不兼容Playwright，改用已安装Node22；旧登录态失效导致导航中断，改用真实登录；首次手写启动请求缺少CSRF头被403正常拒绝，补与产品一致的CSRF头后成功。页面截图等到状态刷新；这些脚本问题不归为业务启动失败。
- 限制：保持当前SQLite/本地仓配置，未主动切PostgreSQL/Gitea；未全量测试或模型对话验收。只验证本次用户报告的阿码，闪电历史异常未自动启动；失败轮仍保留，由用户明确重试。

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-12 | 0.2 | 补真实登录/上线HTTP200与idle、新运行容器及浏览器验证过程，明确零模型轮与保留历史失败。 |
| 2026-10-12 | 0.1 | 记录资产服务版本错配根因、最新上游接入、副本及现场验证与回滚边界。 |
