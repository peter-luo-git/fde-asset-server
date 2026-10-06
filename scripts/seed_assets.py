"""构造演示用的资产数据：三类仓库、七种类型、多格式附件，以及身份与活动种子。

这些内容是虚构的示例，不含任何真实客户信息。
"""

from __future__ import annotations

import io
import json
from typing import Any

from fde_asset.platform.repo.local_git import LocalGitRepo
from fde_asset.platform.repo.ports import RepoRef
from fde_asset.settings import AssetSettings

TODAY = "2026-09-30"


# ---------------------------------------------------------------- 附件生成


def make_docx(title: str, paragraphs: list[str]) -> bytes:
    from docx import Document

    document = Document()
    document.add_heading(title, level=1)
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_xlsx(rows: list[list[Any]]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "配置基线"
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def make_pptx(title: str, bullets: list[str]) -> bytes:
    from pptx import Presentation
    from pptx.util import Inches

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = title
    box = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(8), Inches(4))
    frame = box.text_frame
    frame.text = bullets[0]
    for bullet in bullets[1:]:
        frame.add_paragraph().text = bullet
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def make_pdf(lines: list[str]) -> bytes:
    """手写一个最小可解析 PDF（只有文本层），避免引入额外依赖。"""
    content_lines = ["BT", "/F1 12 Tf", "72 760 Td", "14 TL"]
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        content_lines.append(f"({escaped}) Tj")
        content_lines.append("T*")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


# ---------------------------------------------------------------- 资产内容


def _yaml(
    kind: str,
    name: str,
    title: str,
    summary: str,
    owner: str,
    *,
    industry: list[str],
    suitable: str,
    not_suitable: str,
    extra: str = "",
    source: dict[str, str] | None = None,
    tags: list[str] | None = None,
    lifecycle: str = "stable",
    nature: str = "",
) -> str:
    lines = [
        "apiVersion: fde.asset/v1",
        f"kind: {kind}",
        "metadata:",
        f"  name: {name}",
        f"  title: {title}",
        f"  summary: {summary}",
        f"  tags: [{', '.join(tags or [])}]",
        "spec:",
        f"  owner: {owner}",
        f"  lifecycle: {lifecycle}",
        f"  industry: [{', '.join(industry)}]",
        "  applicability:",
        f"    suitable: {suitable}",
        f"    notSuitable: {not_suitable}",
        "  version: 1.0.0",
    ]
    if nature:
        lines.append(f"  nature: {nature}")
    if extra:
        lines.append(extra.rstrip())
    if source:
        lines.append("  source:")
        for key, value in source.items():
            lines.append(f"    {key}: {value}")
    return "\n".join(lines) + "\n"


