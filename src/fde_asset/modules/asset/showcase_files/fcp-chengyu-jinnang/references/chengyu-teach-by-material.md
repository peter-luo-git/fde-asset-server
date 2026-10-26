---
name: chengyu-teach-by-material
description: Use when choosing the FORMAT of an output, not just its verbosity — match the medium (text, markdown, HTML dashboard, PPTX, DOCX) to the consumer's actual use mode. Operator-specific extension: also covers length calibration for different contexts. Outside scope is video (requires autovideo pipeline — too heavy to be native capability). The chengyu is about teaching with the right material, where "material" includes the format/medium, not just the depth.
---

# 因材施教 — teach by the material

**因材施教** (yīn cái shī jiào) — "according to the material, apply teaching". Match the medium AND the depth of an output to the actual consumer and use-mode. The same content delivered in the wrong format degrades just as much as the same content delivered at the wrong depth.

## Operator extension: format is a calibration dimension

Default agent behaviour is "always emit text into the chat". The operator's refinement: **format itself is one of the variables**, not just verbosity. Different consumers parse different formats; defaulting to text is one failure mode among many.

| Format | Best for | Cost |
|---|---|---|
| Inline chat text | quick answer, single-shot question, operator-in-flow | low |
| Markdown file | doc that gets grep'd, README, internal note | low-mid |
| HTML rich-render dashboard (`~/rich-renders/`) | multi-item synthesis, side-by-side comparison, decision-with-options | mid |
| PPTX | talk, walkthrough, pitch, time-bounded presentation to audience | mid-high |
| DOCX | formal deliverable, contract, signed document, exportable artifact | high |
| Video | **outside native scope** — requires autovideo pipeline; too heavy to be default | very high |

## When this applies

Symptoms that this schema applies:

- **Operator asks for "explanation"** — calibrate: how technical are they? how much time do they have? prose or slides? skim or reference?
- **Multiple items to compare (≥5)** — HTML dashboard (table, side-by-side) almost always beats text list. The stop-hook in this homelab specifically calls this out as a default.
- **Walkthrough of a decision with options** — slides beat prose for the same content because slides force you to one-idea-per-frame.
- **Formal deliverable** — DOCX beats markdown for headers, page numbers, signatures, export to external parties.
- **Internal note that needs to be searchable** — markdown beats DOCX; markdown beats inline chat text.
- **Quick answer** — inline text beats anything else. Don't over-format.
- **Cross-session reference** — file (markdown or HTML) beats chat (chat scrolls away).

## Behavior

1. **Identify the consumer.** Who reads this? Operator only? Operator's team? External? Machine consumer?
2. **Identify the use mode.** Skim once? Referenced repeatedly? Presented to live audience? Legal/formal artifact? Compounded with prior work?
3. **Pick the format that matches consumer × use-mode.** Use the rough matrix above; deviate when context demands.
4. **Calibrate verbosity within the chosen format.** Format selection is the outer loop; depth-calibration is the inner loop.
5. **If unsure between two formats, ASK** the operator — format choice has real cost (PPTX takes minutes; inline chat text takes seconds). Don't burn the cost speculatively.

## Cross-reference

This skill pairs with the stop-hook heuristic: "If this turn produced a multi-item synthesis (≥5 items, e.g. triage/audit/survey/comparison), emit rich HTML dashboard." That heuristic is `chengyu-teach-by-material` applied to a specific consumer-use-mode (operator scanning multi-item synthesis).

## Anti-pattern

**Defaulting to text for everything.** Text-by-default is a calibration failure. Multi-item comparisons especially benefit from HTML structure; presentations especially benefit from PPTX; formal docs benefit from DOCX. The chengyu's whole point is the format is a variable.

**Over-formalising.** Using DOCX when markdown would do, or PPTX when a paragraph would suffice. Formality fatigue is real — operator stops reading the polished deliverable for what would have been a 3-sentence answer.

**Over-prettifying.** Building HTML dashboards for single-paragraph answers. The dashboard has cost (to author, to render, to navigate); use it when the structural value justifies the cost.

**Pretending video is in scope.** The autovideo pipeline exists but is heavy enough that it's not a native capability. Don't promise a video render without explicitly checking with operator whether the pipeline run is justified.

**Confusing format-calibration with depth-calibration.** They're orthogonal axes. A PPTX can be terse; a chat reply can be deep. Pick the format and pick the depth independently.

## Etymology

From the *Analects* (論語) — Confucius is recorded as giving different answers to the same question depending on who asked. The canonical example:

> 子路問：「聞斯行諸？」 — Zilu asked: "When I hear something, should I act on it?"
> 子曰：「有父兄在，如之何其聞斯行之！」 — Confucius said: "Your father and elder brother are still alive — how can you just act on what you hear?"
>
> 冉有問：「聞斯行諸？」 — Ranyou asked the same question.
> 子曰：「聞斯行之。」 — Confucius said: "When you hear it, act on it."
>
> 公西華曰：「赤也惑，敢問。」 — Gongxihua asked: "Same question, opposite answers — please explain."
> 子曰：「求也退，故進之；由也兼人，故退之。」 — Confucius said: "Ranyou is timid, so I push him forward; Zilu is bold for two, so I rein him in."

The teaching matches the student. Same question, different answer. Same content, different delivery.

Agentic translation extends past depth-calibration (verbosity, technical level) into format-calibration (medium itself): not just HOW MUCH you say, but in WHAT FORM. The operator's refinement is sharp: the format is part of the material being taught, and choosing the wrong format is choosing the wrong material.
