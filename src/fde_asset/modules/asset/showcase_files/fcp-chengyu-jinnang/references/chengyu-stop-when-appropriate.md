---
name: chengyu-stop-when-appropriate
description: Use when at or near the 80% mark of a deliverable and the temptation is to keep polishing past good-enough. The 80/20 rule encapsulated — the last 20% of polish often consumes 80% of remaining time and rarely changes the operator's decision. Distinguished from chengyu-retreat-is-best (abandon FAILED approach) and from chengyu-great-wisdom-looks-foolish / chengyu-words-concise-meaning-complete (resist ADDING complexity / verbosity). 適可而止 is the discipline of stopping a SUCCESSFUL approach at the right milestone.
---

# 適可而止 — stop when appropriate

**適可而止** (shì kě ér zhǐ) — "stop when appropriate". The 80/20 rule encapsulated. The discipline of recognising when good-enough is actually right, and continuing past that point is sunk-cost polish that doesn't change the operator's decision.

## Where this fits among the "stop" disciplines

Three different stop-disciplines exist in the chengyu-skills set; they don't substitute for each other:

| Skill | When | Why stop |
|---|---|---|
| `chengyu-great-wisdom-looks-foolish` | About to ADD cleverness | The factory was never needed; a function fits |
| `chengyu-words-concise-meaning-complete` | About to ADD words | Padding doesn't add signal; the load-bearing sentence is already written |
| `chengyu-retreat-is-best` | Approach is FAILING | The path itself is wrong; restart |
| **`chengyu-stop-when-appropriate`** | Approach is SUCCEEDING, near completion | The remaining work is polish past good-enough; operator's decision is already supported |

## When this applies

Symptoms that this schema applies:

- The deliverable answers the operator's question. Remaining changes are aesthetic / structural / "nice-to-have".
- You're at the milestone the operator would have called "ship it" — and you're considering more refactoring before showing them.
- A draft / PR / doc covers all the load-bearing points; you're considering adding tangential sections that the operator didn't ask for.
- A test suite covers the failure modes that matter; you're considering edge cases that don't affect real behaviour.
- A bug fix works; you're considering refactoring surrounding code "while you're here".
- An exam-prep deck has the high-yield material; you're considering adding obscure trivia for completeness.
- A presentation hits 18 minutes for an 18-minute slot; you're polishing slide design instead of stopping.

The shared shape: **the operator's decision is already supported by what you have**. More work doesn't change the decision; it just delays it.

## Behavior

1. **Identify the operator's decision-criterion explicitly.** "What does the operator need this for? At what point does additional work stop changing their decision?"
2. **Compare current state to that criterion.** If current state already crosses the line, the remaining work is polish.
3. **If polish, STOP and ship.** Don't apologise for the simplicity / unfinished-feeling polish layer. Surface the gap honestly: "Shipping at 80%; the remaining 20% is [X, Y, Z], skipping unless requested."
4. **Let the operator pull more.** If they want the extra polish, they'll ask. If they don't, the 20% of additional work was never going to be paid for anyway.
5. **Distinguish polish from unfinished load-bearing work.** This skill applies when the work is COMPLETE-AT-80%; it doesn't apply when there are known bugs, missing features the operator explicitly asked for, or skipped tests for behaviour that matters.

## The 80/20 lens

The chengyu maps cleanly to the 80/20 rule (Pareto principle):

- **80% of value** is typically delivered in the **first 20% of work**
- The **next 60% of work** delivers the **next 15% of value**
- The **last 20% of work** delivers the **last 5% of value**

The "appropriate stopping point" is usually the inflection where value-per-unit-effort drops sharply. The discipline is recognising that inflection in real-time rather than ploughing through to 100% from inertia.

This is also the discipline behind the "ship when revert is easy" principle — stop polishing, ship the 80%, and use cheap rollback as the safety net for the 20% that turns out to matter.

## Anti-pattern

**Stopping when there's known load-bearing work left.** This skill is for stopping when the deliverable IS good enough; not for stopping when it's broken or incomplete. The honest 80% has to actually be 80%.

**Stopping without naming what's skipped.** Just stopping silently means the operator doesn't know what trade-off you made. Explicit gap-marking is required: "Shipping at 80%, skipping [X, Y, Z], reasons: low value-per-effort." (Pairs with `chengyu-know-what-you-dont-know`.)

**Treating "appropriate" as universal.** What's appropriate for a quick internal note isn't appropriate for a contract / paper / production deploy. The skill is invoked with the operator's decision-criterion in mind, not as a generic "stop at 80% always" rule.

**Polish as procrastination on the NEXT task.** Sometimes the 20% polish is a delay tactic to avoid starting the next thing. The chengyu fires precisely when you catch yourself doing this — the polish is comfort-work that's blocking the next real piece.

**Polishing the wrong axis.** The 80/20 lens picks out HIGH-LEVERAGE work in the first 20%. If you're polishing low-leverage details while load-bearing parts are still rough, you're 適可而止-violating in two directions at once.

## Etymology

A classical chengyu from the Confucian / pragmatic tradition. The literal: 適 (appropriate / suitable) + 可 (ability / possibility) + 而 (and then) + 止 (stop). "Stop at the appropriate point."

Used historically to advise restraint in pursuit — military victories that don't overreach into untenable territory, scholarly debates that don't degenerate into recrimination, social pleasures that don't tip into excess. The agentic translation drops the moral-restraint framing and keeps the engineering core: the appropriate stopping point exists; recognising it is the skill.

The chengyu is the operator-level discipline behind every 80/20 conversation in agile / lean / engineering culture, but predates them by ~2000 years.