def company_files() -> dict[str, bytes]:
    files: dict[str, str | bytes] = {}

    files["rules/00-security-redlines.md"] = """# 安全红线

## 结论
任何交付物都不得包含真实凭据、客户名称与内网地址。

## 要求
1. 提交前必须通过敏感信息扫描。
2. 客户数据样例一律使用虚构数据。
3. 生产变更必须有回滚方案。

## 例外
无。发现例外需求先提规范变更。
"""
    files["rules/10-engineering.md"] = """# 工程规范

## 结论
代码分层清晰、变更可回滚、测试与实现同一提交。

## 要求
1. 每个模块只写自己的数据表。
2. 跨模块通知走领域事件。
3. 迁移脚本必须验证升级与回滚。
"""

    files["sops/l1/delivery-standard/SOP.md"] = """# 通用交付流程（L1）

## 结论与目的
所有行业通用的交付主干：从调研到验收，保证每个项目有一致的骨架。

## 适用范围
所有交付项目。

## 角色
项目 Owner 负责整体，FDE 工程师负责执行。

## 前置条件
项目已创建、仓库已关联、成员已加入。

## 步骤
1. 调研与方案确认
   梳理业务现状与目标，输出方案初稿。
   - 检查点：客户确认方案范围
   - 产出：方案文档
2. 环境与数据准备
   准备环境、打通数据通路。
   - 检查点：环境连通性验证通过
   - 产出：环境清单
3. 实施与验证
   按方案实施并自测。
   - 检查点：验收用例全部通过
   - 产出：实施记录
4. 验收与复盘
   客户验收，团队复盘并沉淀资产。
   - 检查点：三件套（方案 / 实施 / 复盘）已提交
   - 产出：复盘报告

## 异常处理
任一检查点不通过时停止进入下一步，登记阻塞。

## 变更记录
- 2026-09-30 初版
"""
    files["sops/l1/delivery-standard/asset.yaml"] = _yaml(
        "Sop",
        "delivery-standard",
        "通用交付流程（L1）",
        "四步主干：调研 → 环境 → 实施 → 验收，每步都有检查点",
        "department:platform",
        industry=["general"],
        suitable="所有交付项目",
        not_suitable="纯售前支持，没有交付动作",
        extra="  layer: L1",
        tags=["delivery"],
    )

    files["sops/l2/bank-core-migration/SOP.md"] = """# 银行核心系统数据迁移（L2）

## 结论与目的
在通用交付流程之上，补充银行核心迁移特有的双跑比对与割接窗口要求。

## 适用范围
城商行与农商行核心系统迁移，日交易量 500 万以下。

## 角色
数据工程负责迁移脚本，业务顾问负责比对口径。

## 前置条件
源库版本已确认，已获得停机窗口。

## 步骤
2. 环境与数据准备（行业增强）
   除通用要求外，必须准备双跑环境。
   - 检查点：双跑环境与生产版本一致
   - 产出：双跑环境清单
3. 双跑比对
   连续三轮全量比对，差异必须归零。
   - 检查点：连续三轮差异为 0
   - 检查点：比对报告已由业务确认
   - 产出：比对报告
5. 割接与回滚演练
   按割接方案执行，并演练一次回滚。
   - 检查点：回滚演练成功
   - 产出：割接记录

## 异常处理
比对差异未归零不得进入割接。

## 变更记录
- 2026-09-30 初版
"""
    files["sops/l2/bank-core-migration/asset.yaml"] = _yaml(
        "Sop",
        "bank-core-migration",
        "银行核心系统数据迁移（L2）",
        "在通用流程上增加双跑比对与割接回滚演练两道关口",
        "department:data-intel",
        industry=["banking"],
        suitable="城商行 / 农商行核心迁移，日交易量 500 万以下",
        not_suitable="分布式核心；跨币种清算系统",
        extra="  layer: L2\n  extends: l1/delivery-standard",
        tags=["banking", "migration"],
    )

    files["skills/insurance-policy-import/SKILL.md"] = """---
name: insurance-policy-import
description: 在保险项目中导入保单流水时使用，提供字段映射、日期与金额格式处理、失败记录容错策略。
---

# 保单流水导入

## 适用场景
保险行业保单流水批量导入，CSV 或定长文本。

## 步骤
1. 读取字段映射表，确认必填字段齐全。
2. 日期按三种格式依次尝试解析，失败记录进错误表。
3. 金额统一转为分，避免浮点误差。
4. 导入完成后输出成功、失败、跳过三类计数。

## 注意事项
失败记录必须保留原始行号，便于回溯。

## 示例
参见 references/ 下的字段映射示例。
"""
    files["skills/insurance-policy-import/asset.yaml"] = _yaml(
        "Skill",
        "insurance-policy-import",
        "保单流水导入",
        "三种日期格式容错 + 金额转分 + 失败行可回溯",
        "department:data-intel",
        industry=["insurance"],
        suitable="保险保单流水批量导入",
        not_suitable="实时接口对接；非结构化影像件",
        source={"customerCode": "CUST-A", "engagementSlug": "policy-import", "workItemId": '"12"'},
        tags=["data-import", "python"],
    )

    files["knowledge/solutions/bank-core-migration-solution/README.md"] = """# 银行核心迁移方案

## 结论
面向城商行核心系统迁移，采用双轨并行 6 周方案，割接前完成三轮数据双跑比对。

## 客户类型与场景
资产规模 500 亿以下的城商行，核心系统从 Oracle 迁移到 PostgreSQL。

## 方案概述
分四个阶段：评估与建模、迁移工具准备、双跑比对、割接与运行护航。

## 关键能力
- 结构与数据双通道迁移
- 逐表差异比对与自动补偿
- 割接窗口压缩到 4 小时内

## 交付物清单
迁移方案、配置基线、双跑比对报告、割接手册、回滚预案。

## 适用边界
不适用于分布式核心与多法人共用一套核心的场景。
"""
    files["knowledge/solutions/bank-core-migration-solution/asset.yaml"] = _yaml(
        "Solution",
        "bank-core-migration-solution",
        "银行核心迁移方案",
        "双轨并行 6 周，割接前完成三轮数据双跑比对",
        "department:data-intel",
        industry=["banking"],
        suitable="资产规模 500 亿以下的城商行核心迁移",
        not_suitable="分布式核心；多法人共用核心",
        tags=["banking", "migration"],
    )
    files["knowledge/solutions/bank-core-migration-solution/attachments/architecture.pptx"] = (
        make_pptx(
            "银行核心迁移方案架构",
            [
                "双轨并行：旧核心与新核心同时运行",
                "比对引擎：逐表差异比对",
                "割接窗口：4 小时",
                "回滚：保留旧核心 30 天",
            ],
        )
    )

    files[
        "knowledge/implementations/oracle-to-pg-cutover/README.md"
    ] = """# Oracle 迁移 PostgreSQL 割接手册

## 结论
割接按停写、追平、校验、切流四步执行，任一步失败立即按回滚预案退回。

## 环境与前置条件
PostgreSQL 17，双跑环境已连续三轮比对差异为 0。

## 实施步骤
1. 停写旧库并记录最后一个事务号。
2. 增量追平到新库。
3. 执行一致性校验脚本。
4. 切换应用连接串并观察 30 分钟。

## 配置基线
见 attachments/baseline.xlsx。

## 验证方法
核对总笔数、总金额、关键业务查询三类指标。

## 割接方案
停机窗口 4 小时，凌晨 1 点开始。

## 回滚预案
切流后 30 分钟内出现严重问题，立即把连接串切回旧库，新库数据留存备查。

## 常见问题
- [[case/oracle-to-pg-sequence-gap]]
"""
    files["knowledge/implementations/oracle-to-pg-cutover/asset.yaml"] = _yaml(
        "Implementation",
        "oracle-to-pg-cutover",
        "Oracle 迁移 PostgreSQL 割接手册",
        "停写、追平、校验、切流四步，30 分钟内可回滚",
        "department:data-intel",
        industry=["banking"],
        suitable="Oracle 到 PostgreSQL 的停机割接",
        not_suitable="不停机双向同步场景",
        tags=["oracle", "postgresql"],
    )
    files["knowledge/implementations/oracle-to-pg-cutover/attachments/baseline.xlsx"] = make_xlsx(
        [
            ["参数", "推荐值", "说明"],
            ["max_connections", "500", "按应用连接池上限设置"],
            ["shared_buffers", "16GB", "物理内存的四分之一"],
            ["sequence cache", "1", "避免序列跳号"],
        ]
    )

    files[
        "knowledge/cases/oracle-to-pg-sequence-gap/README.md"
    ] = """# Oracle 迁移 PostgreSQL 后序列跳号

## 结论
序列缓存导致跳号，不是数据丢失；对连续性敏感的序列设置 CACHE 1，或向业务说明。

## 现象
迁移后订单号从 1021 直接跳到 1041，业务方报告"数据丢失"。

## 影响
业务对账人员误判，一度要求回滚割接。

## 根因
PostgreSQL 序列默认带缓存，会话结束时未用完的值被丢弃，与 Oracle 行为不同。

## 解决方法
对连续性敏感的序列执行 ALTER SEQUENCE ... CACHE 1；其余向业务说明不影响唯一性。

## 如何避免
割接前在配置基线里明确列出序列缓存策略，并在业务培训材料里说明。

## 判断要点
看着像数据丢失，其实是序列缓存。回滚的代价远大于解释成本，先查序列再谈回滚。

## 相关资产
- [[impl/oracle-to-pg-cutover]]
- [[rule/00-security-redlines]]
"""
    files["knowledge/cases/oracle-to-pg-sequence-gap/asset.yaml"] = _yaml(
        "Case",
        "oracle-to-pg-sequence-gap",
        "Oracle 迁移 PostgreSQL 后序列跳号",
        "序列缓存导致跳号而非数据丢失，设置 CACHE 1 或向业务说明",
        "department:data-intel",
        industry=["banking"],
        suitable="存量序列迁移、对连续性敏感的报表场景",
        not_suitable="分布式数据库；业务不依赖序列连续性",
        extra="  caseType: counterexample",
        tags=["postgresql", "migration"],
        nature="judgment",
        source={"customerCode": "CUST-B", "engagementSlug": "core-migration", "workItemId": '"87"'},
    )
    files["knowledge/cases/oracle-to-pg-sequence-gap/attachments/evidence.pdf"] = make_pdf(
        [
            "Sequence gap evidence",
            "order_no jumps from 1021 to 1041 after cutover",
            "root cause: sequence cache default 32",
            "fix: ALTER SEQUENCE order_seq CACHE 1",
        ]
    )

    files["knowledge/experiences/policy-import-poc-retro/README.md"] = """# 保单导入 POC 复盘

## 结论
三条最重要的经验：字段映射要先对齐、样例数据必须脱敏、验收标准写进工作项。

## 项目概况
CUST-A（保险行业），POC 周期三周，两人投入。

## 做对了什么
提前拿到真实字段清单，导入脚本复用了公司技能。

## 踩了什么坑
第一版把金额当浮点处理，导致对账差几分钱。

## 如果重来
第一天就把验收标准写进工作项，而不是做完再补。

## 可沉淀清单
- [x] 金额转分的处理写进 [[skill/insurance-policy-import]]
- [ ] 字段映射清单模板
"""
    files["knowledge/experiences/policy-import-poc-retro/asset.yaml"] = _yaml(
        "Experience",
        "policy-import-poc-retro",
        "保单导入 POC 复盘",
        "字段映射先对齐、样例必须脱敏、验收标准前置",
        "department:data-intel",
        industry=["insurance"],
        suitable="保险行业 POC 复盘参考",
        not_suitable="大型生产项目的复盘（复杂度不同）",
        extra="  projectPhase: poc",
        tags=["retro"],
        lifecycle="experimental",
        source={"customerCode": "CUST-A", "engagementSlug": "policy-import"},
    )
    files["knowledge/experiences/policy-import-poc-retro/attachments/retro-notes.docx"] = make_docx(
        "保单导入 POC 复盘会议纪要",
        [
            "参会：项目 Owner、两名 FDE",
            "结论一：字段映射要先对齐",
            "结论二：金额统一转分",
            "结论三：验收标准必须前置写进工作项",
        ],
    )

    # 两个故意不合格的资产：用来验证「错误可见」
    files["knowledge/implementations/legacy-rollout-no-rollback/README.md"] = """# 旧版上线手册

## 结论
按四步上线。

## 环境与前置条件
无特殊要求。

## 实施步骤
1. 备份
2. 上线

## 验证方法
观察日志。
"""
    files["knowledge/implementations/legacy-rollout-no-rollback/asset.yaml"] = _yaml(
        "Implementation",
        "legacy-rollout-no-rollback",
        "旧版上线手册",
        "按四步上线（缺少回滚预案，公司级应判为无效）",
        "department:platform",
        industry=["general"],
        suitable="历史项目参考",
        not_suitable="新项目",
    )
    files["knowledge/cases/no-summary-case/README.md"] = """# 缺结论的问题记录

## 结论
（空）

## 现象
偶发超时。

## 根因
未知。

## 解决方法
重启。
"""
    files["knowledge/cases/no-summary-case/asset.yaml"] = (
        "apiVersion: fde.asset/v1\nkind: Case\nmetadata:\n  name: no-summary-case\n"
        "  title: 缺结论的问题记录\n  tags: []\nspec:\n  owner: department:platform\n"
        "  lifecycle: experimental\n  industry: [general]\n  applicability:\n"
        '    suitable: ""\n    notSuitable: ""\n  version: 0.1.0\n  caseType: fault\n  severity: S3\n'
    )

    return {
        path: (content.encode("utf-8") if isinstance(content, str) else content)
        for path, content in files.items()
    }


