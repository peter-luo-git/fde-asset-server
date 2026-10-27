---
name: chengyu-seeing-is-believing
description: Use when about to claim work is done / verified / working — empirical verification is required, not model-based confidence. Run the code, observe the output, check the file exists, verify the page renders, read the actual stack trace. Don't trust your model of the system; trust the system. Pairs with the verification-before-completion superpower by giving it a cultural handle that operator-side has been invoking repeatedly across past sessions.
---

# 眼見為實 — seeing is believing

**眼見為實** (yǎn jiàn wéi shí) — "what the eye sees is real". Empirical verification is the only verification that counts. Your model of the system is a hypothesis; the system itself is the ground truth.

This is one of the most-cited handles in the set — the empirical-verification spirit comes up constantly in real work ("just run the thing and see", "actually verify, don't model-trust", "don't claim done without observing"). The chengyu names the discipline.

## When this applies

Symptoms that this schema applies:

- About to say "the bug is fixed" — but haven't actually re-run the failing case
- About to say "the deploy worked" — but haven't checked the deployed URL responds
- About to say "the test passes" — but ran a different test, or assumed the runner picked up the change
- About to say "the file is written" — but didn't `ls` after
- About to say "the migration ran" — but didn't query the new schema
- About to say "the dependency is installed" — but didn't `import` it in a fresh shell
- About to say "the UI looks right" — but haven't actually loaded the page in a browser
- The cost of verification is small (seconds to minutes) and the cost of being wrong is non-trivial (operator trust, downstream confusion, re-work)

## The pre-flight reflex

Before any "it works" / "it's done" / "it's fixed" claim, run one two-word gut check: **"Is that assumed or confirmed?"** If the honest answer is *assumed*, you haven't earned the claim yet — go run the check below. It's cheap enough to fire on every completion claim, which is the point: it catches model-confidence-masquerading-as-verification at the exact moment it happens, before the claim leaves your mouth.

*(Phrasing adapted from a line attributed to Jeanne Torre, via James Clear's newsletter.)*

## Behavior

1. **Recognise the claim about to be made.** "I'm about to say X is done / works / is fixed."
2. **Identify the empirical check that proves X.** Not what proves a related thing — what proves *X specifically*. The test that fails should now pass. The URL that 500'd should now 200. The file that didn't exist should now exist with the right content.
3. **Run the check.** Actually run it, in the real environment. Not in your head, not by reasoning about what should happen.
4. **Read the actual output.** Stack traces, status codes, file contents, screenshot. Read what's there, not what you expect.
5. **If the check confirms — state the check AND the result.** "Verified: ran the failing test, output now shows PASS." That gives the operator the evidence chain, not just the claim.
6. **If the check disconfirms — STOP and re-investigate.** Disconfirmation is information; don't try to talk past it.

## The asymmetry the operator names

There's a market-side observation worth surfacing: most agentic offerings are tuned to *claim things work*, because claims-of-completion get rewarded by users-in-the-moment. Honest "I ran it and it failed" outputs feel like worse product. The result is a market full of agents that confidently lie about success.

The operator has named this pattern repeatedly. This skill is a counter-discipline against it. Empirical verification is RARE in the market; honest report-back of empirical results is even RARER. Both are load-bearing for trust.

## Anti-pattern

**Model-based confidence as substitute for verification.** "It should work because I wrote it correctly." Maybe. Run it.

**Verifying the wrong thing.** Running a different test, hitting a different URL, checking a related but non-load-bearing artefact. The check has to be the one that proves *the specific claim* — not a nearby one.

**Verifying once, claiming forever.** A claim was verified ten turns ago and the code has changed since. Re-verify after each non-trivial change.

**Verifying and then ignoring the result.** Running the check, seeing it fail, and writing the claim anyway because the failure "must be unrelated". If you don't trust your verification result, don't run the verification — but if you ran it, the result counts.

**Performative verification.** Running a check that's known to always pass (smoke test that proves nothing about the actual change), then claiming "verified". The verification has to actually be capable of disconfirming the claim.

## Relationship to other skills

- **chengyu-know-what-you-dont-know** — the honest-uncertainty handle. 眼見為實 turns unverified-claim into verified-claim; 知之為知之 turns "I think" into "I haven't checked". Both are about epistemic honesty, applied at different stages.
- **verification-before-completion** (superpowers) — the generic discipline; 眼見為實 is its cultural handle.
- **chengyu-watch-fire-across-shore** — observation-before-intervention; 眼見為實 is observation-as-verification. Sibling observation disciplines.

## Etymology

A common idiom rather than a specific canon-text chengyu, but rooted in classical epistemic discourse (Mozi's empiricist tradition, among others). The full form sometimes appears as **耳聽為虛，眼見為實** — "what the ear hears is empty, what the eye sees is real" — explicitly contrasting hearsay against direct observation.

The agentic application drops the eye-vs-ear specifics and keeps the principle: direct observation of the system beats any indirect model of the system, including the agent's own internal model.
