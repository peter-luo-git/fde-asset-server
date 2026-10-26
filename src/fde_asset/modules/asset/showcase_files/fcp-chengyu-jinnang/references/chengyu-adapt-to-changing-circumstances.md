---
name: chengyu-adapt-to-changing-circumstances
description: Use when the original plan needs to flex mid-flight in response to new information / changed inputs / discovered constraints — but flexibility WITHOUT defined scope degenerates into either rigid plan-sticking or unbounded winging-it. CRITICAL Step 0 (mirror of chengyu-close-door-catch-thief): scope MUST be defined before any deviation. If operator hasn't given a flexibility-scope tier, agent must PROPOSE 2-4 tiers and let operator pick where the line is. Default tier is "don't do irreversible damage to hardware, software, and data" but context shifts the boundary (hackathon, prod deploy, code review session all have different tier shapes).
---

# 隨機應變 — adapt to changing circumstances

**隨機應變** (suí jī yìng biàn) — "follow the moment, respond to change". The discipline of adapting plan to reality when reality diverges from plan — but only within an explicitly negotiated scope of flexibility.

## Step 0 (CRITICAL): define the flexibility scope

The operator's load-bearing observation: agents without defined flexibility scope **degenerate into one of two failure modes**:

1. **Stick tight to plan** — refuse to deviate even when new information makes the plan obviously wrong
2. **Wing it totally** — abandon plan structure entirely, ad-hoc whatever feels right in the moment

Both are bad. The discipline is the middle path: deviate within bounds the operator has authorised.

### When operator gives scope: proceed within that scope

If the operator has stated the flex scope (e.g. "you can refactor freely but don't change the public API", or "stay within the 2-hour budget", or "any change that's git-revertable is fine"), proceed within that scope. Adaptation inside the scope is authorised; adaptation outside the scope requires re-negotiation.

### When operator hasn't given scope: PROPOSE tiers

Surface 2-4 tiers of flexibility, let operator pick. Don't unilaterally choose. This mirrors `chengyu-close-door-catch-thief`'s Step 0 ASK-or-PITCH pattern.

## Default tier set: "don't do irreversible damage"

Most contexts have flexibility scope rooted in "don't do irreversible damage to hardware, software, and data". Example tier set:

| Tier | Allowed deviations | Forbidden |
|---|---|---|
| **T0 (rigid)** | None — follow plan exactly | Any deviation |
| **T1 (reversible)** | File edits, additions, branch work, anything `git revert` undoes | External-effect operations, deletes |
| **T2 (small-effect)** | T1 + cheap external calls (API requests with low cost, log output, telemetry) | Destructive operations, irreversible commits |
| **T3 (consequential)** | T2 + delete local files, force-push to personal branches, drop test databases | Irreversible production changes, destructive operations on shared infrastructure |
| **T-max (catastrophic)** | Only on explicit per-action operator command | — |

Default scope when context doesn't shift it: **T2** (reversible + cheap external-effect). T3+ requires per-action confirmation.

## Context-shifted tier examples

Same chengyu, different tier shapes depending on the context:

### Hackathon

Operator's example: in a hackathon, "doing time-costly items as if it is overnight coding would be no good". The flex scope is TIME, not reversibility:

| Tier | Allowed |
|---|---|
| T0 | Stick to scoped feature set, 5-min fixes only |
| T1 | T0 + 30-min features that ship visible value |
| T2 | T1 + 2-hr features if they're judgment-grade |
| T3 (forbidden in hackathon) | Refactors, rebuilds, design changes that block teammates |

### Production deployment

The flex scope is BLAST RADIUS:

| Tier | Allowed |
|---|---|
| T0 | Only the planned change, fully reviewed |
| T1 | T0 + revert-on-demand cosmetic fixes |
| T2 (typically forbidden) | Adjacent code cleanups even if "obviously safe" |

### Code review session

The flex scope is REVIEW DEPTH:

| Tier | Allowed |
|---|---|
| T0 | Only flag bugs in the specific lines changed |
| T1 | T0 + adjacent code that the change touches |
| T2 | T1 + design suggestions for separate follow-up PRs |
| T3 | T2 + broader architecture critique |

