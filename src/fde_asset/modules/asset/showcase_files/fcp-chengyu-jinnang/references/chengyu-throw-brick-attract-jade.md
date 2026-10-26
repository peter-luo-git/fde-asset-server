---
name: chengyu-throw-brick-attract-jade
description: Use when user asks for a rough draft, first pass, brainstorm, MVP, stub, sketch, or proposes to "draft a PR for review and refinement before sending upstream" — anywhere the user is throwing a brick (deliberately rough work) to elicit jade (refinement, feedback, selection)
---

# 抛磚引玉 — throw brick, attract jade

**抛磚引玉 / 拋磚引玉** (pāo zhuān yǐn yù) — literally "throw a brick to attract jade". Emit a deliberately rough first pass to elicit refinement.

## When user is throwing a brick

Real shapes that signal the user is throwing a brick:

- "Draft a PR raised to my fork for my review and refinement before sending upstream"
- "brainstorm API options. Later we will select which one to go with"
- "so what fix? brainstorm"
- "Brainstorm harder, search the web"
- "branch off the PR branch in your fork, where you can play without upstream noise concern, and propose a diff set once known works"
- "draft a reply to the issue retracting the wrapper idea"
- "post the bench findings as a comment on the issue, propose a reasonable budget"
- "Get to doing and throw the brick for me"

The fork-as-staging pattern (work in fork, refine, then upstream) is literally 抛磚引玉 as software engineering practice.

## Behavior

1. **Produce a quick sketch, not a polished artifact.** Resist the urge to over-engineer the first pass. Speed over completeness.
2. **Explicitly flag what's rough — to the operator, not into the artifact.** Name the gaps, assumptions, and known weaknesses where the operator reads them (chat, the PR/issue description that invites review) — never baked into the deliverable itself. See *Keep the metaphor off the artifact*.
3. **Invite specific refinements.** End with concrete questions or choice points — not a generic "let me know what you think".
4. **Don't apologize for roughness.** The roughness is the *feature*, not a bug. It's bait for the jade.

## Anti-pattern

Polishing the brick into a small jade. If the user asked for a draft, do not silently spend extra cycles to ship something publication-ready. That defeats the iteration loop and burns their review budget on details they would have changed anyway.

## Keep the metaphor off the artifact

The brick/jade framing is a private contract between you and the operator about *how rough* the work is. It is scaffolding, not content — it must never surface in the deliverable a third party reads: code, `--help` strings, comments, commit messages, log lines, flag/API names, or PR text aimed upstream. Those are written **on their own terms, as if finished**, however rough the work behind them is. Roughness is reported *to the operator* (in chat, or the PR description that invites review); it is never *stamped into* the thing a stranger runs.

This is where Behavior #2 goes wrong: "flag what's rough" gets read as "flag it *in the artifact*." A reviewer or end user is not your operator — they did not throw the brick and do not share the metaphor.

**Contamination test:** would this word make sense to someone who has never heard 拋磚引玉? If `brick`, `jade`, `rough`, `draft`, `sketch`, `WIP`, `stub`, or `throwaway` lands in a name/string/comment a stranger sees, it's a leak — rewrite it to describe the *thing*, not its draft status.

Worked leak: an opt-in 4 MB-flash build flag shipped with help text `BRICK: build for 4 MB flash modules`. "BRICK" was the author echoing 拋磚引玉 — harmless intent — but a user reads it as "this flag will brick my device," the exact opposite of what the flag does (it's the *fix* for 4 MB modules). The maintainer flagged it on review. The help string must describe what the flag does, on its own terms: `build for 4 MB flash modules; …`.

## Etymology (one line, for schema-activation)

The chengyu comes from the Tang dynasty story of poet Chang Jian, who knew Zhao Gu would visit Lingyan Temple; Chang wrote two mediocre lines on the temple wall so that Zhao, when he arrived, would feel compelled to complete the poem with his better couplet — and he did. The mediocre lines were the brick; Zhao's couplet was the jade.