APP_CONSOLE_README = """# 保单导入控制台

## 结论
给运营盯批量导入进度与对账差异的面板，500 万行级别实测可用。

## 怎么跑起来
已经部署在内网演示环境，不需要自己起。要本地跑的话 `docker compose up`，依赖一个 PostgreSQL。

## 演示入口
内网打开演示地址，用只读账号登录，先看「任务列表」再点进任一批次看对账差异。

## 已知限制
演示库里是脱敏后的假数据；导出功能关掉了。
"""

APP_LABELER_README = """# 标注小工具

## 结论
给数据标注同事用的快捷标注工具，自己业余写的，键盘流操作比现有工具快一倍。

## 怎么跑起来
仓库根目录有 Dockerfile，`docker build` 后跑起来监听 7001，没有外部依赖。

## 演示入口
目前只在我本机跑，想看的话找我开一下；镜像传上来之后可以在演示环境点启动。

## 已知限制
没有权限控制，标注结果存在容器里，重启就没了。
"""


CUSTOMER_CASE = """# 华安核心接口限流

## 结论
这家客户的网关默认限流 50 QPS，批量任务必须自己限速，否则会被整体熔断。

## 现象
导入跑到一半整条链路 503。

## 根因
客户网关对单来源 IP 限流，批量任务没有退避。

## 解决方法
客户端限速到 30 QPS 并加指数退避重试。
"""