### Long-running agentic task

The flex scope is SCOPE CREEP:

| Tier | Allowed |
|---|---|
| T0 | Original task only; surface deviations to operator |
| T1 | T0 + closely-related cleanups discovered along the way |
| T2 | T1 + adjacent improvements that the agent has high confidence on |
| T3 | T2 + opportunistic refactors with operator approval |

## Behavior

1. **Step 0: define or propose flexibility scope.** Either operator has given one OR agent proposes 2-4 tiers and waits for selection.
2. **Once scope is defined, deviation INSIDE the scope is authorised** — don't re-ask permission for every adaptation that falls within the agreed tier.
3. **Surface deviations that approach the scope boundary** — "I'm about to do X which is at T2; the agreed scope is T2; proceeding" gives the operator a chance to catch you if X feels like it crosses.
4. **For deviations OUTSIDE scope: stop and re-negotiate.** The operator agreed to T2; X is T3; stop, surface, ask.
5. **Document scope decisions in commit / memory** so the lesson sticks across sessions. The tier picked for this context is signal about how the operator thinks about this category of work.

## Anti-pattern

**Winging it without scope.** The chengyu's most common failure mode — agent adapts and adapts and adapts, no constraint, blast radius grows unbounded. Operator turns around and finds the agent has refactored half the repo "while it was at it".

**Rigid plan-sticking without scope.** The opposite failure — agent refuses to adapt even when new information clearly invalidates the plan. Plan becomes a cage. The fix: scope authorises *some* flex, not zero.

**Choosing the tier without operator input.** This is the bug Step 0 exists to prevent. Tier selection has cost (operator's downstream review burden, blast radius, time budget); only the operator knows their actual constraints.

**Treating the tier as static.** Scope can shift mid-task — e.g. context changes (hackathon ends; production-pause window opens; operator joins keyboard). Re-check scope at major transitions, not just at task start.

**Skipping the surface-deviations step.** Agent operating at T2 may make changes that fall within T2 but feel surprising to operator. Surface boundary-adjacent changes proactively — operator can correct in real time.

## Relationship to other skills

| Skill | Surface | Difference |
|---|---|---|
| `chengyu-adapt-to-changing-circumstances` (this) | Mid-flight deviation scope | Negotiates HOW MUCH to deviate when plan meets reality |
| `chengyu-stop-when-appropriate` (適可而止) | When to STOP a successful approach | Different decision — when work is done, not when work changes shape |
| `chengyu-retreat-is-best` (走為上計) | When to ABANDON a failed approach | Different decision — full bail-out, not mid-flight flex |
| `chengyu-think-thrice-before-acting` (三思而後行) | Pre-action meta-planning | Tier-definition can happen in 三思 phase; this skill is the in-flight executor |
| `chengyu-close-door-catch-thief` (關門捉賊) | Isolation scope for debugging | Same Step-0 ASK-or-PITCH shape; different domain |

The Step-0 ASK-or-PITCH pattern is now established across multiple skills (關門捉賊, 反客為主-dropped, 走為上計's L1/L2/L3, 兼聽則明's source-class selection, 大智若愚's target-param, this). When a skill has a parameter whose wrong selection has real cost, the agent surfaces and the operator picks. Common architectural shape.

## Etymology

A classical chengyu of practical wisdom and military strategy. The literal: 隨 (follow / accord with) + 機 (opportunity / pivotal moment) + 應 (respond) + 變 (change). Used historically to describe generals who didn't slavishly follow rigid battle plans when reality on the battlefield diverged from the briefing-room map.

The agentic translation drops the military setting but keeps the discipline: a plan written before reality is encountered will not perfectly match reality. Adaptation is required. The skill is in WHERE TO DRAW THE LINE — neither rigid plan-sticking nor unbounded improvisation, but a negotiated scope within which adaptation is authorised.

Modern Chinese usage is firmly positive (adaptability as virtue), with the implicit understanding that good 應變 requires judgment, not just willingness to deviate. The tier-definition discipline here makes that implicit judgment explicit.
