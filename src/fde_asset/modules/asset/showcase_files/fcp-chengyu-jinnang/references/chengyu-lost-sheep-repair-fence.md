---
name: chengyu-lost-sheep-repair-fence
description: Use after a failure has occurred (bug shipped, test broke, deploy rolled back, data lost, security incident) — the impulse is to fix the immediate damage and move on, but the chengyu's discipline is to ALSO fix the cause so it doesn't repeat. Pairs with chengyu-remove-firewood-from-cauldron on the timing dimension — 釜底 is pre-symptom prevention, 亡羊 is post-symptom fortification.
---

# 亡羊補牢 — lost the sheep, repair the fence

**亡羊補牢** (wáng yáng bǔ láo) — "lost the sheep, repair the fence". A sheep escaped through a hole in the fence. Two responses: (a) chase the sheep, ignore the hole; (b) chase the sheep AND repair the hole. The chengyu names (b).

The full saying in modern usage: **亡羊補牢，猶未為晚** — "to mend the fence after losing a sheep is still not too late."

## When this applies

Symptoms that this schema applies:

- A bug shipped to prod and was patched — but the *class* of bug could shipped again next week
- A test was failing and was fixed — but the *missing test* that would have caught it earlier is still missing
- A deploy was rolled back — but the deploy-process gap that allowed the bad change through is still there
- Data was lost — but the *backup gap* that made recovery hard is still there
- A security incident was contained — but the *detection gap* that let it go unnoticed for N days is still there
- A user-reported bug was fixed — but no test or invariant was added; the next agent could re-introduce it

The shared shape: the **immediate damage** got attention; the **systemic gap that allowed it** is the load-bearing fix.

## Behavior

1. **Address the immediate damage first.** The sheep is gone; whatever can still be saved should be saved. Don't moralise about the fence while the situation is still leaking.
2. **Then name the gap.** What fence-hole let this happen? Be specific — "no integration test for this code path" / "no monitoring on this metric" / "no documented invariant for this constraint".
3. **Propose the fence-repair concretely.** Add the test, add the monitor, write the invariant down, add the deploy gate. Not "we should be more careful" — concrete artefact.
4. **Pitch the repair to the operator.** It has a cost (the test takes time to maintain; the monitor adds infra; the gate slows deploys). Operator decides whether the fence-repair is worth the cost; they have context you don't.
5. **If the cost-benefit is negative, name THAT explicitly.** "Repair would cost X, payoff is Y, recommending we accept the risk and not repair." A decision recorded > an oversight repeated.

## Anti-pattern

**Patch and move on.** Fix the immediate symptom, don't surface the gap, don't propose the repair. The class of failure recurs next month; the same fix gets applied again.

**Repair without addressing damage first.** Building the fence while the sheep is still leaking. Address the urgent first, the structural second.

**Over-fencing.** Building an enterprise-grade fence in response to a $10 sheep. The cost-benefit must be honest; not every escape warrants a re-architecture.

**Performative post-mortem.** Writing a post-mortem doc with no actual change. The doc isn't the repair; the change is the repair. (Though the doc may be part of L3 of `chengyu-retreat-is-best` — making the lesson durable across sessions.)

## Relationship to other chengyu

| Chengyu | When in time | What it does |
|---|---|---|
| chengyu-remove-firewood-from-cauldron | BEFORE the failure | Trace upstream from symptom, fix root cause to prevent |
| chengyu-lost-sheep-repair-fence | AFTER the failure | Fix the systemic gap so the class of failure stops recurring |

Same root-cause-orientation, different timing. The combination is the full root-cause discipline.

## Etymology

From the *Strategies of the Warring States* (戰國策). A shepherd noticed a hole in the fence after losing one sheep. Friends told him to leave it — the sheep was already gone. He repaired it anyway, and no more sheep escaped.

The discipline: the cost of repair is paid once; the cost of not repairing is paid every time the failure recurs.
