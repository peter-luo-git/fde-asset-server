# 最新资产版本兼容文档审核

文档版本：0.1（2026-10-11）。Agent：researcher_server_chat；模式docs-impact-control update。工作树 `/opt/fde/.run/worktrees/asset-latest-compat`，branch `codex/asset-latest-compat`，baseline `18023dfaad161bddf7e67095fd8277c111e9babe`。Root明确授权README与本独立贡献，覆盖本仓默认评审只写LOG的记录约束；不修改QA拥有的LOG/tests或其他作者贡献。

## 影响矩阵

| 证据 | 文档/分类 | 动作 |
|---|---|---|
| 当前baseline已有`routes_internal.py:132`与`agent_bundle.py`；Server7548733 `AssetBundleReader`调用该接口，旧4fda0ae无路由返回404 | README运行/跨仓说明：required | 固定最新版本匹配；明确40hex内容revision、base64files与Server严格读取后prepare，非模型故障或Git提交身份 |
| 内部router不入公开schema、require_service_key；首次组包member检查与revision复读分支 | README内部接口：required | 服务密钥404、初次组包非成员403、缺失/非法snapshot404；不把revision复读误称每次重查成员 |
| `legacy_layout.migrate_legacy_repos`搬主分支/归档旧repo/退回评审；Root数据副本20旧ID与有效性保持 | README运行说明：required | 区分副本验证、完整备份和未验证恢复；不能只降镜像后继续用转换数据 |
| Server快照有效owner白名单；Root两seed的department:data-intel当前不存在 | README兼容限制：required | 记录安全拒绝，不能因兼容改owner或放宽门禁；两规范可prepare不等全部资产可用 |
| 本次业务无diff、接口已存在且include_in_schema=False；`contracts/asset-v1.yaml`不包含internal路由 | 既有公开契约/生成器：no-impact | 不手改公开OpenAPI、不再生成；README足以解释内部接口，无另造契约套件 |

## 检查与边界

完整读取skill/impact-guide及仓AGENTS、原状态；全文核新增`tests/platform/test_agent_bundle_compat.py`及既有routes/bundle/legacy源码、QA贡献和LOG实际diff；Server只读固定7548733 Reader与CompanyAssetSnapshots owner校验。首次inventory仅QA测试untracked，后续QA追加LOG和贡献均保留，本child无业务/测试/依赖/现场或Git写操作。

QA原证据见[qa_agent_environment.md](qa_agent_environment.md)：新bundle3项加旧sharing1项4pass/15.23秒，catalog/storage10pass/1PGskip/37.78秒，分次共14不同用例；首次环境无pytest及补测试搜索路径的过程保留。测试受控临时库，不是现场服务/PG迁移；本child不重跑。Root候选真实Reader10files/2rules、revision复读和副本20ID保持是上级独立事实，不累计为child测试数。

本次只更新[README](../../../README.md)，新增本记录；业务、公开DTO/鉴权、依赖、现有迁移协议均不新增。尚未切现场、不模型调用或宣modelpassed；未做完整make check或备份恢复演练。Root负责最终部署/验收和本地提交，无文档阻塞。

## 文档窄验证

README限定`git diff 18023df --check` exit0；两份本角色文档回读/版本/变更记录/空白/末尾换行与3个新增本地链接目标PASS。README实际16行新增；首次实质编辑后核精确cwd/root/branch/status/diff与未跟踪所有权，保留QA的LOG/tests和Root贡献。仓Makefile只有应用检查，无专用docs构建，不运行make check或伪造生成结果。

Root已审阅接受本轮README/贡献，并后继确认现场镜像已切18023df、真实Reader10files/2rules通过。按Root明确分工，本记录及README保留先前候选/副本审核时点，现场后继与版本记录由Root追加，不重叠编辑；未因此宣称真实模型轮通过。

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-11 | 0.1 | 记录最新资产bundle兼容、内部接口与副本迁移的文档影响矩阵、QA证据和未部署/恢复边界。 |
