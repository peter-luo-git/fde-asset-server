"""七种资产类型的空白模板：新建沉淀与校验共用同一套章节定义。"""

from __future__ import annotations

from fde_asset.modules.asset.manifest import KIND_RULES

_COMMON_YAML = """apiVersion: fde.asset/v1
kind: {kind}
metadata:
  name: {name}
  title: {title}
  summary: ""            # 一句话结论：别人只看这一行就能判断要不要点开
  tags: []
spec:
  owner: {owner}
  lifecycle: experimental
  industry: []
  applicability:
    suitable: ""         # 什么情况下适用
    notSuitable: ""      # 什么情况下不适用
  version: 0.1.0
{extra}"""

_EXTRA = {
    # 新建默认是 L1（通用流程，不继承谁）；要写行业或项目流程，在草稿页选层级和上层流程
    "Sop": "  layer: L1\n",
    "Case": "  caseType: fault      # fault|faq|pitfall|rejection|counterexample|edge\n  severity: S3\n",
    "Application": """  sourceType: fcp          # fcp（平台内开发）| external（外部自研）
  maturity: poc            # poc | pilot | production
  repo: ""
  runtime:
    type: url              # url | static | container | compose
    entry: ""              # Dockerfile 或 docker-compose.yml 的路径
    ports: []
    healthcheck: ""
    env: []                # 只写变量名，值由演示环境注入，不进资产库
  demo:
    network: intranet      # internet（公网）| intranet（内网）| vpn | local（仅本机）
    url: ""
    account: ""            # 演示账号说明
    reachable_from: ""     # 一句话说清在哪能打开
    note: ""
""",
}

_BODY = {
    "Rule": """# {title}

## 结论
<一句话说明这条规范要求什么>

## 适用范围
<适用于哪些项目、哪些场景>

## 要求
1. <必须做什么>
2. <禁止做什么>

## 例外
<什么情况下可以豁免，谁批准>
""",
    "Sop": """# {title}

## 结论与目的
<这条流程解决什么问题，按它做完能得到什么>

## 适用范围
<行业、客户类型、项目规模；不适用的情况写进 asset.yaml 的 applicability>

## 角色
<谁负责哪几步，谁来确认>

## 前置条件
<开始之前必须具备什么>

## 步骤
1. <第一步标题>
   <这一步做什么、怎么做；细的操作方法写成技能，这里只写阶段要求>
   - 检查点[交付物]：`docs/<文件名>.md` 已提交，含「<必需章节>」一节
   - 检查点[确认:客户]：<需要谁确认什么>
   - 产出：<这一步产生什么>
   - 技能：<这一步要用的技能名，没有就删掉这一行>
2. <第二步标题>
   <操作说明>
   - 检查点[命令]：<能用命令验证的要求> `<命令，如 pytest -q>`
   - 检查点[判断]：<需要人凭经验判断的，写清看什么>
   - 产出：<这一步产生什么>

## 异常处理
<哪一步的检查点不通过怎么办>

## 变更记录
- {date} 初版
""",
    "Skill": """---
name: {name}
description: <一句话说明这个技能在什么时候用、能做什么>
---

# {title}

## 适用场景
<什么任务下该加载这个技能>

## 步骤
1. <怎么做>

## 注意事项
<容易出错的地方>

## 示例
<最小可用示例>
""",
    "Solution": """# {title}

## 结论
<给什么客户、解决什么问题、用什么打法>

## 客户类型与场景
<行业、规模、典型诉求>

## 方案概述
<架构与关键路径；图放 attachments/>

## 关键能力
<方案里最值钱的几点>

## 交付物清单
<最终交给客户什么>

## 适用边界
<什么情况下不要用这个方案>
""",
    "Implementation": """# {title}

## 结论
<这份手册覆盖什么环境、按它做能得到什么结果>

## 环境与前置条件
<版本、资源、权限>

## 实施步骤
1. <步骤>

## 配置基线
<关键参数与推荐值>

## 验证方法
<怎么确认做对了>

## 割接方案
<切换窗口与顺序>

## 回滚预案
<失败了怎么退回；公司级资产必填>

## 常见问题
<链到 [[case/...]]>
""",
    "Case": """# {title}

## 结论
<一句话：根因是什么、应该怎么处理>

## 现象
<看到了什么，报错原文放这里>

## 影响
<影响范围与严重程度>

## 根因
<为什么会这样>

## 解决方法
<当时怎么解决的>

## 如何避免
<下次怎么不再踩>

## 判断要点
<为什么"看着对其实不对"，这一段是最值钱的部分>

## 相关资产
- [[rule/...]]
""",
    "Experience": """# {title}

## 结论
<最重要的三条>

## 项目概况
<只写客户代号、行业、周期、规模>

## 做对了什么

## 踩了什么坑

## 如果重来

## 可沉淀清单
- [ ] <可以拆成 SOP 的部分>
- [ ] <可以拆成 Case 的部分>
""",
    "Application": """# {title}

## 结论
<这个应用解决什么问题，给谁用>

## 怎么跑起来
<依赖、启动命令、最小环境；本地开发的应用写清楚 Dockerfile 或 compose 在哪>

## 演示入口
<在哪看、用什么账号、先看哪几个页面最能说明问题>

## 已知限制
<什么没做、哪些数据是假的、别人试的时候要注意什么>
""",
}


def render_template(
    kind: str, *, name: str, title: str, owner: str, date: str = ""
) -> dict[str, str]:
    """返回 {文件相对路径: 内容}：主文件加 asset.yaml。"""
    if kind not in KIND_RULES:
        raise ValueError(f"不支持的 kind：{kind}")
    body = _BODY[kind].format(title=title, name=name, date=date or "")
    main_file = KIND_RULES[kind].main_file
    manifest = _COMMON_YAML.format(
        kind=kind, name=name, title=title, owner=owner, extra=_EXTRA.get(kind, "")
    )
    return {main_file: body, "asset.yaml": manifest}


#: 写 L2、L3 时的步骤模板：只写要覆盖或新增的步骤，上层的不用重抄
SOP_LAYERED_STEPS = """## 步骤
<!-- 只写要覆盖或新增的步骤。编号和上层相同 = 整步替换上层那一步（连同它的检查点）；新编号 = 新增一步。上层其余步骤自动沿用，不用重抄。 -->
2. <要替换上层第 2 步时，写新的标题>
   <和上层相比多了什么要求>
   - 检查点[交付物]：`docs/<文件名>.md` 已提交，含「<必需章节>」一节
   - 检查点[确认:<谁>]：<需要谁确认什么>
   - 产出：<这一步产生什么>
5. <新增的步骤标题>
   <操作说明>
   - 检查点[命令]：<能用命令验证的要求> `<命令>`
   - 产出：<这一步产生什么>
"""


def sop_template(layer: str, *, title: str = "", date: str = "") -> str:
    """L1 用完整模板；L2、L3 的「步骤」换成只写覆盖和新增的写法。"""
    body = _BODY["Sop"].format(title=title or "<流程名称>", name="", date=date or "")
    if layer not in {"L2", "L3"}:
        return body
    head, _, rest = body.partition("## 步骤\n")
    _, _, tail = rest.partition("\n## 异常处理")
    return head + SOP_LAYERED_STEPS + "\n## 异常处理" + tail