def customer_files() -> dict[str, bytes]:
    """客户级资产：同一个客户的多个项目都用得上，而且这些项目常常跨部门。"""
    files: dict[str, str] = {}
    files["knowledge/cases/huaan-gateway-throttling/README.md"] = CUSTOMER_CASE
    files["knowledge/cases/huaan-gateway-throttling/asset.yaml"] = _yaml(
        "Case",
        "huaan-gateway-throttling",
        "华安核心接口限流",
        "客户网关默认限流 50 QPS，批量任务要自己限速并退避",
        "department:finance",
        industry=["insurance"],
        suitable="对接这家客户任何需要批量调接口的项目",
        not_suitable="非本客户的项目",
        tags=["限流", "网关"],
        extra="  caseType: fault\n  severity: S2",
        source={"customerCode": "HUAAN", "engagementSlug": "policy-import"},
    )
    return {name: text.encode("utf-8") for name, text in files.items()}


def application_files() -> dict[str, bytes]:
    """两个应用资产：一个内网已部署（A 档登记），一个本地容器化（B 档）。"""
    files: dict[str, str] = {}
    files["applications/policy-import-console/README.md"] = APP_CONSOLE_README
    files["applications/policy-import-console/asset.yaml"] = _yaml(
        "Application",
        "policy-import-console",
        "保单导入控制台",
        "盯批量导入进度与对账差异的运营面板",
        "department:data-intel",
        industry=["insurance"],
        suitable="需要人工盯批量任务的交付",
        not_suitable="纯后台无人值守的任务",
        tags=["导入", "对账"],
        source={"origin": "engagement", "engagementSlug": "policy-import"},
        extra="""  sourceType: fcp
  maturity: pilot
  repo: https://gitea.internal/fde-apps/policy-import-console
  runtime:
    type: compose
    entry: docker-compose.yml
    main_service: web
    ports: [8080]
    healthcheck: /healthz
    env: [DB_URL]
  demo:
    network: intranet
    url: http://demo.fde.internal/policy-import-console
    account: demo / demo123（只读）
    reachable_from: 公司内网或办公 WiFi
    note: 每晚 2 点重置数据""",
    )

    files["applications/quick-labeler/README.md"] = APP_LABELER_README
    files["applications/quick-labeler/asset.yaml"] = _yaml(
        "Application",
        "quick-labeler",
        "标注小工具",
        "键盘流的数据标注工具，业余项目，比现有工具快一倍",
        "user:chen",
        industry=[],
        suitable="小批量人工标注",
        not_suitable="需要多人协作或审计留痕的标注",
        tags=["标注", "效率"],
        lifecycle="experimental",
        extra="""  sourceType: external
  maturity: poc
  repo: https://github.com/example/quick-labeler
  runtime:
    type: container
    entry: Dockerfile
    ports: [7001]
    healthcheck: /
    env: []
  demo:
    network: local
    url: ""
    account: ""
    reachable_from: 作者本机，镜像上传后可在演示环境启动
    note: 标注结果存容器里，重启即丢""",
    )
    return {name: text.encode("utf-8") for name, text in files.items()}


