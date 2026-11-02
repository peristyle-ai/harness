## What

The context budget had one threshold. Past it, the harness dropped the OLDEST iterations of the turn out of the model's context, and the model carried on working with a trajectory it could no longer see -- fifty iterations, two hours, ending on a promise instead of an answer.

It now has two:

| threshold | value | what happens |
|---|---|---|
| `agent.context_window_tokens` | 600000 -> **800000** | the hard ceiling: past it, the oldest steps are dropped and the model is told its tail was truncated |
| `agent.context_wrap_tokens` | **600000** (new) | the soft threshold: at it, the model is told, in the system prompt, to WRAP THE TURN UP |

## What "wrap up" means

The notice spells it out, because a model told to "wrap up" will otherwise just say so and stop:

1. write (or update) a **pinned** memory carrying the handoff -- the goal, what is DONE and the evidence, what is NOT done, and the exact next step;
2. **report where it got to**, plainly, with the evidence;
3. end with an **ordered plan** for finishing the task, each step concrete enough to execute without re-reading the conversation.

A pinned memory is injected into every later prompt, including the next turn of this conversation. The context is not. That is the whole point: the handoff has to live somewhere the window cannot reach.

## Two details

- The wrap-up notice rides **last** in the system prompt, after the truncation notice -- it is the instruction the model has to act on now.
- A turn that was told to wrap up gets the **wrap-up nudge**, not the "you said you would do X, now do it" nudge. The intent nudge would push the model straight back into the work loop it was told to leave.

The model's thinking blocks are preserved on purpose: its own reasoning is the largest thing in the context, and for this model stripping it is not an option.

## The numbers

- `context_window_tokens` 600000 -> 800000. The model's own window is 1,000,000 tokens; the old ceiling tripped at ~600k, which is where the harness started dropping the oldest iterations of a long turn.
- The soft threshold sits below the hard one, so the wrap-up happens while there is still room to finish.

## Tests

18 new tests in `tests/test_context_wrapup.py`, 17 of them seen failing on the old code (the 18th is a guard, which is a guard):

- the soft threshold fires, and only at or above it;
- the notice asks for a pinned memory, a report, and a resume plan;
- the hard ceiling still drops the oldest steps -- the soft threshold does not weaken it;
- the wrap-up notice rides last in the system prompt;
- a wrapped-up turn gets the wrap-up nudge; a normal turn still gets the intent nudge and the standard ones;
- the shipped defaults are 800000 / 600000, and an explicit value is honoured;
- **the wiring**: the real agent loop over a fake adapter that remembers what each call was given -- the notice is not in the first call's system prompt and IS in the next one's.

A config object that predates the new key degrades to "no wrap-up notice" rather than failing a live turn.

## Not merged

This waits for a code-owner review like every other pull request here.
