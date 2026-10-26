"""资产示范库的内容：FCP 平台预置的优质 SOP 和技能（2026-10-26）。

每份资产是一个目录：{相对路径: 文本}。asset.yaml 由 showcase.py 按下面的元数据生成。
SOP 的步骤用「技能：」点名配套的技能——任务按流程执行时，这一步点名的技能会自动带给 Agent。
"""

from __future__ import annotations

from typing import Any

SHOWCASE_TAG = "示范资产"
OWNER = "department:platform"

# ---------- SOP ----------

DOC_SOP = """# 技术文档编写流程（示范）

## 结论与目的
写一份别人能独立看懂、能照着做的技术文档：先定读者和范围，再写初稿，最后评审定稿。

## 适用范围
设计文档、方案说明、操作手册、接口说明等需要交给别人阅读的技术文档。

## 角色
文档作者负责编写，文档负责人确认范围和评审结论。

## 前置条件
项目仓库可以提交文件。

## 步骤
1. 明确读者与范围
   写清这份文档给谁看、解决什么问题、不包括什么，列出大纲。
   - 检查点[交付物]：`docs/文档大纲.md` 已提交，含「读者」「范围」「大纲」三节
   - 检查点[确认:文档负责人]：负责人确认读者和范围
   - 产出：文档大纲
   - 技能：fcp-tech-doc-writer
2. 写初稿
   按大纲写正文，结论先行，每节回答一个问题；术语第一次出现时解释。
   - 检查点[交付物]：`docs/技术文档.md` 已提交，含「背景」「方案」「风险」三节
   - 检查点[命令]：正文没有残留的待补充标记 `python3 -c "import pathlib,sys; t=pathlib.Path('docs/技术文档.md').read_text(encoding='utf-8'); sys.exit(1 if ('TODO' in t or '待补充' in t) else 0)"`
   - 产出：文档初稿
   - 技能：fcp-tech-doc-writer
3. 评审与定稿
   请读者按评审清单通读，处理全部意见后定稿。
   - 检查点[判断]：不看作者解释，读者能独立看懂并照着做
   - 检查点[确认:文档负责人]：评审意见都已处理
   - 产出：定稿文档

## 异常处理
范围不清时回到第 1 步，不要边写边改范围。

## 变更记录
- 2026-10-26 FCP 示范资产初版
"""

CODE_SOP = """# 代码改动交付流程（示范）

## 结论与目的
一次代码改动从需求到合并的最小闭环：先说清楚改什么，再实现并自测，最后按清单自查、走评审。

## 适用范围
功能开发、缺陷修复、重构等需要合并进主干的代码改动。

## 角色
开发者负责实现和自测，评审人负责代码评审。

## 前置条件
项目有代码仓库，能本地运行测试。

## 步骤
1. 理解需求
   写清楚这次改动的目标、改动范围和怎么验证，范围外的事不做。
   - 检查点[交付物]：`docs/改动说明.md` 已提交，含「目标」「改动范围」「验证方式」三节
   - 检查点[确认:负责人]：负责人确认改动范围
   - 产出：改动说明
2. 实现与自测
   小步提交，每一步都能运行；新增逻辑要有单元测试。
   - 检查点[命令]：单元测试全部通过 `python3 -m pytest -q`
   - 检查点[判断]：按评审清单自查，没有遗留的调试代码和待办标记
   - 产出：代码和测试
   - 技能：fcp-code-review-checklist
3. 评审与合并
   提交合并请求，写清改了什么、怎么验证；处理评审意见后合并。
   - 检查点[确认:评审人]：合并请求已通过评审
   - 产出：已合并的改动
   - 技能：fcp-code-review-checklist

## 异常处理
测试不过不提交评审；评审发现范围外的问题另开任务。

## 变更记录
- 2026-10-26 FCP 示范资产初版
"""

SLIDES_SOP = """# 汇报材料（PPT）制作流程（示范）

## 结论与目的
做一份结论先行、每页一个观点的汇报材料：先定受众和核心结论，再写页面，最后走查定稿。

## 适用范围
项目周报、方案汇报、复盘分享等需要对外讲解的材料。

## 角色
汇报人负责内容和定稿。

## 前置条件
知道汇报对象和时长。

## 步骤
1. 明确受众与核心结论
   一句话写清：讲给谁、希望他们记住什么、听完要做什么决定。
   - 检查点[交付物]：`slides/提纲.md` 已提交，含「受众」「核心结论」「页面提纲」三节
   - 检查点[确认:汇报人]：汇报人确认核心结论
   - 产出：汇报提纲
   - 技能：fcp-markdown-slides
2. 写页面草稿
   用 Markdown 写页面（每页用 `---` 分隔），每页标题就是这一页的结论。
   - 检查点[交付物]：`slides/汇报.md` 已提交
   - 检查点[命令]：页数不超过 15 页 `python3 -c "import pathlib,sys; t=pathlib.Path('slides/汇报.md').read_text(encoding='utf-8'); sys.exit(0 if t.count('\\n---') <= 15 else 1)"`
   - 产出：页面草稿
   - 技能：fcp-markdown-slides
3. 走查定稿
   从头到尾讲一遍，删掉和核心结论无关的页。
   - 检查点[判断]：每页一个观点、标题即结论、数据有出处
   - 检查点[确认:汇报人]：汇报人确认定稿
   - 产出：定稿材料

## 异常处理
时长不够时先砍细节页，不砍结论页。

## 变更记录
- 2026-10-26 FCP 示范资产初版
"""

