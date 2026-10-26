---
name: chengyu-two-pipes-adversarial
description: GAN thinking mode — when delivering X, also run an adversarial pipe asking "is X fundamentally flawed as a concept? achievable with existing means via refactor? do bugs/vulns exist in X?". Two timings — pre-ship (in-vacuum, no real-world signal yet) and post-ship (with feedback signal). DELIBERATE REFRAME of the chengyu: the literal Chinese is "two pipes simultaneously" (parallel-cooperative), but the agentic translation makes the second pipe an adversarial discriminator. Inter-links with chengyu-listen-all-sides-see-clearly (which is external multi-source review) and chengyu-think-thrice-before-acting (which is process-level meta-planning).
---

# 雙管齊下 — two pipes simultaneously (agentic reframe: generator + discriminator)

**雙管齊下** (shuāng guǎn qí xià) — literally "two pipes / brushes drawn simultaneously". Classical reading: parallel-cooperative effort on the same goal. **Agentic reframe: the two pipes are ADVERSARIAL** — one generates the deliverable; the other actively attacks it, looking for what's wrong before reality finds it for you.

## IMPORTANT: this is a deliberate reframe

The classical Chinese reading is parallel-cooperative (e.g. Tang painter Zhang Zao with a brush in each hand, painting two trees at once). The agentic reframe is GAN-style (generator + discriminator): one pipe ships the work, the other pipe simulates the critic.

The framing in plain terms: *the spirit of 雙管齊下 in agent context is that the two 管 are adversarial — there's no need to teach "how to parallelize" (any decomposition skill covers that); what needs teaching is the GAN-mode discipline of don't just generate, be adversarial so as to catch issues before shipping.*

The recall-test argument: when a reader scans 雙管齊下, both readings can fire. The body's load-bearing role is to anchor the agentic one before the classical one drifts in. **Always lead with the adversarial framing when invoking this skill.**

## When this applies

Symptoms that this schema applies:

- About to declare a deliverable done — but no one has actively tried to break it
- About to ship a feature — but no one has asked "is this even the right feature?"
- About to commit a refactor — but no one has asked "could we just delete this code instead?"
- About to publish a writeup — but no one has stress-tested the argument for steelman counter-positions
- About to merge a PR — but no one has asked "what would the most adversarial reviewer in your project say about this?"
- After shipping — and you're tempted to move on without checking what surfaced

The shared shape: **the deliverable exists OR is about to exist, and no internal critic has been activated yet**. 雙管齊下 says: don't move on until the critic has run.

## The four adversarial questions

When the generator pipe is done (or about to be), the discriminator pipe asks:

1. **Is X fundamentally flawed as a concept?** Could the whole premise be wrong? Is there a frame from which X is obviously the wrong answer? (Concept-level critique.)
2. **Is X achievable with existing means / can we pull a refactor instead?** Could the same outcome be reached by deleting code, reusing existing patterns, or doing nothing? (Approach-level critique.)
3. **Are there bugs or vulnerabilities in X?** What's the failure mode? What edge case breaks it? What's the security surface? (Implementation-level critique.)
4. **What else about not delivering X?** Open category — anything the user would think of that the generator pipe missed. (Catch-all.)

## Two timings

The adversarial pipe runs at two distinct times, each with different information available:

### Pre-ship (in-vacuum)

No real-world signal yet. The discriminator has to work from priors + reasoning. Useful but limited — you don't know what users / production / reality will actually do.

Apply at:
- Before opening a PR
- Before pressing the deploy button
- Before declaring a design "done"
- Before sending a piece of writing
- Before committing a change to shared branch

### Post-ship (with feedback)

Real-world signal arrives — bug reports, telemetry, user feedback, CI failures, downstream agent behaviour. The discriminator now has empirical grounding, not just priors.

Apply at:
- After deploy lands
- After a PR is reviewed
- After a writeup is read
- After a feature has been in use for N days
- After a downstream agent has consumed your output

**Both timings are required.** Pre-ship catches concept / approach / obvious-bug issues; post-ship catches what only reality could surface. Skipping either leaves a class of problem uncaught.

