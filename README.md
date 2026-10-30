# fde-asset-server — FDE 资产中心

文档版本：1.6（2026-10-30）。

管理规范、SOP、技能和知识的检索、沉淀、评审、分发与复用。通过 HTTP 与 `fde-server` 协作，独立使用 PostgreSQL 和资产 Git 仓库，不共享平台数据库。

## 核心能力

- 支持 Rule、Sop、Skill、Solution、Implementation、Case、Experience 七类资产及范围可见性。
- 提供技能导入与依赖检查、知识附件解析、检索推荐、会话沉淀建议和版本修订。
- 为 Agent 选择规则、技能与知识快照；SOP 按任务绑定版本和当前步骤执行，最终验收仍归平台。
- [project-development](src/fde_asset/modules/asset/showcase_files/project-development/SKILL.md) 是可选示范 Skill，管理员显式安装后按正常关联与选择流程使用。

## 本地开发

准备 Python 3.12、uv 和独立 PostgreSQL 数据库；本地仓库模式无需 Gitea 或模型凭据：

```bash
uv sync --frozen --dev
cp .env.example .env
```

编辑 `.env`，配置必填的 `FDE_ASSET_DATABASE_URL` 和本地目录，再导出配置并启动：

```bash
set -a
. ./.env
set +a
make migrate
make dev
```

API 默认监听 `http://127.0.0.1:8100`；另一个加载同一配置的终端运行 `make worker`，处理索引与快照。完整平台使用 `FDE_ASSET_IDENTITY_MODE=platform` 对接 Server 身份，详见 [配置定义](src/fde_asset/settings.py)。升级前备份数据库、资产仓库与附件。

## 验证

| 命令 | 用途 |
|---|---|
| `make check-fast T=tests/asset/test_manifest.py` | 开发中定向检查 |
| `make check` | Ruff、格式、编译、完整 pytest、迁移头及条件依赖锁检查 |
| `python scripts/smoke.py --python /绝对路径/python --report /绝对路径/asset-smoke.xml` | 60 秒无模型冒烟 |

测试使用有建库权限的独立 PostgreSQL，地址由 `FDE_ASSET_TEST_DATABASE_URL` 指定；冒烟必须显式提供该值。真实模型和 Docker 专项须单独启用，跳过不计通过。

语义重排使用专用 `FDE_ASSET_RERANK_BASE_URL`、`FDE_ASSET_RERANK_API_KEY`、`FDE_ASSET_RERANK_MODEL_NAME`；聊天模型或生成推荐理由不能代替 `/rerank` 能力。

## 关键入口

- [contracts/asset-v1.yaml](contracts/asset-v1.yaml)：公开 API 契约；[scripts/export_openapi.py](scripts/export_openapi.py)：导出工具。
- [src/fde_asset/](src/fde_asset/)：API、资产处理、身份与存储。
- [migrations/](migrations/)：数据库迁移；仅支持 PostgreSQL。
- [tests/](tests/) 与 [Makefile](Makefile)：检查入口；[环境说明](tasks/ENV_BLOCKERS.md)：外部依赖限制。

## 变更记录

- 2026-10-30，1.6：精简为当前能力、启动、验证及契约入口，移除首页历史部署与逐轮测试记录。