# ---------- 技能 ----------

DOC_SKILL = """---
name: fcp-tech-doc-writer
description: 在写设计文档、方案说明、操作手册等技术文档时使用：按读者和范围定结构，结论先行，给出模板和自查清单
---

# 技术文档写作

## 适用场景
要写一份交给别人看的技术文档，或者要把零散的说明整理成文档。

## 步骤
1. 先问清三件事：读者是谁、读完要能做什么、不包括什么。写不清就先问用户，不要猜。
2. 按 `references/文档模板.md` 搭结构，删掉用不上的节，不要硬凑。
3. 每节开头一句话说结论，再展开；一段只讲一件事。
4. 术语第一次出现时解释；缩写写全称。
5. 有步骤的写成编号列表，每步一个动作，能照着做。
6. 写完按下面的清单自查，再交给用户。

## 自查清单
- 标题能看出这份文档讲什么
- 第一屏就能看到结论和适用范围
- 没有「TODO」「待补充」之类的残留
- 每个链接、命令、路径都真实存在
- 风险和不适用的情况写清楚了

## 注意事项
- 不要堆形容词（「非常」「完善」「强大」），用事实和数字说话。
- 不确定的地方明确写「待确认：……」，并告诉用户。
"""

DOC_TEMPLATE = """# <文档标题>

> 读者：<给谁看> · 状态：草稿 / 评审中 / 定稿 · 更新：<日期>

## 结论
<一两句话：这份文档的核心结论或建议>

## 背景
<为什么要做这件事，现状和问题>

## 范围
- 包括：<…>
- 不包括：<…>

## 方案
<怎么做，关键设计和取舍>

## 风险
| 风险 | 影响 | 应对 |
|---|---|---|
| <…> | <…> | <…> |

## 附录
<术语表、参考链接>
"""

REVIEW_SKILL = """---
name: fcp-code-review-checklist
description: 在提交代码前自查或评审别人的代码改动时使用：按清单逐项检查正确性、测试、可读性和安全，并用脚本找出遗留的调试代码和待办标记
---

# 代码评审清单

## 适用场景
提交合并请求之前自查，或评审别人的改动。

## 步骤
1. 先读改动说明，确认这次改动要解决什么，范围外的问题另记。
2. 运行 `python3 scripts/find_leftovers.py <要检查的目录>`，找出遗留的 TODO、FIXME、调试打印和断点。
3. 按下面的清单逐项看，每项给出「通过 / 有问题（附位置）」。
4. 汇总：先列必须改的，再列建议改的，每条写清文件和行号。

## 清单
- 正确性：边界条件、空值、并发、错误处理有没有覆盖
- 测试：新增逻辑有测试；测试名能看出测的是什么
- 可读性：命名直白；函数只做一件事；没有大段注释掉的代码
- 安全：没有硬编码密钥、口令；外部输入有校验
- 兼容：接口和数据格式的改动有没有影响调用方

## 注意事项
- 只评这次改动，不顺手重写不相关的代码。
- 拿不准的写成问题问作者，不要直接下结论。
"""

