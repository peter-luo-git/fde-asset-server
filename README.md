# fde-asset-server

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

## 与其他仓库的关系

| 仓库 | 关系 |
|---|---|
| `fde-specs` | 规格与契约来源，以 submodule 挂在 `specs/` |
| `fde-server` | 消费方：取快照与 SOP 步骤摘要、上报使用事件；提供成员关系查询 |
| `fde-web` | 前端，资产页面在 `src/features/asset/` |