## Behavior

1. **Identify the deliverable** (X). What is being generated? What's the artefact?
2. **Run the generator pipe to completion (or near-completion).** Don't interleave; let the generator finish a coherent draft before the discriminator engages, otherwise both pipes degrade.
3. **Run the discriminator pipe with the four adversarial questions.** Be specific — for each question, produce concrete attack-attempts, not generic worries.
4. **Decide based on the adversarial findings.** Three outcomes:
   - Discriminator found nothing real → ship
   - Discriminator found small things → fix in-place, ship
   - Discriminator found load-bearing things → either restart (`chengyu-retreat-is-best`), repair-then-ship, or surface to the user for direction
5. **After ship: schedule the post-ship adversarial pass.** Don't move on until reality has had a chance to test the deliverable AND you've checked what it surfaced.

## Inter-link with other skills

| Skill | Surface | Difference |
|---|---|---|
| `chengyu-two-pipes-adversarial` (this) | INTERNAL generator-discriminator within one agent's reasoning | The critic is simulated by the same agent |
| `chengyu-listen-all-sides-see-clearly` (兼聽則明) | EXTERNAL multi-source review (subagent / internet / other LLMs) | The critic is a separate entity |
| `chengyu-think-thrice-before-acting` (三思而後行) | Meta-planning — what didn't surface in the FIRST PLAN | Process-level adversarial pass, before generation |
| `chengyu-create-something-from-nothing` (無中生有) | Bottom-K sampling for divergent ideation | Can be the engine of the adversarial pipe — sample low-probability attacks |

Together these four skills form the **anti-overconfidence stack**:
- 三思而後行: adversarial-pass the PLAN before generating
- 雙管齊下: adversarial-pass the DELIVERABLE during/after generating
- 兼聽則明: adversarial-pass via EXTERNAL sources
- 無中生有: bottom-K sampling to find low-probability attack vectors

Use the four together when stakes are high; use selectively when stakes are low.

## Anti-pattern

**Generator-only.** Ship without ever running the discriminator pipe. The whole reason this chengyu exists is the observation that agents over-generate and under-critique.

**Discriminator-only.** All critique, no shipping. Skill is invoked AROUND a deliverable, not as a general anxiety pattern.

**Interleaving the two pipes.** Critiquing each sentence as you write it produces stilted output AND incomplete critique. Let the generator finish a coherent draft; let the discriminator have it whole.

**Generic discriminator output.** "There might be edge cases" / "this could have bugs" without specifics is performative discrimination. The four adversarial questions exist to force specificity.

**Skipping the post-ship pass.** Pre-ship catches concept / obvious-bug; post-ship catches what only reality knows. Skipping post-ship means the deliverable never gets the real test.

**Confusing this with parallel execution.** If you find yourself reaching for 雙管齊下 to describe "do two things in parallel", you've slipped back to the literal Chinese reading. Use `chengyu-break-whole-into-parts` + `dispatching-parallel-agents` superpower for parallel execution; this skill is specifically the adversarial reframe.

## Etymology

The classical chengyu: Tang dynasty (618-907). Painter Zhang Zao (張璪) was famed for painting two trees simultaneously, one brush in each hand, both equally skilled — neither pipe was secondary, both contributed to the same painting. The classical reading is therefore parallel-cooperative excellence.

The agentic reframe: in agent work, parallel cooperation doesn't need a chengyu handle — `chengyu-break-whole-into-parts` + `dispatching-parallel-agents` cover it. What DOES need a handle is the discipline of running an adversarial critic alongside the generator. The four-character form 雙管齊下 supports the reframe well: 雙 (two) + 管 (pipes/brushes) + 齊 (together) + 下 (drawn / applied) — nothing in the literal forces the cooperative reading; the cooperative semantic is convention. The reframe takes the same characters and reads them as generator + discriminator running together. This is similar to how `chengyu-create-something-from-nothing` reclaims an adversarial-core chengyu for productive sampling — a deliberate, narrow reading shift adopted for the agentic context.