def department_files() -> dict[str, bytes]:
    files: dict[str, str] = {}
    files["skills/pg-vacuum-tuning/SKILL.md"] = """---
name: pg-vacuum-tuning
description: 数据智能部内部使用的 PostgreSQL vacuum 调优经验，适合大表批量写入后的维护。
---

# PostgreSQL vacuum 调优

## 适用场景
大表批量导入后膨胀明显、查询变慢。

## 步骤
1. 先看 pg_stat_user_tables 的 dead tuple 比例。
2. 调整 autovacuum_vacuum_scale_factor 到 0.02。
3. 大表单独设置表级参数，不要全局改。

## 注意事项
不要在业务高峰执行 VACUUM FULL。
"""
    files["skills/pg-vacuum-tuning/asset.yaml"] = _yaml(
        "Skill",
        "pg-vacuum-tuning",
        "PostgreSQL vacuum 调优",
        "先看 dead tuple 比例，再按表调参，避免全局修改",
        "department:data-intel",
        industry=["general"],
        suitable="批量导入后的表膨胀治理",
        not_suitable="OLTP 高峰期在线执行 VACUUM FULL",
        tags=["postgresql"],
    )
    files["knowledge/cases/dept-quota-mistake/README.md"] = """# 部门内部：配额申请填错导致环境延期

## 结论
配额申请必须按峰值申请，按均值申请会在压测时被限流。

## 现象
压测第二天开始大量请求被限流。

## 根因
申请配额时按日均值填写。

## 解决方法
临时提额并重新压测。

## 如何避免
配额申请模板里增加峰值字段。
"""
    files["knowledge/cases/dept-quota-mistake/asset.yaml"] = _yaml(
        "Case",
        "dept-quota-mistake",
        "配额申请填错导致环境延期",
        "配额按峰值申请，按均值申请会在压测时被限流",
        "department:data-intel",
        industry=["general"],
        suitable="需要申请云资源配额的项目",
        not_suitable="自建机房项目",
        extra="  caseType: pitfall",
        nature="judgment",
    )
    return {path: content.encode("utf-8") for path, content in files.items()}


