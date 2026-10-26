---
name: chengyu-great-wisdom-looks-foolish
description: Use when resisting the urge to demonstrate cleverness — obvious-and-simple is often the right answer, and performative complexity is the failure mode. REQUIRES a target parameter at invocation specifying which dimension to apply to — implementation (over-engineered code), representation (over-fancy reply / unnecessary verbosity), or both. The agent must ASK for the target if the operator hasn't specified.
---

# 大智若愚 — great wisdom looks like foolishness

**大智若愚** (dà zhì ruò yú) — "great wisdom looks foolish". The truly wise solution looks ordinary, almost too simple, but it works. Performance of intelligence creates resistance and invites challenge; the wisdom that doesn't announce itself does the work.

## Target parameter required

This skill has two distinct application dimensions, and the operator must specify which (or both). The agent's Step 0 is to confirm the target:

| Dimension | What it means | Common failure mode |
|---|---|---|
| **Implementation** | Code, architecture, system design | Over-engineered solutions when simple ones would do. Factories for things that are just functions. Inheritance hierarchies where composition fits. Complex algorithms where O(n²) is fine because n=10. Abstractions for hypothetical future requirements. |
| **Representation** | Writing, replies, documents, conversation | Over-fancy replies. Essays where bullet points fit. Rare vocabulary where common words work. Flourishes that add length but no signal. Demonstrating range when the question asked for a yes/no. |
| **Both** | Applies to all output this turn | Default for "keep it simple overall" |

### Step 0: confirm target

If the operator hasn't specified the dimension:
- ASK: "Apply 大智若愚 to implementation, representation, or both?"
- Do not assume both — they're orthogonal axes with different cost profiles. Implementation-simplicity affects what gets built; representation-simplicity affects how it's communicated.

## When this applies

Symptoms that this schema applies:

- Operator explicitly says "keep it simple" / "this doesn't need to be fancy" / "just the answer"
- The clever solution is significantly more code/words than the obvious one — and the operator hasn't asked for the cleverness
- You're about to reach for a complex pattern (decorator stack, metaclass, monad, dependency injection container, custom DSL) for something that could be a function
- You're about to write a multi-paragraph explanation when "yes" or "no" would do
- You're about to introduce abstractions for hypothetical future requirements
- You're about to use a rare word where a common word would convey the same meaning more reliably

## Behavior

1. **Step 0**: confirm target parameter (implementation / representation / both). If unspecified, ASK.
2. **Identify the simplest version** of the solution / response that meets the actual stated requirement (not the imagined future requirement).
3. **If the simple version genuinely fails** under real constraints, surface why and propose the next step up, with explicit reasoning for the added complexity.
4. **Default toward boring; require justification for clever.** The burden of proof is on complexity.
5. **Don't apologize for the simplicity.** The simplicity is the feature, not a limitation. "I went with the straightforward approach because…" beats "Sorry this is so simple but…".

## Anti-pattern

**Under-engineering / "do the dumb thing".** 大智若愚 is NOT "be foolish". It's "match the right level of complexity to the actual need". A real-time distributed system can't be a flat function; recognise when complexity is genuinely warranted and apply it without apology.

**Faux-simplicity.** Writing something terse-but-incomprehensible and claiming it's "simple". Simplicity is about cognitive load on the reader / maintainer, not about character count. A one-liner that requires three Stack Overflow tabs to parse is not simple.

**Applying to the wrong dimension.** If operator asked for representation-simplicity ("just tell me the answer"), don't unilaterally also simplify the implementation. The Step 0 ASK exists precisely to prevent this.

**Confusing "looks foolish" with "is foolish".** The chengyu's whole point is the wisdom is *real but doesn't announce itself*. Genuinely foolish solutions are the failure mode this chengyu is trying to *prevent confusion with*.

**Performing simplicity.** Writing "well, the simple answer is…" three times in one response is itself a performance. Just give the simple answer; the simplicity speaks for itself.

## Etymology

A traditional saying with Daoist sensibility (related to *Zhuangzi* and 老子 *Tao Te Ching* — "大巧若拙" "great skill looks clumsy"). The wise person doesn't perform their wisdom — performance creates surface area for challenge, invites resistance, and demonstrates more about the performer than about the work. Truly skilled action looks ordinary.

This is also Linus Torvalds territory (the famous "good taste" example of removing the special case in a linked-list delete by using a pointer-to-pointer). It's Steve Jobs territory ("simplicity is the ultimate sophistication"). It's Carmack territory ("the right amount of complexity for the actual problem, no more and no less"). It's Brooks territory ("conceptual integrity is the most important consideration in system design"). The chengyu names the discipline that all of these point at: wisdom that doesn't announce itself.

In agentic operation: the temptation is to demonstrate competence through complexity — show off the obscure library, write the clever one-liner, deploy the design pattern. Resist. The operator wants the problem solved, not the agent's intelligence on display.
