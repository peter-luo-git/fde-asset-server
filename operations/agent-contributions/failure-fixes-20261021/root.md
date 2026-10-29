# Root：固定冒烟补齐及集成

2026-10-21；分支codex/failure-fixes-20261021。reviewer_docs_refresh实施专用重排协议与安全日志，implementer_web_qa独立审代码/既有文档，Root统一Git。

Root将本轮test_rerank_http_contract.py纳入既有固定smoke.py；测试runner核实际pytest argv的回归先RED后GREEN，相关runner7pass。保留全部既有入口、60秒总预算及45秒pytest预算、独占PG清单/清理与无skip/零用例/旧报告拒绝。README对应说明经独立文审最小同步。

产品34pass/1明确授权外live skip见实施者贡献和原XML；Root最终固定双冒烟、Ruff/format/diff结果以evidence与hook实际输出为准。无全量/真实模型/业务服务、无依赖或公开合同变化；不将mock协议验证报告为真实语义质量通过。
