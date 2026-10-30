# other_services — Asset 上游整合

- 状态：completed；日期：2026-10-30；角色：implementer；运行时标识：`/root/other_services`，逻辑名沿用 Root 合同。交接 Root，状态日志：`/home/hbw/projects/FDE/.run/runtime-protocol-20261030/other_services/AGENT.md`。
- 项目：`/home/hbw/projects/FDE/.worktrees/runtime-protocol-20261030/fde-asset-server`；分支：`codex/runtime-protocol-20261030-asset`；开始基线：`abc34a6c5dcf5f26d3188d952692b51511724d30`；整合上游：`upstream/dev1005` 的 `865f16d8082016917f7c05fc478fac286b20e883`。
- 目的：本机全服务更新时保留上游历史，避免同一项目开发流程出现两个示范条目。Root 明确批准保留本地规范入口与更完整正文。

## 核对与贡献范围

- 上游提交新增 `fcp-project-development` 长入口与示范元数据；本地 `project-development` 已将同源 Runtime 正文迁为可选资产，短入口仅在明确选择时读取 references，且正文保留后续独立读取、文档任务/repo 可空等修订。
- 合并中去掉上游新增重复目录及其元数据，保留原 `showcase_content.py`、`showcase_files/project-development/` 和 `tests/asset/test_showcase.py` 的全部本地内容。未认领原正文、测试或上游作者的实现；本 Agent 编写本记录并执行比较、去重整合和验证。
- 相对原 main 的唯一最终文件增量为本贡献记录。原工作区 README 未提交修改保持原样；未修改其他仓库源码或 live。

## 成熟机制与复用

- 2026-10-30 重新核对 [Anthropic 官方 Agent Skills 概览](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)，在线滚动文档，无固定 SDK 版本。其 metadata / instructions / resources 分层和按需读取参考资料机制支持保留现有短入口结构。
- 差异：FDE 当前入口按用户要求收窄为显式选择，不把一般开发请求当作触发条件；沿用既有 `_folder`、安装、版本和复制机制，不新增状态系统。结构降低流程预读风险是机制推断，本轮未调用真实模型验证语义遵从。
- 未引入官方 SDK、第三方依赖或复制第三方源码。上游内容源于项目既有 Runtime 文本，最终不新增该副本；保留上游 Git 作者历史与本地既有正文，不新增或删除许可证声明。

## 验证与文档影响

- 去重后、添加本记录前：`git write-tree` 与原 main 的树均为 `be109596c6d88c8384aab6ca8efd1bfc665b63ad`，整树字节一致；`git diff main --exit-code -- src tests README.md` 和 `git diff --cached --check` 通过。
- `python -m pytest tests/asset/test_showcase.py::test_project_development_workflow_preserves_delivery_guidance -q`：1 passed；合并后运行整个相关文件：2 passed，3 项数据库夹具 setup error。旧隔离 PG 53619 已关闭，提升权限复查仍为 connection refused；未作为功能失败或通过证据。
- `ruff check` 与 `ruff format --check` 针对 `showcase_content.py`、`test_showcase.py` 均通过。Root 随后明确要求以精确树对象证明无行为变化，无需重新准备数据库或补跑无变化的 DB 用例。
- README 导航仍准确指向唯一可选 `project-development`；公开 API、schema、迁移、安装与关联行为均无变化，文档分类为 no-impact，未改 README。无生成文档变更。
- 未运行全量测试、全量 E2E、真实模型或真实消息；未 push、改 main 或部署。Web/Asset 镜像来源与部署由 Root 负责；本记录不宣称已上线。未决问题：无代码阻塞，DB 集成检查本轮未完成，已有树等价证据界定其影响。
