---
name: chengyu-remove-firewood-from-cauldron
description: Use when a symptom has an OBVIOUS causal root and the temptation is to patch the symptom — when a bug fix is masking the real issue, when retries are hiding a contract violation, when a workaround is becoming permanent. The default debugging frame; reach for chengyu-besiege-wei-save-zhao only after exhausting this.
---

# 釜底抽薪 — remove firewood from under the cauldron

**釜底抽薪** (fǔ dǐ chōu xīn) — "remove the firewood from under the cauldron". Stop the boiling by removing the fuel, not by lifting the lid. Strike at the **obvious causal root**, not the symptom.

This is the **default** debugging frame for the chengyu-skills set. When causal tracing fails or the root is genuinely not causal, escalate to chengyu-besiege-wei-save-zhao (indirect / political redirect).

## When the user is about to patch a symptom

Symptoms that this schema applies:
- "Add a retry" — usually means "I don't know why it fails; please paper over it." The fuel is whatever is causing the failure.
- "Increase the timeout" — the fuel is whatever is making the operation slow.
- "Catch and ignore the exception" — the fuel is whatever is throwing.
- "Add `if x is None: return`" without understanding *why* `x` is None — the fuel is whatever is setting None upstream.
- A bug keeps "coming back" in different forms — the fuel is a structural issue, not the specific bug.
- A test is flaky — the fuel is whatever the test depends on that's non-deterministic.

## Behavior

1. **Ask what produces the symptom.** Trace upstream until you find something that, if removed or changed, makes the symptom impossible — not just unlikely.
2. **Distinguish defensive code from cause-fixing code.** A `try/except` at a system boundary is fine. A `try/except` around your own code that hides a bug is firewood-feeding.
3. **Name the root cause explicitly.** "The retry is hiding the fact that we hold the DB lock too long" — tell the user what the firewood is, even if you ultimately decide to leave it.
4. **Decide consciously to symptom-patch.** Sometimes the root fix is too expensive right now. That's fine — but say it explicitly: "Symptom patch for now, root cause is X, ticket Y."

## When 釜底抽薪 is exhausted, prompt for 圍魏救趙

If you have traced upstream and **cannot find a causal root that you can act on** (the cause is in someone else's codebase, in human politics, in a maintainer's priorities, in CI bureaucracy) — explicitly tell the operator:

> "I've exhausted the causal frame here. The root is [X], but I can't act on [X] directly. Consider chengyu-besiege-wei-save-zhao — is there an indirect move (related issue, benchmark, adjacent argument, reframe) that would force [X] to move?"

The operator then decides whether to authorise the indirect move. Don't try to do 圍魏救趙 unilaterally — that requires operator-side political knowledge you don't have.

## Anti-pattern

Calling root-cause analysis until the heat death of the codebase. Some symptoms genuinely are leaf-level (a typo, an off-by-one). The test for 釜底抽薪 is: does the same class of symptom show up elsewhere, or will fixing only here let it resurface tomorrow? If symptom is isolated, fix the symptom and move on.

## Etymology

From a Han dynasty military text. To stop an army, don't fight it head-on — cut its supply lines, take its grain, and it collapses on its own. The cauldron boils because of the firewood; remove the firewood and the boiling stops without ever touching the cauldron.