REVIEW_SCRIPT = '''#!/usr/bin/env python3
"""找出遗留的待办标记、调试打印和断点。用法：python3 find_leftovers.py [目录]"""

import pathlib
import re
import sys

PATTERNS = [
    ("待办标记", re.compile(r"\\b(TODO|FIXME|XXX)\\b")),
    ("调试打印", re.compile(r"^\\s*(print\\(|console\\.log\\()")),
    ("断点", re.compile(r"\\b(breakpoint\\(\\)|pdb\\.set_trace\\(\\)|debugger;)")),
]
SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rb", ".sh"}
SKIP = {".git", "node_modules", ".venv", "dist", "build", "__pycache__"}


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    found = 0
    for path in sorted(root.rglob("*")):
        if path.suffix not in SUFFIXES or any(part in SKIP for part in path.parts) or not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            for label, pattern in PATTERNS:
                if pattern.search(line):
                    print(f"{path}:{number}: {label}: {line.strip()[:120]}")
                    found += 1
    print(f"共 {found} 处" if found else "没有发现遗留的待办、调试打印和断点")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

SLIDES_SKILL = """---
name: fcp-markdown-slides
description: 在需要做汇报 PPT、分享材料或演示页面时使用：先写提纲再用 Markdown 写页面，每页标题即结论，可以用脚本把提纲转成页面骨架
---

# 用 Markdown 写汇报材料

## 适用场景
项目汇报、方案讲解、复盘分享，需要一份结构清楚的演示材料。

## 步骤
1. 先问清：受众是谁、讲多久、希望他们记住哪一句话。
2. 写提纲（`slides/提纲.md`）：一级标题是核心结论，二级标题是每一页的结论。
3. 运行 `python3 scripts/outline_to_slides.py slides/提纲.md slides/汇报.md`，把提纲转成页面骨架（每页用 `---` 分隔）。
4. 按 `templates/页面模板.md` 填每一页：标题是结论，正文最多 3 条要点，数据写出处。
5. 页数控制在 15 页以内；第一页给结论，最后一页给需要对方做的决定。
6. 生成的 Markdown 可以用 Marp、Slidev 等工具直接转成 PPT。

## 注意事项
- 一页只讲一个观点；放不下就拆页，不要缩小字号。
- 不要整段贴文字，讲的内容和页面上的字不要重复。
"""

SLIDES_TEMPLATE = """---
marp: true
paginate: true
---

# <核心结论：一句话>

<汇报人> · <日期>

---

## <这一页的结论>

- <要点 1>
- <要点 2>
- <要点 3>

> 数据来源：<…>

---

## 需要您决定

1. <决定事项 1>
2. <决定事项 2>
"""

SLIDES_SCRIPT = '''#!/usr/bin/env python3
"""把 Markdown 提纲转成页面骨架。用法：python3 outline_to_slides.py 提纲.md 输出.md

提纲里的一级标题作为封面，每个二级标题变成一页（标题即结论），下面的列表项作为这一页的要点。
"""

import pathlib
import sys


def convert(outline: str) -> str:
    pages, cover, current = [], "", None
    for line in outline.splitlines():
        text = line.strip()
        if text.startswith("# "):
            cover = text[2:].strip()
        elif text.startswith("## "):
            current = {"title": text[3:].strip(), "points": []}
            pages.append(current)
        elif text.startswith(("- ", "* ")) and current is not None:
            current["points"].append(text[2:].strip())
    slides = [f"# {cover or '<核心结论>'}"]
    for page in pages:
        body = "\\n".join(f"- {point}" for point in page["points"][:3]) or "- <要点>"
        slides.append(f"## {page['title']}\\n\\n{body}")
    header = "---\\nmarp: true\\npaginate: true\\n---\\n\\n"
    return header + "\\n\\n---\\n\\n".join(slides) + "\\n"