def engagement_files() -> dict[str, bytes]:
    files: dict[str, str] = {}
    files[".agents/skills/policy-date-parse/SKILL.md"] = """---
name: policy-date-parse
description: 本项目专用的保单日期解析实现，覆盖客户三种历史格式。
---

# 保单日期解析（项目级）

## 适用场景
本项目的保单流水文件，日期字段混用三种格式。

## 步骤
1. 依次尝试 yyyyMMdd、yyyy-MM-dd、dd/MM/yyyy。
2. 解析失败写入错误表并保留行号。

## 注意事项
不要用本地时区解析，统一按 UTC 存储。
"""
    files[".agents/skills/policy-date-parse/asset.yaml"] = _yaml(
        "Skill",
        "policy-date-parse",
        "保单日期解析（项目级）",
        "三种历史日期格式依次尝试，失败行可回溯",
        "user:chen",
        industry=["insurance"],
        suitable="本项目保单流水文件",
        not_suitable="其他客户的文件格式",
        source={"engagementSlug": "policy-import", "customerCode": "CUST-A"},
    )
    files[".fde/assets/cases/import-over-10k-rows-timeout/README.md"] = """# 导入超过 1 万行报错

## 结论
批量提交尺寸过大导致事务超时，按 2000 行分批提交即可。

## 现象
导入 12000 行时报 statement timeout。

## 影响
客户验收当天阻塞了两个小时。

## 根因
一次性提交整个文件，单事务超过语句超时时间。

## 解决方法
改为每 2000 行提交一次，并在失败时重试当前批次。

## 如何避免
导入脚本默认分批，批次大小做成参数。

## 判断要点
报错信息指向超时，容易被误判为数据库性能问题，实际是提交粒度问题。
"""
    files[".fde/assets/cases/import-over-10k-rows-timeout/asset.yaml"] = _yaml(
        "Case",
        "import-over-10k-rows-timeout",
        "导入超过 1 万行报错",
        "单事务过大导致超时，按 2000 行分批提交",
        "user:chen",
        industry=["insurance"],
        suitable="批量导入类任务",
        not_suitable="流式接口",
        extra="  caseType: fault\n  severity: S2",
        nature="judgment",
        source={"engagementSlug": "policy-import", "customerCode": "CUST-A", "workItemId": '"15"'},
    )
    return {path: content.encode("utf-8") for path, content in files.items()}


DIRECTORY: dict[str, Any] = {
    "users": {
        "chen": {
            "display_name": "小陈",
            "department_code": "data-intel",
            "memberships": [
                {
                    "engagement_slug": "policy-import",
                    "department_code": "finance",
                    "customer_code": "HUAAN",
                    "role": "member",
                }
            ],
        },
        "wang": {
            "display_name": "老王",
            "department_code": "finance",
            # 两个项目都在 finance 部门下，由他兼部门主管，才演示得了「部门视图 → 推送给负责人」
            "is_department_head": True,
            "memberships": [
                {
                    "engagement_slug": "policy-import",
                    "department_code": "finance",
                    "customer_code": "HUAAN",
                    "role": "owner",
                }
            ],
        },
        "li": {
            "display_name": "小李",
            "department_code": "data-intel",
            "is_asset_reviewer": True,
            "memberships": [],
        },
        "zhao": {"display_name": "小赵", "department_code": "market", "memberships": []},
        # finance 部门里不归老王负责的项目要有个负责人，才演示得了「部门主管推送给同事」
        "sun": {
            "display_name": "小孙",
            "department_code": "finance",
            "memberships": [
                {
                    "engagement_slug": "claims-recon",
                    "department_code": "finance",
                    "customer_code": "",
                    "role": "owner",
                }
            ],
        },
        "admin": {
            "display_name": "管理员",
            "department_code": "platform",
            "is_admin": True,
            "is_asset_reviewer": True,
            "memberships": [],
        },
    },
    # 推荐要按项目与 Agent 的上下文匹配；正式环境这两份清单由 fde-server 提供
    "engagements": {
        "policy-import": {
            "title": "华安人寿保单批量导入",
            "department_code": "finance",
            "owner": "wang",
            "industry": "insurance",
            "stage": "开发与联调",
            "description": "把 500 万行历史保单从 Oracle 迁到 PostgreSQL，要求对账零差异",
        },
        "core-migration": {
            "title": "城商行核心系统迁移",
            "department_code": "finance",
            "owner": "wang",
            "industry": "banking",
            "stage": "调研与方案确认",
            "description": "核心系统从主机下移，双跑比对后割接",
        },
        "claims-recon": {
            "title": "理赔对账自动化",
            "department_code": "finance",
            "owner": "sun",
            "industry": "insurance",
            "stage": "调研与方案确认",
            "description": "每日把理赔系统与财务系统的流水自动对账，差异超过阈值就告警",
        },
        "ops-dashboard": {
            "title": "内部运维看板",
            "department_code": "data-intel",
            "owner": "chen",
            "industry": "",
            "stage": "开发与联调",
            "description": "给运维同事看的任务与告警面板",
        },
    },
    "agents": {
        "import-coder": {
            "title": "导入编码助手",
            "department_code": "data-intel",
            "owner": "chen",
            "role": "coding_agent",
            "description": "负责保单导入相关的编码任务，常见活是改批次大小、处理日期格式、排查超时",
            "skills": ["policy-date-parse"],
        },
        "migration-reviewer": {
            "title": "迁移评审助手",
            "department_code": "finance",
            "owner": "wang",
            "role": "review_agent",
            "description": "评审数据迁移方案与割接计划，重点看回滚预案和双跑比对",
            "skills": [],
        },
    },
}

