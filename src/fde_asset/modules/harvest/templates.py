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
    "Sop": '  layer: L2\n  extends: ""        # L2 指向 L1，L3 指向 L2\n',
    "Case": "  caseType: fault      # fault|faq|pitfall|rejection|counterexample|edge\n  severity: S3\n",
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
<这条流程解决什么问题，跑完能得到什么>

## 适用范围
<行业、客户类型、项目规模；不适用的情况写进 asset.yaml 的 applicability>

## 角色
<谁负责哪几步>

## 前置条件
<开始之前必须具备什么>

## 步骤
1. <第一步标题>
   <操作说明>
   - 检查点：<可验证的判定条件>
   - 产出：<这一步产生什么>
2. <第二步标题>
   - 检查点：

## 异常处理
<哪一步失败了怎么办>

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
}


def render_template(
    kind: str, *, name: str, title: str, owner: str, date: str = ""
) -> dict[str, str]:
    """返回 {文件相对路径: 内容}。Rule 只有一个 Markdown，不带 asset.yaml。"""
    if kind not in KIND_RULES:
        raise ValueError(f"不支持的 kind：{kind}")
    body = _BODY[kind].format(title=title, name=name, date=date or "")
    if kind == "Rule":
        return {f"{name}.md": body}
    main_file = KIND_RULES[kind].main_file
    manifest = _COMMON_YAML.format(
        kind=kind, name=name, title=title, owner=owner, extra=_EXTRA.get(kind, "")
    )
    return {main_file: body, "asset.yaml": manifest}
