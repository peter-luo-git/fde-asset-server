---
name: chengyu-words-concise-meaning-complete
description: Use when an output is at risk of verbosity — the TLDR-outside / details-inside discipline. Distinguished from chengyu-great-wisdom-looks-foolish on the representation axis — 大智若愚 = resist performative complexity, 言簡意賅 = resist sprawl. Orthogonal failure modes; you can be performatively-clever AND brief, or non-clever AND verbose.
---

# 言簡意賅 — words concise, meaning complete

**言簡意賅** (yán jiǎn yì gāi) — "words few, meaning complete". The discipline of brevity-with-completeness: every sentence must be load-bearing; padding is the failure mode. The 90% case answerable from the first paragraph; the rest is reachable but not foregrounded.

This skill is sibling to chengyu-great-wisdom-looks-foolish; the failure modes are orthogonal:

| Skill | Failure mode | Example |
|---|---|---|
| 大智若愚 (great-wisdom-looks-foolish) | Performative CLEVERNESS — over-engineered solutions, rare vocabulary | Building a factory for what should be a function |
| 言簡意賅 (words-concise-meaning-complete) | Performative SPRAWL — over-verbose output, unnecessary padding | 3 paragraphs to say what a sentence would |

Both push toward unornamented output, but through different mechanisms. Invoke either or both.

## The TLDR-outside / details-inside pattern

The cleanest expression of this skill — broadly applicable but most explicit on GitHub:

> When a comment / PR description / issue body has any longwinded content (tables, code blocks, range-diffs, multi-point walkthroughs), it should open with a TL;DR and put the rest inside a `<details>` collapsible.
>
> Reviewers skim. A wall of text raises the cost of "what did this say?" — the TL;DR pays the bill in one line. The collapsible keeps the proof reachable for whoever wants to drill in, without cluttering the timeline.

This is the GitHub-comment-specific application. The general principle: **load-bearing content goes first, supporting content is reachable but not foregrounded**. Applies anywhere — agent reply, PR description, doc, design summary, post-mortem.

## When this applies

Symptoms that this skill applies:

- About to write a multi-paragraph explanation when one sentence answers the question
- About to use 3 hedges where 0 would do
- About to include "interesting context" that isn't load-bearing for the user's decision
- A test file is verbose with try-finally / setup-teardown / context-manager when none is needed
- A code comment is "too long" — comments accumulate sediment over revisions; a multi-line block usually has one load-bearing line and N lines of decoration
- A GitHub comment / PR description / issue body has tables, code blocks, range-diffs, or multi-point walkthroughs — the TL;DR-outside / details-inside pattern fires
- A filename / identifier is verbose when a short one would do

## Behavior

1. **Identify the load-bearing content** — the one or two sentences the user's next action depends on. That goes first.
2. **Compress everything else.** Reachable but not foregrounded. In writing: `<details>`. In code: separate function. In speech: parenthetical. In docs: appendix.
3. **Skip the ceremony.** No "I'd love to help with this!" preamble. No "I hope this helps!" closing. No paragraph of context-setting before the actual answer.
4. **Distinguish brevity from terseness.** Brevity = no padding. Terseness = no scaffolding. Brevity is the goal; terseness can sacrifice completeness.
5. **For GitHub comments specifically**: TL;DR sentence + `<details><summary>label</summary> ... </details>`. The summary should hint at the form, not the conclusion (`Per-point status table`, `What changed and proof`, `Range-diff output`, etc.).

## Anti-pattern

**Padding to look thorough.** Length as performance. Recall test: "what did this actually say?" If the answer is ≤ one sentence, the rest was padding.

**Brevity through omission of load-bearing content.** The chengyu is *brevity-with-completeness*. Cutting the load-bearing detail in pursuit of shortness is just incomplete, not concise.

**Terseness without scaffolding.** A one-liner that requires three Stack Overflow tabs to parse is not 言簡意賅 — it's compression without communication. The user should be able to act on the brief version without external lookup.

**Skipping TL;DR when it's required.** GitHub long comments without TL;DR cost reviewers. Reviewer bandwidth is finite; padding the comment shifts cost to them.

**Applying brevity uniformly.** Sometimes the user needs depth (a design spec, a post-mortem, a primer). Brevity is the *default*, not the *only* mode. The skill is invoked, not always-on.

## Relationship to other skills

- **chengyu-great-wisdom-looks-foolish** — orthogonal-but-paired representation discipline. 大智若愚 cuts cleverness; 言簡意賅 cuts sprawl. Often invoked together.
- **chengyu-teach-by-material** — format-calibration. Sometimes the right answer to verbosity is not just "shorter text" but "different format" (HTML dashboard for multi-item; PPTX for walk-through; markdown file for reference). 言簡意賅 inside the chosen format; 因材施教 picks the format.

## Etymology

A classical four-character chengyu of literary criticism. Used for centuries to describe writing that achieves complete meaning with minimal words — the ideal of every form from regulated verse to imperial memorial.

The chengyu's whole structure embodies its principle: four characters, no padding, complete meaning. 言 (words) + 簡 (few/concise) + 意 (meaning) + 賅 (complete/comprehensive). Removing any one breaks the principle; adding any would falsify it.
