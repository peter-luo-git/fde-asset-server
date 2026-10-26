---
name: chengyu-think-thrice-before-acting
description: Use AFTER first-round planning is complete, to do META-PLANNING — what wasn't planned that should have been? What pitfalls don't surface in the first round due to existing context bias, context rot, plan mode system prompt biases, or other distortions? Triage importance, surface escape routes (subagent assessment, backups, operator ready for ctrl+C, anything). NOT a duplicate of plan mode or common-sense planning — this is the SECOND pass that catches what the first pass missed.
---

# 三思而後行 — think thrice before acting

**三思而後行** (sān sī ér hòu xíng) — "think thrice, then act". The chengyu's load-bearing word is *thrice* — not "think before acting" (which is common sense) but **think multiple times**, where each pass surfaces things the previous pass missed.

## Critical disambiguation

This skill is NOT:
- Plan mode (handled by plan mode itself)
- Common-sense planning (baseline competence)
- Generic "be careful"
- The first-pass plan you'd write naturally

This skill IS the **meta-pass after first-pass planning** — explicitly hunting for what the first pass missed due to:

- **Existing context bias** — the conversation framed the problem one way; the first pass inherited that framing
- **Context rot** — earlier context has degraded; assumptions from then may no longer hold
- **Plan mode system prompt biases** — plan mode itself has shapes it pushes toward
- **Sunk-cost / consistency pressure** — once a plan exists, the second pass tends to refine rather than question

## When this applies

Symptoms that this schema applies:

- A plan has been written (in any form — TodoWrite, plan mode, prose, conversation) and the next step would be execution
- The blast-radius of execution is non-trivial — destructive, network-visible, expensive, irreversible, or affecting shared state
- The plan was written under pressure / late at night / after long context / by an agent including yourself
- The plan looks "obvious" — which is often the signal that obvious-looking-things weren't questioned

## Behavior

The meta-pass has three explicit slots:

### 1. Pitfall hunt — what didn't surface in round 1?

Enumerate explicitly:
- What assumption is the first plan relying on that might not hold?
- What edge case is the first plan silent about?
- Did context rot eliminate a constraint the first plan would have respected?
- Is the first plan optimised for a frame that's no longer the operator's actual frame?

Spending 2 minutes here saves hours of rollback.

### 2. Importance triage

For each pitfall surfaced: how bad is it if it materialises?
- Catastrophic (data loss, security breach, shared infra down) — must mitigate
- Recoverable but costly (need to rerun, lose hours) — should mitigate
- Annoying but quickly fixed — accept

Don't treat all pitfalls equally; the meta-pass exists to direct effort to the right places.

### 3. Escape route inventory

For the catastrophic and costly pitfalls, surface explicit escape routes:
- **Ask a subagent** to second-opinion the plan from a different framing
- **Backup data / take a snapshot** before the destructive step
- **Have the operator ready** at the keyboard for ctrl+C / pull-the-plug
- **Dry-run first** with a no-op flag or in a sandbox
- **Stage the change** (deploy to 1% / one user / one branch) before full rollout
- **Pre-write the rollback** so you can execute it fast if needed

Surface options to the operator; let them pick which to deploy.

## Anti-pattern

**Re-running the same planning loop.** The meta-pass with the SAME context as the first pass will mostly reproduce the first pass. Deliberately shift framing: read older context, dispatch a subagent with a fresh start, ask "what would someone skeptical of this plan say?"

**Sandbagging via meta-pass.** Using "let me think thrice" to indefinitely delay execution of something that's actually clear. The schema applies when blast-radius is non-trivial; not as a general procrastination cover.

**Performing thoroughness.** Listing pitfalls you don't actually believe in, just to look careful. The pitfall hunt is for real candidates, triaged by real probability.

**Skipping the escape route inventory.** Identifying pitfalls without prepping mitigations is just anxiety. The point of three-pass thinking is to have a real safety net for the execute step.

## Etymology

From the *Analects*. Confucius is told that 季文子 (Ji Wenzi) thought thrice before acting. Confucius's response is sometimes read as endorsement, sometimes as critique:

> 季文子三思而後行。子聞之，曰：「再，斯可矣。」
> "Ji Wenzi thought three times before acting. The Master hearing this said: 'Twice is enough.'"

The agentic translation honours both readings — meta-planning is valuable (Confucius accepts the second pass), AND it can become its own avoidance pathology if extended indefinitely (Confucius rejects the third). The skill's discipline: do the meta-pass once, get the value, then act.
