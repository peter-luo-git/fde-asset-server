---
name: chengyu-close-door-catch-thief
description: Use when debugging by narrowing the search space — isolate the failure domain so the bug reproduces in a controlled, minimal environment. CRITICAL Step 0: the agent must ASK (if unclear) or PITCH (if obvious, just needs operator confirmation) the specific "close the door" strategy BEFORE executing it; the closing has side effects (disabling services, modifying shared state, replacing real integrations with mocks) that need operator sign-off.
---

# 關門捉賊 — close the door, catch the thief

**關門捉賊** (guān mén zhuō zéi) — "close the door, catch the thief". Trap the failure inside a minimal, controlled domain so it can be inspected without all the surrounding noise. The narrowed search space is what makes the bug catchable.

## Step 0 (CRITICAL) — ASK or PITCH before closing

The "close the door" step has real side effects:
- Disabling a service or subsystem affects other consumers
- Mocking an integration loses fidelity to the real interaction
- Reproducing in a sandbox uses different data / config / scale than prod
- Locking down an environment may interfere with teammates' work

The agent **must not silently choose an isolation strategy**. Always:

| Situation | Action |
|---|---|
| Unclear how to isolate | **ASK** the operator: "How should we close the door here? Test in isolation? Disable feature X? Run against a fixture? Spin up a clean container?" |
| Obvious how to isolate, but it has side effects | **PITCH** the specific plan: "I'll close the door by reproducing in a sandbox with [X, Y] disabled. Confirm before I proceed." |
| Truly obvious AND zero side effects (e.g. a unit test runs in pure isolation already) | Proceed, but state the closing inline so the operator sees it |

Skipping Step 0 is the failure mode. The closing dynamics depend on the operator's system, team, and constraints, and the agent doesn't have that context.

## When this applies

Symptoms that this schema applies:
- A test is flaky in the full suite — isolate it; run in a minimal harness; see if it's still flaky.
- A bug only reproduces in production — reproduce in a controlled staging env with the same data shape.
- A subsystem is misbehaving — disable other subsystems to confirm the failure is in that one and not in an interaction.
- An integration test fails ambiguously — replace integrations with mocks/stubs one at a time to narrow which integration is at fault.
- A user-reported bug is unreproducible — get the user to share a minimal repro (you're asking *them* to close their door).
- Performance regression in a pipeline — bisect by disabling stages to find the slow one.

## Behavior

1. **Step 0: ASK or PITCH the closing strategy.** Do not proceed until the operator has confirmed.
2. **Execute the closing once confirmed.**
3. **Verify the failure still reproduces inside the closed domain.** If the bug *disappears* when you close the door, you've closed too many doors — the bug needed some of the things you disabled. Re-pitch a narrower closing.
4. **Debug with the narrowed search space.** With fewer moving parts, the cause becomes traceable.
5. **Restore what you closed.** When done, undo the disable / remove the mocks / tear down the sandbox. The closing was temporary instrumentation, not a permanent change.

## Anti-pattern

**Closing doors that lock other people out.** Shared infrastructure, prod data, teammates' working branches, CI pools — these are not yours to close unilaterally. The Step 0 ASK/PITCH catches this.

**Closing prematurely without confirming the bug actually fits in the closed domain.** False isolation — the bug seems gone, you declare victory, the bug returns the moment the doors open. The reproduction-inside-closed-domain check (Step 3) prevents this.

**Over-closing.** Minimal repro so different from real conditions that the bug disappears entirely. You closed too many doors; the bug needed some of them open. Re-pitch with one fewer thing disabled.

**Forgetting to restore.** Stubs and mocks left in place after the debugging session ends. The next bug becomes harder because the system is no longer in its real shape. Clean up.

**Silent closing.** Agent picks a closing strategy and runs with it without telling the operator. Even when it works, this trains the operator to lose trust in what state the system is in.

## Etymology

A burglar enters a courtyard with one gate; the household closes the gate and the burglar has nowhere to flee. Modern Chinese usage: positive/neutral, "trap the suspect by closing exits". Agentic translation: trap the *failure*, not the people. Same shape — make the escape paths zero so the thing you're chasing has to stay put — but redirect the metaphor's edge from human-targeting to defect-targeting.