def main() -> int:
    if len(sys.argv) != 3:
        print("用法：python3 outline_to_slides.py 提纲.md 输出.md")
        return 2
    source, target = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(convert(source.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"已生成 {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

ENGLISH_SKILL = """---
name: fcp-english-sentence-coach
description: 在想练英语表达、把中文句子翻成地道英文、或请人批改英文句子时使用：先让用户自己造句，再指出错因并给出地道说法和同类练习
---

# 英语句型教练

## 适用场景
用户想练英语写作或口语表达，或者给出一句英文请你批改。

## 步骤
1. 用户给的是中文：先不要直接翻译，给一个提示句型，请用户自己写英文。
2. 用户写了英文：逐句批改，每处问题按「原句 → 改后 → 错因」列出，错因用 `references/常见错误.md` 里的类别。
3. 给出 1～2 种更地道的说法，并说明适用场合（正式 / 口语）。
4. 针对出错的句型，再出 3 道同类的中译英小练习，等用户作答后再批改。
5. 最后用一句话总结这次最需要注意的一点。

## 注意事项
- 用中文解释，例句用英文；一次只纠正最重要的 3 处，不要把用户压垮。
- 不要只给答案：先让用户想，再讲为什么。
"""

ENGLISH_REFERENCE = """# 常见错误类别

| 类别 | 例子 | 怎么改 |
|---|---|---|
| 中式直译 | open the light | turn on the light |
| 时态 | I go there yesterday. | I went there yesterday. |
| 主谓一致 | He have two cats. | He has two cats. |
| 冠词 | I am engineer. | I am an engineer. |
| 介词 | discuss about the plan | discuss the plan |
| 可数不可数 | many informations | a lot of information |
| 句子不完整 | Because it rains. | We stayed home because it rained. |
| 用词正式度 | wanna / gonna 用在邮件里 | want to / going to |
"""


def _skill(
    name: str, title: str, summary: str, suitable: str, not_suitable: str, files: dict[str, str]
) -> dict[str, Any]:
    return {
        "kind": "Skill",
        "name": name,
        "title": title,
        "summary": summary,
        "suitable": suitable,
        "not_suitable": not_suitable,
        "files": files,
    }


def _sop(
    name: str, title: str, summary: str, suitable: str, not_suitable: str, body: str, try_it: str
) -> dict[str, Any]:
    return {
        "kind": "Sop",
        "name": name,
        "title": title,
        "summary": summary,
        "suitable": suitable,
        "not_suitable": not_suitable,
        "files": {"SOP.md": body},
        "layer": "L1",
        "try_it": try_it,
    }


SHOWCASE: list[dict[str, Any]] = [
    _sop(
        "fcp-tech-doc-sop",
        "技术文档编写流程（示范）",
        "定读者和范围 → 写初稿 → 评审定稿，三步写出别人能独立看懂的技术文档",
        "设计文档、方案说明、操作手册、接口说明",
        "一两句话就能说清的临时说明",
        DOC_SOP,
        "新建任务「写一份内部运维看板的部署说明」，执行规范里选这条流程，交给 Agent 执行；"
        "看 Agent 是否按第 1 步只出大纲、回报检查点，并加载了 fcp-tech-doc-writer 技能",
    ),
    _sop(
        "fcp-code-change-sop",
        "代码改动交付流程（示范）",
        "说清改什么 → 实现并自测 → 按清单自查、走评审，代码改动的最小闭环",
        "功能开发、缺陷修复、重构",
        "只改文档、不涉及代码的任务",
        CODE_SOP,
        "新建任务「给运维看板加一个健康检查接口」，绑这条流程，交给 Agent 执行；"
        "第 2 步可以看 Agent 怎么用 fde_task_complete 跑测试、按清单自查",
    ),
    _sop(
        "fcp-presentation-sop",
        "汇报材料（PPT）制作流程（示范）",
        "定受众和核心结论 → 写页面 → 走查定稿，做一份结论先行的汇报材料",
        "项目周报、方案汇报、复盘分享",
        "需要精美视觉设计的对外宣传材料",
        SLIDES_SOP,
        "新建任务「做一份运维看板的项目周报」，绑这条流程；看 Agent 先出提纲、再用 fcp-markdown-slides 生成页面",
    ),
    _skill(
        "fcp-tech-doc-writer",
        "技术文档写作（示范）",
        "按读者和范围定结构、结论先行，附文档模板和自查清单",
        "设计文档、方案、手册等交给别人看的技术文档",
        "代码注释、提交说明这类很短的文字",
        {"SKILL.md": DOC_SKILL, "references/文档模板.md": DOC_TEMPLATE},
    ),
    _skill(
        "fcp-code-review-checklist",
        "代码评审清单（示范）",
        "提交前自查或评审改动：按清单逐项检查，并用脚本找出遗留的调试代码和待办",
        "合并请求之前的自查、评审别人的代码",
        "架构层面的设计评审",
        {"SKILL.md": REVIEW_SKILL, "scripts/find_leftovers.py": REVIEW_SCRIPT},
    ),
    _skill(
        "fcp-markdown-slides",
        "用 Markdown 写汇报 PPT（示范）",
        "先写提纲再写页面，每页标题即结论；脚本把提纲转成页面骨架，可转成 PPT",
        "项目汇报、方案讲解、复盘分享",
        "需要复杂动画和精美视觉的材料",
        {
            "SKILL.md": SLIDES_SKILL,
            "templates/页面模板.md": SLIDES_TEMPLATE,
            "scripts/outline_to_slides.py": SLIDES_SCRIPT,
        },
    ),
    _skill(
        "fcp-english-sentence-coach",
        "英语句型教练（示范）",
        "先让你自己造句，再指出错因、给地道说法和同类练习",
        "练英语写作和表达、批改英文句子",
        "整篇文档的专业翻译",
        {"SKILL.md": ENGLISH_SKILL, "references/常见错误.md": ENGLISH_REFERENCE},
    ),
]

TRY_SKILL = {
    "fcp-tech-doc-writer": "在私聊里说「帮我写一份内部运维看板的部署说明大纲」，看 Agent 是否加载这个技能、按模板出大纲",
    "fcp-code-review-checklist": "在私聊里说「帮我评审一下 xxx 这段代码」，看 Agent 是否按清单逐项检查",
    "fcp-markdown-slides": "在私聊里说「帮我做一份运维看板项目周报的 PPT 提纲」，看 Agent 是否先问受众、再按每页一个结论写",
    "fcp-english-sentence-coach": "在私聊里说「我想练英语：把『这个问题我们下周再讨论』翻成英文」，看 Agent 是否先让你自己写",
}
for _item in SHOWCASE:
    if _item["kind"] == "Skill":
        _item["try_it"] = TRY_SKILL[_item["name"]]
