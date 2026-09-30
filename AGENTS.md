# fde-asset-server 协作入口

本仓库是 **FDE 资产中心服务**：资产索引、目录、沉淀、评审、分发与复用计量。与 `fde-server` 通过 HTTP 契约和领域事件交互，**不共享数据库、不互相 import 代码**。

## 1. 边界

- 只改本仓库。需要 `fde-server` 或 `fde-web` 配合的变更，写进任务卡，按"契约先行"顺序合并（先本服务发接口，再对方接）。
- 对外契约以 `specs/contracts/openapi/asset-v1.yaml` 为准；改契约先改契约，再改实现。
- 不得在资产数据里携带 provider、model、credential、系统提示词等运行时配置。
- 公司级资产不得包含客户名称或客户敏感词，由提交前扫描阻断。

## 2. 完成标准

`make check` 绿 = 切片完成。结论只有两种：**通过 / 需修改**，不发明新的结论词。

```
make check-fast T=tests/asset/test_manifest.py   # 开发中
make check                                        # 合并前
```

## 3. 评审规则

1. 评审输入只有三样：本切片的 `git diff`、任务卡、`make check` 输出。不复述历史。
2. 每条发现标注严重级别与 `文件:行号`：**P0 阻塞**（安全越权、数据损坏、契约破坏、fail-open）必须本轮修；P1 转成新切片；P2 只记一行。
3. 每个切片最多 2 轮评审，第 2 轮仍有 P0 由人决定降范围或拆卡。
4. 评审不产生新的 Markdown 文件，结论写进 `tasks/LOG.md` 一行。

## 4. 环境阻塞

真实 dsh、Gitea、PostgreSQL 等见 `tasks/ENV_BLOCKERS.md`。**任何切片都不因为这些被判为未完成**，标记为"代码完成，待环境验收"即可。

## 5. 提交

- 中文提交说明 `type(scope): 摘要`；代码与文档可以在同一个提交里。
- 未经明确授权不 push、不建 PR、不部署。
