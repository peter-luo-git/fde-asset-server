---
name: chengyu-break-whole-into-parts
description: Use when facing a task too large to hold in one piece — decompose into atomic subtasks. Divide-and-conquer in chengyu form. Pairs naturally with subagent dispatch (each part becomes one subagent's job) and with the dispatching-parallel-agents superpower. The decomposition itself is the skill — the parts should each have one clear purpose and a well-defined interface to the others.
---

# 化整為零 — break the whole into small parts

**化整為零** (huà zhěng wéi líng) — "transform the whole into small pieces". The literal: 整 (whole) + 零 (zero/small/atomic). The agentic translation is exact: **divide-and-conquer** as classical Chinese chengyu.

## When this applies

Symptoms that this schema applies:

- A task is too large to hold in working memory or one context window
- The task has natural seams (per chengyu-cleave-ox-at-joints) but the agent is treating it as one block
- Multiple sub-problems could be worked on independently and in parallel
- The operator's request is "do all of [X, Y, Z]" and X/Y/Z don't depend on each other
- Reviewing a 5000-line diff — break it into modules, review each
- Implementing a feature — break into testable atomic units (per TDD), each one shippable independently
- Debugging — bisect to narrow the search space (sub-skill of decomposition)

## Behavior

1. **Identify the natural seams.** Don't decompose arbitrarily; find the joints (per chengyu-cleave-ox-at-joints). Random decomposition produces parts that depend on each other through hidden state.
2. **Each part should have one clear purpose.** If you can't describe a part in one sentence, the decomposition went wrong.
3. **Each part needs a well-defined interface to the others.** Input contract, output contract, what it depends on, what depends on it. Ambiguity at the interface produces integration pain later.
4. **Each part should be small enough to hold in context at once.** This is the agentic difference from generic D&C — context window is the constraint; atomic = fits in one focused pass.
5. **Decide if parts are sequential or parallel.** Parallel parts → dispatch as subagents (pairs with dispatching-parallel-agents superpower). Sequential parts → execute in order, but each one is still scoped to fit in one pass.

## Decomposition checklist (apply per part)

For each candidate part, you should be able to answer:
- What does it do? (one sentence)
- How do you use it? (interface — inputs, outputs)
- What does it depend on? (other parts, external systems, operator decisions)
- How do you know it's done? (success criterion)

If any of these is fuzzy, the part is too large or the seam is wrong — re-decompose.

## Anti-pattern

**Decomposition theatre.** Producing a list of "parts" that are really just steps of a single sequential procedure with shared state. That's a recipe, not a decomposition. True decomposition produces parts with clean interfaces between them.

**Over-decomposition.** Breaking a small task into 12 sub-tasks. The decomposition overhead (interface design, coordination) exceeds the gain. Check: is each part actually small enough to need its own focus, or are you fragmenting for fragmentation's sake?

**Decomposition without seam-finding.** Slicing through bone (per chengyu-cleave-ox-at-joints). The parts won't be reusable, the interfaces will be messy, integration will pain.

**Decomposing then doing it all yourself anyway.** The point of decomposition is that the parts CAN be parallelised or delegated. If you decompose and then sequentially execute all parts yourself, you've done the planning step but skipped the harvest.

## Relationship to other chengyu / skills

- **chengyu-cleave-ox-at-joints** — provides the *where* to decompose (natural seams)
- **chengyu-break-whole-into-parts** — provides the *whether and how* to decompose (D&C discipline)
- **dispatching-parallel-agents** (superpower) — provides the *execution* of decomposed parallel parts
- **subagent-driven-development** (superpower) — provides the *coordination* of decomposed parts across subagents

These four compose into a full decomposition-and-execution pipeline.

## Etymology

A common idiom of strategic / organisational thought rather than a classical canon-text chengyu. Used widely in military, business, and engineering discourse. The literal pair (整 = whole, integrated, organised vs 零 = scattered, small, individual) makes the schema legible immediately to any speaker — no etymology required for the recall test to fire correctly.

The classical military application: a single large force is vulnerable to one decisive defeat; many small dispersed forces are resilient and individually hard to target. The agentic application is the same shape — one large monolithic task is one fragile execution; many small atomic tasks can each be reasoned about, tested, and retried independently.
