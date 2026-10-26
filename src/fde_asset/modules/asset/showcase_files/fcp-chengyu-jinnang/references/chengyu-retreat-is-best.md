---
name: chengyu-retreat-is-best
description: Use when abandoning a current approach / experiment / task / project, regardless of what triggered it (not just chengyu-skill or skill-driven contexts). Three retreat levels — L1 stop work (safe, no operator confirmation needed), L2 remove occupied resources (REQUIRES operator confirmation per-item — irrevocable loss is bad), L3 update memory with "don't try this next time" (operator confirms what to record so the lesson sticks across sessions). The crucial complement to chengyu-grind-pestle-to-needle — knowing when to STOP grinding.
---

# 走為上計 — retreat is the best stratagem

**走為上計** (zǒu wéi shàng jì) — "retreat is the best stratagem". Most famous of the Thirty-Six Stratagems (#36, last and most cited). The discipline of knowing when to bail — agents over-persist by default; this skill is the explicit counter-force.

Pairs with chengyu-grind-pestle-to-needle as opposite-and-complementary: 鐵杵磨針 says "keep grinding when the path is real but the result is far"; 走為上計 says "stop grinding when the path itself is wrong".

## When this applies

Symptoms that this schema applies (NOT just chengyu-skill or skill-driven contexts — generic bail-out):

- A debugging session has tried 10 things, none worked, and the next 10 won't either — the approach is wrong, not the count
- An experiment was based on an assumption that has now been falsified — continuing would compound the wrong inference
- A feature implementation is bigger than expected AND the requirement that drove it has weakened — retreat is cheaper than completion
- A project / fork / branch has drifted away from anything useful — bail before sunk cost gets larger
- The session itself is corrupted (context rot, repeated failures, model getting stuck in a loop) — restart in a fresh session

The fact that bailing FEELS bad is not signal that it's wrong. Sunk cost is invariant under future decisions.

## The three retreat levels

| Level | What it means | Operator confirmation |
|---|---|---|
| **L1** | Stop work. No further changes, no resource cleanup. The current state stays as-is. | Not required — safe default |
| **L2** | Remove occupied resources — kill processes, delete temp files, clean up branches, remove infra, etc. | **REQUIRED per-item** — irrevocable loss is bad |
| **L3** | Update memory with "don't try this next time" so the lesson survives the session boundary. | **REQUIRED for content** — operator confirms what gets recorded |

## Behavior

1. **State the bail explicitly.** "Retreating from this approach. Reasons: [X, Y]." Don't drift into something else without naming the bail.
2. **Pick the retreat level.** Default to L1 unless something obviously needs to be cleaned up.
3. **For L2: ASK or PITCH each resource removal.** Mirror of chengyu-close-door-catch-thief's Step 0 pattern — "I want to delete the staging branch / kill the training run / remove the temp directory at X. Confirm before I do it." Per-item, not batched, because each is irrevocable.
4. **For L3: PITCH the lesson text to the operator.** "I'd record: 'Don't try [approach] for [problem-shape] because [reason].' Approve, edit, or skip." Memory entries that survive across sessions are durable; get the wording right.
5. **Don't retreat further than the operator authorised.** L1 doesn't escalate to L2 unilaterally; L2 doesn't escalate to L3.

## Anti-pattern

**Silent resource cleanup.** Deleting branches / killing processes / removing files without operator confirmation. Catastrophic when wrong; very hard to recover from.

**Not marking the lesson.** Bailing without L3 means the same wrong approach gets retried next session because there's no record. Compound wasted work.

**Bail-out as procrastination.** Retreating from work that's actually clear because it's hard. The schema applies when the *approach* is wrong, not when you don't feel like continuing. Test: would a clean restart actually go differently, or just postpone the same difficulty?

**Heroic last-stand grinding.** Refusing to bail because "I'm so close" when in fact you're not. Pair this skill with chengyu-grind-pestle-to-needle's "if attempt N is just attempt N-1 with no new information, stop" check.

## Etymology

Thirty-Six Stratagems #36, the famous last. The full saying in modern usage: 三十六計，走為上計 — "of the thirty-six stratagems, retreat is the best." The classical military reading: when outmatched, an orderly retreat preserves the army for future engagement; a destroyed army loses everything. The agentic translation is exact — preserving cognitive resources (operator attention, agent context, time budget) for the next approach is worth more than the sunk cost of the current one.
