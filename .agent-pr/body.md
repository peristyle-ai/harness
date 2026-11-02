## The bug

The "intent without execution" guard exists to catch the model that
describes an action it never performs ("Resending the invoice.") and
leaves it there as the answer. That failure mode is real and the guard
is worth keeping.

But the guard keyed on a bare action verb ANYWHERE in the text
(`_INTENT_RE`), and ordinary English is full of action verbs: "here is
where I GOT to", "read-only check", "you can FIND the invoice".

Measured against one production conversation's 89 assistant answers, the
old detector fired on **86 of the 89**.

## What it cost

On 2026-11-02 an agent was asked to stop working and report where it
had got to. It did exactly that, in twelve seconds:

    "I'm stopping. Here's exactly where I got to.

     ## Where I got to

     **Done and verified:** ..."

The guard matched the word "got", told the agent it had narrated an
action without performing it, and pushed it back into the work loop for
another 45 iterations and forty minutes.

## The fix

An announcement of work has a shape, and the guard now looks for the
shape instead of for a verb:

- a forward frame -- "let me check", "I'll send", "I'm going to
  render", "I need to look";
- or a bare progressive -- "Resending the invoice now.";

and it is the LAST thing the turn says (a promise in an earlier
sentence is the model thinking out loud, and the answer that follows it
is the answer), and it is neither negated ("I will NOT push") nor
gated on a consent-conditional ("say the word and I'll build it").

The claim guard ("PDF rendered (3,988 bytes)") now looks at the
CONVERSATION rather than only at the current turn: a claim of work
done is only a fabrication if the work was never done, and the
conversation's own tool output is the evidence. The memory API gains
`conversation_ran_tool(conversation_id, tool_name=None)` for it; the
loop asks for it dynamically, so a memory implementation without the
method degrades to the old turn-scoped behaviour instead of raising.

## The evidence

Against the same 89 production answers:

| detector | fires on |
|---|---|
| the old bare-verb detector | 86 of 89 |
| the new announcement detector | **3 of 89** -- and those three are exactly the turns that really did end on a promise |

## The tests

`tests/test_loop_announcement_vs_report.py` (new) drives the real agent
loop over a fake adapter -- no database, no model, no tool executed.

Seen failing on the old code first: 5 failed, 5 passed. The five that
failed are the ones that describe the new behaviour; the five that
passed are the guards that have to keep working.

`tests/test_loop_unfulfilled_promise.py` (the promise half) still
passes unchanged: 8 passed.

Full suite: 350 passed, 9 skipped, 3 pre-existing failures -- the
signature resolver and the commit-identity gate read this install's git
configuration and the local branch history, and fail identically on a
pristine `main`.
