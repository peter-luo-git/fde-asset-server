# reviewer_docs_refresh：Asset 专用语义重排修复

角色：实现与相关验证；本轮不充当自己改动的独立复审。逻辑名 reviewer_docs_refresh，运行时ID未提供。日期2026-10-21（Asia/Hong_Kong），工作树 `/home/hbw/projects/FDE/.run/failure-fixes-20261021/asset`，分支 `codex/failure-fixes-20261021`，基线 `c08f5fe0d4ffa39f9e3cb93336c1d547fd5559dc`。Root负责独立审查、暂存、提交和合并；无Git写操作。

## 范围与现行合同

用户要求修复已确认问题；Root先核对最小方案后明确接受保留专用rerank，不扩展ask到生成式fallback、不新增模型路由/平台。Specs `contracts/openapi/asset-v1.yaml:728-730` 和 `docs/architecture/资产中心-后续工作清单.md:121-124` 指定SQL权限过滤后全量可见文档交专用模型评分，未配/失败退关键词；既有生成式推荐fallback只处理最多30候选，不能直接替代ask最多500文档、0–1阈值的合同。

2026-10-21核对官方 [Cohere Rerank API v2](https://docs.cohere.com/reference/rerank)：query/documents/top_n与返回index/relevance_score明确属于专用评分协议。采用其评分响应结构核验机制；FDE仍使用现有通用HTTP客户端、可配置base加`/rerank`及`data/document_index/score`兼容格式，不引入Cohere SDK、不更改路由或依赖。没有复制外部代码。此前chat live-rerank成功不证明本次专用语义验收通过，也未把原HTTP未知错误补写成404。

## 实际逐文件修改

- `src/fde_asset/platform/llm/reranker.py`：`parse_results`拒绝缺评分列表、chat-shaped JSON、错误列表/条目、无效/重复索引、bool/非整数索引、缺失/bool/非有限/超范围分数；保留两种字段格式、有效空列表和按分数排序。`HttpReranker.rank`另拒绝超出本次documents范围的索引。
- `src/fde_asset/modules/asset/ask.py`：保持原SQL可见性、阈值、排序和keyword退路；只在异常边界增加logger，内容限异常类型及HTTP状态，不记录异常正文、URL、key、query、资产正文或traceback。
- `tests/asset/test_rerank_http_contract.py`：新增23项纯离线协议/分支回归；真实客户端经MockTransport，不起服务/数据库，显式拒绝socket连接；覆盖错误响应不能semantic、安全日志、阈值、排序/空响应及索引边界。目录execute/行是合成替身，不宣称PG ACL已实测。
- `tests/asset/test_rerank.py`：原“无index行返回空数组”断言改为明确协议错误，避免无效模型输出算成功空排名。
- `README.md`：“本地验证”新增两段专用RERANK配置、生成式成功用例范围、严格响应、安全诊断与相关测试入口。
- 本贡献记录。没有配置、依赖、公开API schema或生成文档变更。

## RED / GREEN 与检查

证据根：`/home/hbw/projects/FDE/.run/failure-fixes-20261021/evidence/reviewer_docs_refresh`。

1. 先执行新文件：`env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /home/hbw/projects/FDE/.run/test-system-refresh-20261020/asset-env/bin/python -m pytest -q -p no:cacheprovider --tb=short tests/asset/test_rerank_http_contract.py --junitxml=<证据根>/asset-rerank-red.xml`，20失败/3通过。失败包含错误响应仍semantic、无协议拒绝及缺诊断日志，非依赖或网络错误。
2. 修复后执行同解释器 `-m pytest -q -p no:cacheprovider --tb=short tests/asset/test_rerank_http_contract.py tests/asset/test_rerank.py --junitxml=<证据根>/asset-final.xml`：34通过/1显式live-model跳过，未运行真实模型。保留既有Starlette/pytest-asyncio弃用警告。
3. 既有固定Ruff二进制 `/home/hbw/projects/FDE/.run/full-e2e-20261019/qa_asset/source/.venv/bin/ruff` 对上述四个Python文件 `check --no-cache` 和 `format --check` 均通过。首次 `python -m ruff` 因复用venv路径下缺启动文件失败，改用同一既有锁定二进制；没有安装/升级工具。
4. `git diff --check`与实际diff/新增文件内容核对；未运行完整make check、固定PG冒烟、PG权限集成或真实上游模型，遵循本轮仅相关离线授权。

## 文档影响审计（实现者自检，Root另派独立复审）

1. **Scope**：上述基线和写集；README仅Root明确授权的本地验证段落，update。
2. **Impact matrix**：专用HTTP严格响应和安全日志→README本地验证，required；生成式live用例不覆盖ask→同段，required。
3. **Documentation changes**：已补上述两段，命令与实际模块/环境变量核对。
4. **No-impact**：启动、DB迁移、制品、历史验收、平台身份及仓库关系未变，保留README其他段落；Specs公开HTTP返回结构未变，不修改冻结schema或历史设计。
5. **Verification**：对应离线测试及Ruff通过；本地文件链接交Root汇总检查，未修改生成输出。
6. **Unresolved**：真实rerank路由/模型尚未获本子任务授权配置或调用；Web的显式RERANK前置检查由Root维护。keyword不能算semantic验收通过。

## 状态与清理

实现及相关验证完成，交Root独立审查；无后台任务、容器、数据库、模型调用或凭据副本，无待清理外部资源。只读旧失败证据保留。外部合同 `/home/hbw/.codex/agent-state/fde/failure-fixes-20261021/reviewer_docs_refresh/AGENT.md` 随两仓实施最终收口更新。
