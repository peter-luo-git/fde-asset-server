---
name: chengyu-cleave-ox-at-joints
description: Use when planning any non-trivial change — refactor, bug fix, migration, optimisation, communication — and the agent should find the natural seams / high-leverage points of the target system rather than brute-forcing through. Cut at joints (low resistance, clean result), not at bone (high resistance, blade dulls, messy result). The patron-saint chengyu of working WITH the grain of the system.
---

# 庖丁解牛 — Cook Ding cleaves the ox at the joints

**庖丁解牛** (páo dīng jiě niú) — "Cook Ding cleaves the ox". Find the natural seams of the target and slide the blade through where there is no resistance. The blade preserves itself; the work is clean; the result is fast — because the structure was respected, not fought.

This is arguably the most important chengyu in this set. It names the central virtue of good agentic operation: working WITH the system rather than against it.

## When this applies

Symptoms that this schema applies:

- **Refactor**: identify the natural module boundaries; cut along those, not across them. A change that fits inside one module is at a joint; a change that bleeds across five is at a bone.
- **Bug fix**: find the high-leverage single-line change at the actual root, not the 50-line patch that papers over multiple symptoms.
- **Migration**: identify natural transition points — function boundaries, type boundaries, version boundaries — and migrate at those. Don't migrate mid-function or mid-type; that's mid-bone.
- **Optimisation**: find the 10% of code that consumes 90% of cycles. Optimise there. Optimising the other 90% is bone-hacking with no payoff.
- **Documentation**: find the 3 key concepts that explain 80% of the system; document those well. Documenting everything equally is bone.
- **Communication with operator**: surface the one decision that gates 5 others; don't weight all 6 equally. The gating decision is the joint.
- **Test coverage**: cover the high-traffic load-bearing paths well; don't aim for 100% line coverage on glue code.

## Behavior

1. **Pause before cutting — locate the joints first.** The single most important step. The blade-dulling failure mode comes from cutting first and finding-the-joint never.
2. **Identify natural seams of the target.** Architectural (module boundaries), semantic (concept boundaries), lexical (function/type boundaries), temporal (version boundaries).
3. **Choose the lowest-resistance path that achieves the goal.** Not the path that demonstrates cleverness; the path where the structure already wants the change to be.
4. **If you find yourself fighting the structure** — the change cascades further than expected, you're rewriting more than expected, the resistance is high — STOP. You're hitting bone. Step back and re-locate the joints from a different angle.
5. **If no clear joint exists**, escalate to operator: "I can't find a clean joint for this change. Options: (a) bone-cut anyway with explicit cost, (b) restructure first to create a joint, (c) change the goal so a joint emerges. Pick."

## Anti-pattern

**Brute-force cutting.** Hack through bone; high resistance; quick blade-dulling; messy result. In agentic terms: large blast radius changes that the codebase didn't want, repos that look refactored but are actually more entangled.

**Refusing to cut at all.** Avoiding the operation because you can't find the *perfect* joint. Better an OK joint than no cut; perfect-joint paralysis is its own failure mode.

**Hallucinating joints that aren't there.** "This looks like a seam" when it's actually load-bearing bone. Verify before cutting — read the references, check the consumers, trace the dependencies. A surprise-bone cut is worse than a known-bone cut.

**Generalising from one joint to all joints.** Each ox is different. The joint pattern in one codebase doesn't transfer verbatim. Re-find the joints in each new target.

**Cleaving for its own sake.** Cutting because cutting feels productive, even when the system didn't need surgery. The chengyu assumes there's an ox to be cleaved; if there isn't, put the blade down.

## Etymology

From the *Zhuangzi* (莊子), the chapter on Nourishing Life (養生主):

Cook Ding (庖丁) is cleaving an ox for Lord Wenhui (文惠君). The blade flows; the strokes follow rhythm; it is more dance than butchery. Lord Wenhui marvels: "Ah, excellent! How can your skill have reached such heights?"

Cook Ding sets down his blade and answers:

> "What I love is the Way (道), which goes beyond mere skill. When I first began cutting up oxen, all I could see was the ox itself. After three years I no longer saw the whole ox. And now — I work with my mind, not with my eye. My senses stop and my spirit moves as it will. I follow the natural grain, slide the blade through the great hollows, guide it through the great cavities. I follow the structure as it actually is."

> "A skilled cook changes his blade once a year — because he cuts. A common cook changes his blade once a month — because he hacks. I have had this blade of mine for nineteen years and it has cut up thousands of oxen, yet its edge is as if it had just come from the grindstone. Between the joints there are gaps, and the edge of the blade has no thickness. When that which has no thickness enters where there is gap, there is plenty of room. That is why after nineteen years the edge is as fresh as if newly off the stone."

> "However, when I come to a complicated knot — I see the difficulty, I become cautious, I fix my gaze, I work slowly, I move the blade with the greatest subtlety. With one slight movement the whole thing falls apart like a clod of earth crumbling to the ground. I stand there, blade in hand, looking around, satisfied and reluctant to move on. Then I wipe the blade and put it away."

Lord Wenhui: "Excellent. Listening to Cook Ding, I have learned how to live."

The Daoist reading: the world has structure; wisdom is finding the structure and moving with it. The agentic translation is exact: codebases have structure, problems have structure, conversations have structure — find it, cut at the joints, and the blade lasts.
