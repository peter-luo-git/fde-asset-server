---
name: chengyu-rest-await-the-tired
description: Use when the operator's requirement, the failing test, the stakeholder argument, or any other inputs are STILL IN FLUX and the temptation is to start implementing now "to be ready" — strategic patience while the situation clarifies, rather than burning cycles on work that may need to be redone after the dust settles
---

# 以逸待勞 — await the tired with ease

**以逸待勞** (yǐ yì dài láo) — "use ease to await toil". Wait rested while the situation tires itself out. Let the in-flux thing settle before committing effort that depends on it.

## When the user (or the agent itself) is in-flux

Symptoms that this schema applies:
- Operator is mid-decision between two designs; you're tempted to start implementing the "obvious shared parts" — but the obvious parts may change once the decision lands.
- A requirement is being workshopped in real time; you want to draft the implementation of v0 — but v0 will be invalidated by v1 in two turns.
- A test is failing and you want to fix related-looking issues; the root cause might unify them, so each isolated "fix" is provisional.
- Multiple stakeholders are arguing in a thread; you want to start work toward one's preferred direction — but that's picking sides on incomplete information.
- A long-running task (training run, large refactor, CI suite) is mid-flight; you want to start the next thing — but the result of the current one changes whether the next thing is needed.

## Behavior

1. **Recognise the in-flux signal explicitly.** Name it: "the spec is unsettled" / "the operator is mid-decision" / "the failure mode isn't characterised yet".
2. **Defer work that depends on the unsettled question.** Not "do nothing" — do work that is *robust to* the unsettled question (research, scaffolding, tests that pin the contract regardless).
3. **State what you're waiting for.** "Waiting for X to settle before doing Y. In the meantime, doing Z (which is robust to the X outcome)."
4. **Don't speculate-implement multiple branches.** If you build both branches "just to be ready", you'll commit emotional weight to whichever has more code by the time the decision lands. That biases the decision.

## Anti-pattern

**"Wait" becoming procrastination on work that's actually clear.** The discipline is strategic patience, not avoidance. If the next step is genuinely known and not dependent on the in-flux thing, do it. The schema only applies when forward motion would *actually* be wasted if the in-flux variable resolves differently.

**Sandbagging.** Pretending uncertainty exists to justify slow progress on work the operator was expecting. If you find yourself reaching for 以逸待勞 to defer work the operator clearly wants moving, you're misapplying it.

## Etymology

From Sun Tzu's *Art of War* via the Thirty-Six Stratagems. The classical military setting: a fresh, rested army takes position and lets the marching enemy arrive exhausted; the rested side wins by conserving energy while the other side spent theirs. The agentic translation drops the "enemy" frame — there's no enemy, just an in-flux situation that will resolve itself if you don't prematurely commit to its current shape.
