---
name: chengyu-cicada-sheds-its-shell
description: Use when designing a migration — graceful transition where the old interface stays as a thin stub/proxy/shim, real implementation moves underneath, consumers keep working through the transition, eventual cleanup of the shell once consumer count reaches zero. The schema of every good rename / endpoint move / library v1→v2 / database schema migration / monorepo split.
---

# 金蟬脫殼 — the cicada sheds its shell

**金蟬脫殼** (jīn chán tuō qiào) — "the golden cicada sheds its shell". The cicada matures, leaves the outer shell behind on the tree, and flies away; the shell remains visibly recognisable. In agentic engineering use: this is exactly what a good migration looks like — the old interface stays as a stub while the real implementation has moved.

## Cultural-shift note

The classical military reading has a deceptive-escape flavour (army leaves but leaves campfires burning to fake presence). The modern engineering reading dominates in tech discourse for this audience: shedding-as-graceful-migration, where the shell is honest (a real stub that delegates, not a fake one that lies). This skill embraces the engineering reading; do not pull the deceptive-escape frame.

## When this applies

Symptoms of a migration that wants 金蟬脫殼:
- Renaming a function with external callers — old name should remain as a thin wrapper around new name, with a deprecation warning, removed only after callers update.
- Moving a service endpoint — old endpoint returns 301 or proxies to the new one for a deprecation window.
- Splitting a monolith into modules — old import path still works (re-exports from the new location) until consumers update their imports.
- Database schema migration — views or triggers maintain the old shape during the transition window.
- Library v1 → v2 — v1's API kept as a shim wrapping v2 calls; v1 removed only when consumer count reaches zero.
- API contract change — old contract supported alongside new, with telemetry to see when the old contract stops being called.

The shared shape: **the old surface stays alive and honest while the real work moves**.

## Behavior

1. **Identify the consumers of the old interface.** Search the codebase, search downstream repos, check logs/telemetry. If you don't know who consumes the old interface, the shell can't be sized right.
2. **Build the new implementation cleanly** — don't compromise the new design to make the migration easier; that's tail-wagging-dog.
3. **Leave a stub at the old interface that delegates to the new.** Honest stub, not a fake — the stub actually works, just by calling through to the new implementation.
4. **Deprecate the stub with a clear timeline.** "Removed in v0.X / on date Y / when telemetry shows zero calls for Z days." Without a timeline the stub becomes permanent tech debt.
5. **Migrate consumers one by one.** Send PRs, file issues, communicate the deprecation in changelogs and release notes.
6. **Remove the stub when consumer count = 0.** The cicada doesn't come back for its shell; the shell is meant to be discarded once the transition is done.

## Anti-pattern

**Big-bang migration without the shell.** Breaks consumers, generates outages, forces panic-revert. The shell is the politeness that makes migration possible.

**Leaving the shell forever.** The cicada is supposed to LEAVE eventually — and the shell is supposed to be removed eventually. Stubs that survive their original purpose accumulate as a sediment of confusing legacy surfaces. Always set the removal condition explicitly.

**Stealth migration without a stub.** Moving things without leaving the old interface in place — the modern "sneaky escape" reading; deceptive, breaks consumers' assumptions silently. The whole point of shedding-the-shell is that the shell is *visibly* there for the transition.

**Wrapping the wrong direction.** The shell goes on the OLD interface, not the new. The new implementation should be clean; the old surface is what gets the thin wrapper. Backward shell, not forward shell.

## Etymology

A cicada in its final moult: the nymph climbs a tree, splits its outer skin (the shell, 殼), and emerges as the adult cicada — flying away while the discarded shell remains stuck to the bark, intact and visibly cicada-shaped. The shell is *real* (it was the cicada); it's just no longer animated. The classical military application of this image was deception (leave the camp shape behind while the army moves); the agentic application is honest migration (leave the interface shape behind while the implementation moves).
