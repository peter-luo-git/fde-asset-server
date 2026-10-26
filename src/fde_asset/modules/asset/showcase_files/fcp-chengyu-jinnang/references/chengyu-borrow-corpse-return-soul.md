---
name: chengyu-borrow-corpse-return-soul
description: Use when proven-elsewhere logic should be transplanted into your project rather than re-derived — covers both reviving abandoned-but-good code from your own history AND copying live working config (linting, CI, README templates, infra patterns) from a healthy sibling project. The "soul" is the working logic; the "body" is your project that receives it.
---

# 借屍還魂 — borrow a corpse to return a soul

**借屍還魂** (jiè shī huán hún) — "borrow a corpse to bring back the soul". The substance/logic lives on through a new vessel. In agentic engineering use: working code/config from one place is transplanted into another rather than being re-invented from scratch.

## Two flavours, one schema

The chengyu's "soul transplant" semantic covers two distinct agentic situations:

1. **Reviving the dead** — old code from a deprecated project, abandoned branch, or your own history that solved the current problem. The body is dead but the logic was right; bring it back into a live project.
2. **Transplanting the living** — copy working config from another *live, healthy* project. Copy linting/CI from a sibling repo, copy README/CONTRIBUTING templates from a project you respect, copy an infra pattern from a service you trust.

Both are 借屍還魂. The soul (working logic) survives the move into a new body (your project). Skip the re-derivation cost.

## When this applies

- You're about to write CI from scratch — a sibling project already has CI that you've vetted; copy that.
- You need linting/formatting config — borrow from a project whose code-quality you admire, don't roll your own opinions.
- A migration you wrote 18 months ago in a different repo solved exactly this — port the migration, don't re-derive.
- A library has been deprecated but its core algorithm was correct — fork the algorithm into your own utility module.
- Onboarding a new repo — copy the README scaffolding, the `.github/` directory, the `pyproject.toml` skeleton from a repo that does this well.

## Behavior

1. **Identify the donor.** Name what working logic exists elsewhere that you could borrow. If you can't name a specific donor, this isn't 借屍還魂 — it's just "write something."
2. **Verify the donor is alive and right.** For revival case: was the dead code actually working when it died, or did it die because it was broken? For transplant case: is the live project actually healthy, or just popular? Check before borrowing.
3. **Bring the *logic*, not just the surface.** Understand WHY it works before transplanting. Cargo-cult copies break in your context because the donor's context isn't yours.
4. **Adapt to your project's conventions.** Verbatim copy is suspicious — different style, different naming, different deps. Adapt at the seams.
5. **Credit the donor.** In the commit, in comments if non-obvious. Both for license/etiquette reasons and so future-you can find the donor when the soul needs an update.

## Anti-pattern

**Cargo-culting** — copying without understanding why it works. The borrowed code runs but you can't explain it, so when it breaks you can't fix it, and you've imported a foreign object that the rest of the codebase can't reason about.

**Cargo-culting from dead projects** — assuming "famous = right". The donor died in 2021; the world has changed; the soul may not be returnable. Verify the donor's logic is still correct under current constraints (new versions, new threats, new conventions).

**Copy-as-laundering** — taking restrictively-licensed code and stripping the attribution. Legal landmines aside, this corrupts the donor's credit chain and makes the soul un-findable when it needs updating.

**Re-deriving when a donor is right there.** Pride / NIH (Not Invented Here) is the failure mode in the other direction. If a known-good donor exists and you have the right to use it, the *re-derive* cost is the unjustified one.

## Etymology

A Daoist/folk legend frame: someone has died, but their soul is good and shouldn't be lost; the soul borrows another body to come back to life. The classical military reading (Thirty-Six Stratagems) is more cynical — using a fallen banner or pretext to re-establish a faction. The engineering/agentic translation drops the cynical frame and keeps the substance-survives-the-vessel core: working logic outlives the project it lived in.
