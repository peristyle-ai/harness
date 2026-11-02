"""Sliding context window for a single agent turn.

WHY (2026-10-25): the agent loop replays the WHOLE turn on every model
call, so a long tool-heavy turn grows the request without bound. The
assistant's thinking blocks (~37 KB per response) and the tool results are
echoed back every iteration. On 2026-10-25 a turn reached ~1.4M
tokens in one request against MiniMax-M3.1-Flash's 1,000,000-token window
and the API rejected it:

    {"type":"error","error":{"type":"invalid_request_error",
     "message":"invalid params, context window exceeds limit (2013)"}}

THE MECHANISM: a sliding window over the live part of the turn. When the
live context exceeds the budget, the OLDEST iterations are dropped whole
(one iteration = the assistant message + its tool_result messages) and the
model is told, in the SYSTEM prompt, that its tail was truncated.

TWO THRESHOLDS (2026-11-02): the budget has a HARD ceiling (the ceiling
above -- past it, the oldest steps are dropped from under the model) and a
SOFT one (`wrap_tokens` -- at it, the model is told, in the SYSTEM prompt,
to wrap the turn up in an orderly way: write the handoff into a pinned
memory, report where it got to, and emit the plan for finishing the task,
so the next turn can resume it). The soft threshold exists because
dropping the tail is a lossy failure: the model keeps working with a
history it can no longer see. The soft threshold is a chance to stop on
purpose, with the work written down where it survives.

A group is always dropped WHOLE. Dropping a tool_result while keeping the
tool_use it answers (or the reverse) is a 400 from the API ("tool result's
tool id ... not found"), and keeping a tool_result whose tool_use is gone is
worse.

The dropped context is not lost, only moved out of the window: the notice
tells the model to recover the trajectory from its memory tools and from its
commits, which outlive the window.

Tool output is trimmed HEAD+TAIL, not head-only: the head of a command's
output is usually the command echo and the TAIL is the result or the error
message -- a head truncation would cut off exactly the part the model needs
to act. The FULL output still goes into the trajectory (the retraining
corpus); only what is sent to the model is trimmed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Rough average for English + code + JSON. Deliberately conservative
# (over-estimating is the safe direction: we drop a little early rather
# than trip the provider's limit).
CHARS_PER_TOKEN = 4


@dataclass
class Dropped:
    """What the window threw away, for the notice and the log."""
    groups: int = 0
    iterations: int = 0
    tokens: int = 0


class ContextWindow:
    """Keeps the live part of one turn inside a token budget.

    limit_tokens   the HARD budget for the live turn context (the history
                   that is replayed from the DB is bounded separately, by
                   turn count). Past it, the oldest iterations are dropped.
    wrap_tokens    the SOFT threshold. At it, the model is told to wrap
                   the turn up. 0 = never warn. If it is not below
                   limit_tokens, the hard ceiling bites first and the
                   truncation notice is the one that fires.
    head_chars     how much of a long tool output to keep at the front.
    tail_chars     how much to keep at the end.
    """

    def __init__(
        self,
        limit_tokens: int = 800_000,
        head_chars: int = 4_000,
        tail_chars: int = 4_000,
        chars_per_token: int = CHARS_PER_TOKEN,
        wrap_tokens: int = 600_000,
    ) -> None:
        self.limit_tokens = int(limit_tokens)
        self.wrap_tokens = int(wrap_tokens)
        self.head_chars = int(head_chars)
        self.tail_chars = int(tail_chars)
        self.chars_per_token = max(1, int(chars_per_token))

    # ---------------------------------------------------------------- sizing

    def estimate(self, text: str | None) -> int:
        if not text:
            return 0
        return (len(text) + self.chars_per_token - 1) // self.chars_per_token

    def content_text(self, content: Any) -> str:
        """Flatten a message content (str or a list of blocks) to text."""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    for key in ("text", "thinking", "content", "input"):
                        val = block.get(key)
                        if isinstance(val, str):
                            parts.append(val)
                        elif isinstance(val, dict):
                            parts.append(str(val))
                        elif val is not None:
                            parts.append(str(val))
                else:
                    parts.append(str(block))
            return "\n".join(parts)
        return str(content)

    def message_tokens(self, message: Any) -> int:
        content = getattr(message, "content", message)
        return self.estimate(self.content_text(content))

    def tokens_of(self, messages: list[Any]) -> int:
        return sum(self.message_tokens(m) for m in messages)

    # ------------------------------------------------------------- trimming

    def trim_text(self, text: str) -> str:
        """Head + tail, with an explicit elision marker in between."""
        keep = self.head_chars + self.tail_chars
        if len(text) <= keep + 200:
            return text
        head = text[: self.head_chars]
        tail = text[-self.tail_chars:] if self.tail_chars else ""
        elided = len(text) - len(head) - len(tail)
        marker = (
            f"\n\n... [{elided} characters elided by the harness context window "
            f"(head {self.head_chars} + tail {self.tail_chars} kept) ...]\n\n"
        )
        return head + marker + tail

    def trim_value(self, value: Any, _depth: int = 0) -> Any:
        """Trim long strings inside a tool result, recursively.

        The caller's own copy of the result is untouched -- the trajectory
        keeps the full output.
        """
        if _depth > 6:
            return value
        if isinstance(value, str):
            return self.trim_text(value)
        if isinstance(value, dict):
            return {k: self.trim_value(v, _depth + 1) for k, v in value.items()}
        if isinstance(value, list):
            return [self.trim_value(v, _depth + 1) for v in value]
        return value

    # ----------------------------------------------------------- enforcement

    def enforce(
        self,
        messages: list[Any],
        live_start: int,
        groups: list[tuple[int, int, int]],
        live_tokens: int,
    ) -> tuple[Dropped, int]:
        """Drop the oldest live groups until the live tail fits the budget.

        `groups` holds (start, end, tokens) index ranges into `messages`,
        one per iteration, and is MUTATED IN PLACE (the caller keeps using
        it). `live_tokens` is the running token count of the live tail.

        Returns (what was dropped, the new live token count).
        """
        dropped = Dropped()
        while groups and live_tokens > self.limit_tokens:
            start, end, group_tokens = groups.pop(0)
            if end <= start:
                continue
            size = end - start
            del messages[start:end]
            # every later group shifts left by `size`
            groups[:] = [(s - size, e - size, t) for (s, e, t) in groups]
            live_tokens -= group_tokens
            dropped.groups += 1
            dropped.iterations += 1
            dropped.tokens += group_tokens
        return dropped, live_tokens

    # --------------------------------------------------------------- notice

    def notice(self, dropped: Dropped) -> str:
        """The system-prompt text that tells the model its tail was cut."""
        if dropped.groups <= 0:
            return ""
        return (
            "## Context window: the tail of this turn was truncated\n"
            "\n"
            f"This turn grew past the model's context window. The {dropped.groups} "
            f"oldest step(s) of this turn -- about {dropped.tokens:,} tokens of "
            "tool output and your own reasoning -- have been dropped from your "
            "context. Everything before this point is no longer visible to you: "
            "you cannot see your earlier tool calls, their output, or the "
            "thinking you did to reach them.\n"
            "\n"
            "Your context no longer contains your own trajectory. Recover it from "
            "the things that outlive the window:\n"
            "\n"
            "- your MEMORY tools (memory_recall / memory_recent / memory_pin) -- "
            "pin anything you still need before it is dropped;\n"
            "- your COMMITS -- commit as you go, so the work is in `git log` and "
            "`git show` even after the context is trimmed;\n"
            "- files on disk -- write the state down (notes, plans, docs) rather "
            "than relying on having it in context.\n"
            "\n"
            "From here on, keep tool output SMALL: pipe commands through "
            "`head`/`tail`, grep for what you need instead of dumping files, and "
            "prefer a summary line over a full dump. That way the remaining tail "
            "of this turn survives to the end."
        )

    # ------------------------------------------------------------- wrap-up

    def wrap_notice(self, live_tokens: int) -> str:
        """The system-prompt text that tells the model to wrap the turn up.

        EMPTY below the soft threshold. This is the OTHER half of the
        budget: the hard ceiling drops the oldest steps from under the
        model, which is lossy -- the model keeps working with a history
        it can no longer see. The soft threshold gives it a chance to
        stop ON PURPOSE, with the work written down where it survives.

        What "wrap up" means is spelled out, because a model that is
        told to "wrap up" will otherwise just say so and stop:

          1. write (or update) a PINNED memory carrying the handoff --
             the goal, what is done and the evidence, what is not, and
             the exact next step. A pinned memory is injected into every
             later prompt, including the next turn of this
             conversation; the context is not;
          2. report where it got to, plainly, with the evidence;
          3. emit an ordered plan for finishing the task, each step
             concrete enough to execute without re-reading the
             conversation -- that plan is what the next turn resumes
             from.

        The token count in the text is rounded to 10k so the system
        prompt does not change on every iteration once the threshold is
        crossed.
        """
        if not self.wrap_tokens or live_tokens < self.wrap_tokens:
            return ""
        shown = int(round(live_tokens / 10_000.0) * 10_000)
        pct = int(round(100.0 * shown / max(1, self.limit_tokens)))
        return (
            "## Context budget: wrap this turn up\n"
            "\n"
            f"This turn has used about {shown:,} of your {self.limit_tokens:,}"
            f"-token context budget ({pct}%). The budget is finite, and "
            "your own reasoning is the largest thing in it: once it is "
            "spent, the harness drops the oldest steps of this turn out "
            "of your context, and you lose the trajectory you have built.\n"
            "\n"
            "Wrap up NOW, in this order:\n"
            "\n"
            "1. **Write (or update) a PINNED memory that carries the work "
            "forward.** Use `memory_write` with `pin=true`, or `memory_pin` "
            "on an existing memory. It must carry, in a form a fresh turn "
            "can act on without re-deriving anything: the goal; what is "
            "DONE, with the evidence (the file, the commit, the command "
            "output); what is NOT done; and the exact next step. A pinned "
            "memory is injected into every later prompt, including the "
            "next turn of this conversation. Your context is not.\n"
            "2. **Commit as you go** if the work is in a repository, so "
            "`git log` and `git show` hold the state even after the window "
            "moves.\n"
            "3. **Report where you got to.** Say plainly what is done, what "
            "is not, and the evidence for each. A report of work already "
            "done is an answer; a promise to do it later is not.\n"
            "4. **Emit the resume plan.** End with an ordered plan for "
            "finishing the task: the remaining steps, each concrete enough "
            "to execute without re-reading this conversation. That plan is "
            "what the next turn resumes from, so it has to stand on its "
            "own.\n"
            "\n"
            "Keep tool output small from here on -- grep for what you need, "
            "`head`/`tail` the rest, prefer a summary line over a dump -- so "
            "the rest of the turn fits."
        )