COMPLIANCE: dict[str, Any] = {
    "customer_names": ["测试保险公司", "示范银行"],
    "sensitive_terms": ["内部代号X"],
}

ACTIVITY: dict[str, Any] = {
    "work_items": [
        {
            "work_item_id": "12",
            "title": "保单流水导入支持三种日期格式",
            "engagement_slug": "policy-import",
            "owner_user": "chen",
            "status": "done",
            "delivered_pr": True,
            "changed_lines": 180,
            "closed_at": "2026-09-24T10:00:00Z",
            "produced_asset": True,
        },
        {
            "work_item_id": "15",
            "title": "导入超过 1 万行报错",
            "engagement_slug": "policy-import",
            "owner_user": "chen",
            "kind": "issue",
            "severity": "S2",
            "status": "closed",
            "closed_at": "2026-09-27T09:00:00Z",
            "produced_asset": False,
        },
        {
            "work_item_id": "18",
            "title": "保单导入字段映射调整",
            "engagement_slug": "policy-import",
            "owner_user": "chen",
            "status": "done",
            "closed_at": "2026-09-26T09:00:00Z",
        },
        {
            "work_item_id": "19",
            "title": "保单导入容错处理",
            "engagement_slug": "policy-import",
            "owner_user": "chen",
            "status": "done",
            "closed_at": "2026-09-28T09:00:00Z",
        },
        {
            "work_item_id": "21",
            "title": "对账脚本重构",
            "engagement_slug": "policy-import",
            "owner_user": "chen",
            "status": "done",
            "delivered_pr": True,
            "changed_lines": 260,
            "prompt_appends": 5,
            "review_changes_requested": 2,
            "error_signature": "ORA-01722",
            "delivery_summary": "注意先备份，否则回滚会丢数据",
            "closed_at": "2026-09-29T09:00:00Z",
        },
        {
            "work_item_id": "22",
            "title": "对账脚本二期",
            "engagement_slug": "policy-import",
            "owner_user": "chen",
            "status": "done",
            "error_signature": "ORA-01722",
            "closed_at": "2026-09-29T15:00:00Z",
        },
    ],
    "engagements": [
        {
            "engagement_slug": "policy-import",
            "owner_user": "wang",
            "accepted": True,
            "accepted_at": "2026-09-29T18:00:00Z",
        },
    ],
}


def merge_missing(existing: Any, fresh: Any, path: str = "") -> tuple[Any, list[str]]:
    """把 `fresh` 里有、`existing` 里没有的部分补进去，已有的内容一律不动。返回（结果, 补了哪些）。

    字典逐层补缺的键；成员关系这类列表按 `engagement_slug` 对上号，只给对上的那一项补缺的字段，
    不增不删——名单里少一个人、少一个项目可能是有意改的，多出来的更不能动。
    """
    if isinstance(existing, dict) and isinstance(fresh, dict):
        merged = dict(existing)
        added: list[str] = []
        for key, value in fresh.items():
            here = f"{path}.{key}" if path else str(key)
            if key not in existing:
                merged[key] = value
                added.append(here)
            else:
                merged[key], more = merge_missing(existing[key], value, here)
                added.extend(more)
        return merged, added
    if isinstance(existing, list) and isinstance(fresh, list):
        by_slug = {
            item["engagement_slug"]: item
            for item in fresh
            if isinstance(item, dict) and "engagement_slug" in item
        }
        merged_list = []
        added = []
        for item in existing:
            match = by_slug.get(item.get("engagement_slug")) if isinstance(item, dict) else None
            if match is None:
                merged_list.append(item)
                continue
            patched, more = merge_missing(item, match, f"{path}[{item['engagement_slug']}]")
            merged_list.append(patched)
            added.extend(more)
        return merged_list, added
    return existing, []


