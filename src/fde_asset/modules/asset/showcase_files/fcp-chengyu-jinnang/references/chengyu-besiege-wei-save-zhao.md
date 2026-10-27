---
name: chengyu-besiege-wei-save-zhao
description: Use when the operator has explicitly authorised an indirect / non-causal move — when frontal attack is blocked AND chengyu-remove-firewood-from-cauldron has been exhausted, requiring attention-redirect or incentive-shift rather than technical root-fixing. Default to chengyu-remove-firewood-from-cauldron unless the operator has told the agent how to 圍魏.
---

# 圍魏救趙 — besiege Wei, save Zhao

**圍魏救趙 / 围魏救赵** (wéi wèi jiù zhào) — "besiege Wei to rescue Zhao". When frontal attack fails and causal tracing has no actionable root, create a **new, non-causal pressure elsewhere** that forces the blocking party to reallocate.

This is **not** chengyu-remove-firewood-from-cauldron. The mechanism is different: Sun Bin didn't fix what was causing Zhao's siege — he made Wei's army go home by threatening their territory. Attention politics, not technical roots.

## Default: try 釜底抽薪 first

If the operator hasn't explicitly told you to make an indirect move, **prompt for chengyu-remove-firewood-from-cauldron instead**. The causal frame is:
- cheaper to verify (does removing X actually fix the symptom?)
- safer (no political collateral damage)
- within agent capability (technical tracing vs. social reading)

Only proceed with 圍魏救趙 when **the operator has explicitly described the indirect move they want**. Agent-initiated political moves without operator direction is the failure mode this skill must avoid.

## When the operator authorises 圍魏

Real shapes the operator might use:
- "Open a related issue first — it'll shift the reviewer's priorities so my PR floats up."
- "Ship the benchmark — it makes their alternative look weak, and then mine merges."
- "Publish the writeup that reframes this; once the framing is public, the current objection becomes unreasonable."
- "Win the adjacent argument — the current one becomes moot."
- "Build community support for approach B so refusing B becomes the controversial position."

The common shape: the blocking party is not removed and not technically fixed — the **incentives around them shift** so they move on their own.

## Behavior

1. **Confirm operator authorisation.** Quote back the indirect move the operator described. If unclear, ask: "You want me to [indirect move] rather than [causal fix]? Confirm before I proceed."
2. **Execute the operator-specified indirect move precisely.** Don't improvise additional indirect moves; the operator has political context you don't.
3. **Surface the indirection in your output.** "I'm not fixing X — I'm doing Y, which should pressure X into resolving itself."
4. **Stop if the indirect move risks burning bridges the operator hasn't priced in.** A benchmark that embarrasses a maintainer might cost a relationship. Flag the cost; let the operator decide.

## Anti-pattern

**Doing 圍魏救趙 when 釜底抽薪 would have worked.** The causal frame is cheaper, safer, more verifiable. If you find yourself reaching for 圍魏救趙 because the causal fix is *boring*, you're yak-shaving — go back to causal.

**Doing 圍魏救趙 unilaterally.** Political moves need operator authorisation. The operator has social context (who's blocking, what they care about, what the relationship costs are) that the agent does not. Initiating indirect moves without that context is high-blast-radius.

## Etymology

Warring States era (354 BC). Wei attacked Zhao's capital Handan. Qi general Sun Bin, asked to rescue Zhao, did not march to Handan — that would have been the direct/causal move. Instead he attacked Wei's lightly-defended capital. Wei's army had to abandon the siege of Handan to defend home, and Sun Bin ambushed them en route. Zhao was saved not by fixing what besieged it, but by creating an unrelated threat that pulled the besieger away.