def refresh_directory(settings: AssetSettings) -> list[str]:
    """沿用旧数据目录启动时，把名单文件里后来才有的部分补上。返回补了哪些。

    代码往名单里加东西（项目和 Agent 清单、成员关系里的客户代号、新角色）之后，
    旧数据目录里的 directory.json 不会自己长出来，页面上就表现为「找不到项目」。
    """
    target = settings.root / "directory.json"
    if not target.exists():
        return []
    existing = json.loads(target.read_text(encoding="utf-8"))
    merged, added = merge_missing(existing, DIRECTORY)
    if added:
        target.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return added


def seed_repos() -> list[tuple[str, RepoRef, dict[str, bytes], str]]:
    """种子数据有哪几个仓库、各放什么：（结果里的键, 仓库, 文件, 提交说明）。"""
    return [
        (
            "company",
            RepoRef(name="company-assets", scope="company"),
            company_files(),
            "chore(assets): 公司级种子资产",
        ),
        (
            "department",
            RepoRef(
                name="dept-data-intel-assets", scope="department", department_code="data-intel"
            ),
            {**department_files(), **application_files()},
            "chore(assets): 部门级种子资产（含应用）",
        ),
        (
            "customer",
            RepoRef(name="cust-HUAAN-assets", scope="customer", customer_code="HUAAN"),
            customer_files(),
            "chore(assets): 客户级种子资产",
        ),
        (
            "engagement",
            RepoRef(name="policy-import", scope="engagement", engagement_slug="policy-import"),
            engagement_files(),
            "chore(assets): 项目级种子资产",
        ),
    ]


def refresh_seed_assets(settings: AssetSettings) -> list[str]:
    """沿用旧数据目录启动时，补上后来才加进种子数据的资产。返回补了哪些。

    和 `refresh_directory` 是一回事：代码往种子里加了新资产（应用、客户级仓库），
    旧数据目录里的仓库不会自己长出来，页面上就表现为「应用市场是空的」。
    只补**整份缺失**的资产（它的目录在仓库里完全不存在），已有的文件一个字不动。
    """
    git = LocalGitRepo(settings.repos)
    added: list[str] = []
    for _key, repo, files, _message in seed_repos():
        if not git.path_of(repo).exists():
            git.commit_files(repo, "main", files, "chore(assets): 补上后来才有的种子仓库")
            added.append(f"{repo.name}（整个仓库）")
            continue
        existing = {entry.path for entry in git.list_tree(repo)}
        asset_dirs = sorted(
            {path.rsplit("/", 1)[0] for path in files if path.endswith("/asset.yaml")}
        )
        missing: dict[str, bytes] = {}
        for directory in asset_dirs:
            if any(path == directory or path.startswith(directory + "/") for path in existing):
                continue
            for path, content in files.items():
                if path.startswith(directory + "/"):
                    missing[path] = content
            added.append(f"{repo.name}:{directory}")
        if missing:
            git.commit_files(repo, "main", missing, "chore(assets): 补上后来才有的种子资产")
    return added


def seed(settings: AssetSettings) -> dict[str, Any]:
    settings.ensure_dirs()
    git = LocalGitRepo(settings.repos)
    result: dict[str, Any] = {}
    for key, repo, files, message in seed_repos():
        result[key] = git.commit_files(repo, "main", files, message)

    (settings.root / "directory.json").write_text(
        json.dumps(DIRECTORY, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (settings.root / "compliance.json").write_text(
        json.dumps(COMPLIANCE, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (settings.root / "activity.json").write_text(
        json.dumps(ACTIVITY, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> None:
    from fde_asset.settings import load_settings

    settings = load_settings()
    result = seed(settings)
    print("种子资产已写入：")
    for key, sha in result.items():
        print(f"  {key}: {sha[:10]}")
    print(f"  目录文件：{settings.root}/directory.json、compliance.json、activity.json")


if __name__ == "__main__":
    main()
