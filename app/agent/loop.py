"""Agent loop — the orchestrator. Model-agnostic.

For each chat request:
1. resolve conversation (load or create)
2. save user turn
3. fetch permanent memory (collective cap)
4. compose messages: system -> bootstrap -> permanent -> history -> user
5. tool-use loop: call adapter.complete() up to MAX_TOOL_ITERATIONS,
   executing tool calls as they come, until the model produces text.
6. save assistant turn + chunk+embed both turns
7. return ChatResponse

Phase 8 hardening (2026-10-08):
- llm_semaphore caps MAX_CONCURRENT_LLM concurrent LLM calls.
- Per-tool counters enforce MAX_SEARCHES/PINS/UNPINS_PER_TURN.
- OpenAI round-trip: append the assistant's tool_calls message before
  each tool result so the model sees its own prior tool calls.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import threading
import time
import re
from queue import Empty, Queue
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from . import prompts as agent_prompts
from . import turn_guard
from .context_window import ContextWindow


# Soft-error nudge for the "model narrated a tool call in prose"
# failure mode. When the model returns text only (no tool_use blocks)
# but the text contains a recognisable tool-call-like pattern
# (e.g. 'memory_recall(' in markdown, or 'memory_id 18 (pinned)' as
# a hallucinated output), we append a synthetic user message
# nudging the model to re-issue as a real tool_use block. See
# the loop body for how this is used.
_NARRATE_RE = re.compile(
    # Tool name with paren: "memory_recall(", "memory_write(" etc.
    r"\b(memory_recall|memory_recent|memory_write|memory_update"
    r"|memory_pin|memory_unpin|sql_query|exec_bash)\s*\("
    # OR hallucinated tool output: model pretending a tool returned
    # something, without actually calling the tool. Catches the
    # 'memory_id 18 (pinned)' / 'chunk_id 42' / 'id=99' / 'bytes=...'
    # / 'source_table=' / 'importance=N' patterns.
    r"|\bmemory_id\s*=?\s*\d+"
    r"|\bchunk_id\s*=?\s*\d+"
    r"|\bsource_table\s*="
    r"|\bimportance\s*=?\s*\d+"
    r"|\b(?:returned|saved|pinned)\b"
)

# Phase 15.9 (2026-10-18): _INTENT_RE catches "intent without
# execution" - the model writes an action verb in prose
# ("resending", "let me check", "sending the email") without
# issuing a tool_use block. This is the new failure mode
# the model fell into after the narrate-detection was
# tightened: too cautious to claim, too cautious to act.
#
# The regex lists explicit verb forms (base + -s + -ing + -ed
# + irregular past tenses) so it matches "Resending",
# "saved", "I just sent the email", etc. Word boundaries
# (\b) prevent false positives like "ready" matching "read",
# "listen" matching "list", etc.
_INTENT_RE = re.compile(
    r"\b(?:resend|resends|resending|resent|send|sends|sending|sent|check|checks|checking|checked|verify|verifies|verifying|verified|query|queries|querying|queried|render|renders|rendering|rendered|compute|computes|computing|computed|find|finds|finding|found|fetch|fetches|fetching|fetched|search|searches|searching|searched|attach|attaches|attaching|attached|write|writes|writing|wrote|written|read|reads|reading|list|lists|listed|listing|count|counts|counted|counting|pull|pulls|pulling|pulled|push|pushes|pushing|pushed|build|builds|building|built|generate|generates|generating|generated|create|creates|created|creating|delete|deletes|deleted|deleting|update|updates|updated|updating|save|saves|saved|saving|post|posts|posted|posting|get|gets|got|getting|sent|built|found|wrote|written)\b",
    re.IGNORECASE | re.MULTILINE,
)

# Phase 15.9: intent-without-execution nudge message.
# Different from the existing narrate-nudges because the
# failure mode is different: the model said "I will do X"
# without actually doing X. The nudge is positive-framed:
# "describe AND execute".
_NUDGE_INTENT_MESSAGE = (
    "You described an action ('resending', 'sending', 'let me check', etc.) but you did not issue a `tool_use` content block in this turn. Describing the plan is not the same as executing it. The user (and the harness) cannot see your intent - they can only see the `tool_use` blocks. If you want to send the email, you must call the relevant tool (exec_bash with the mail-send script, attach_file with the PDF, etc.) as a `tool_use` block. Try again - this time, actually call the tool.",
)
_NUDGE_MESSAGE = (
    "Your last response did not include a structured `tool_use` content "
    "block, but it described a tool call in prose (or hallucinated a "
    "tool output). Please re-issue the call as a proper `tool_use` block "
    "in the `content` array. Inline text like `memory_recall(\"...\")` "
    "is NOT a tool call — the system cannot detect your intent. If you "
    "intended to call a tool, return a `tool_use` content block. If you "
    "did not, just answer the user's question directly without mentioning "
    "any tool."
)
_MAX_NUDGES = 4

# Phase 15.6 (2026-10-18): _CLAIM_RE catches prose-only side-effect
# claims that the original _NARRATE_RE missed. The model was writing
# "Email id 6808 sent", "PDF rendered", "memory written", "I'll write
# the memory now", "via memory_write", "Recap of what I did" in plain
# prose (no tool_use blocks), and the old detector only caught
# parseable tool-call syntax like memory_recall(args). Either regex
# matching triggers the narrate-detection branch.
_CLAIM_RE = re.compile(
        r"\b(?:email\s+id|id)[\s:=|`*~]*\*?`?\*?\d{3,}\*?`?\*?"
        r"|\bPDF[^\n]{0,60}\b\d{3,}\s*(?:bytes|kb|mb)\b"
        r"|\b(?:memory|chunk)\s+(?:id\s+)?\d{3,}\b"
        r"|\b\d{3,}\s*(?:bytes|kb|mb)\b"
        r"|\bvia\s+`?(?:memory_write|memory_recall|memory_recent|memory_delete|memory_update|memory_pin|memory_unpin|sql_query|exec_bash|attach_file|cat|less|head|tail|grep|awk|sed)\b"
        r"|\bvia\s+`?(?:mail_api|smtp|fetch|curl|aws|sendmail|postfix|ses|sendgrid|mailgun)\b"
        r"|\bvia\s+[a-z_][a-z0-9_]*\.py\s+\w+"
        r"|\b(?:I\x27ll|will|I\s+am\s+going\s+to)\s+(?:write|send|render|delete|update|save)\s+(?:the\s+|a\s+)?(?:memory|email|pdf|file|chunk|user|account|note|message)\b"
        r"|\bWriting\s+(?:the|this|a)\s+(?:memory|note|file|email|pdf|message)\s+now\b"
        r"|\bRecap\s+of\s+what\s+I\s+(?:actually\s+)?did\b"
        r"|\bVerified\s+end-to-end\b"
        r"|\bI\s+(?:just\s+)?(?:sent|saved|wrote|created|rendered|deleted|queried|read)\s+(?:it|the|a|that|this)\s+\S+.{0,40}\b(?:to|at|from|in|via)\s+\S+"
        r"|\bI\s+(?:just\s+)?queried\s+(?:the\s+)?(?:sample employee|payroll|pay_events|the sibling|peristyle|enp)"
        r"|\b(?:Queried|Sent|Rendered|Wrote|Read|Verified|Saved|Deleted|Updated|Created)\s+(?:pay_events|sample employee|payroll|payslip|the sibling|peristyle|enp)"
    "|\bfound\s+\d{1,}\s+(?:events?|rows?|hits?|results?|matches?|chunks?|memories?)\b, re.IGNORECASE | re.MULTILINE | re.VERBOSE"
)
# Phase 15.6: second-nudge message (stricter — explicitly calls out that
# prose claims of side effect are visible lies because the user can see
# which tool calls actually ran).
_NUDGE_MESSAGE_2 = (
    "Your response STILL contains no `tool_use` block but you are writing "
    "prose that claims a side effect ('Email id N sent', 'PDF rendered', "
    "'memory written', 'I\\'ll write the memory now', 'via memory_write', "
    "'Recap of what I did'). NONE of those side effects happened - the user "
    "can see exactly which tool calls ran, so a prose claim without an "
    "actual `tool_use` block is a hallucination. Re-issue the call as a "
    "real `tool_use` block in the same turn, OR do not mention the side "
    "effect at all. There is no third option."
)
# Map nudge index (1-based) to the message text. Index 1 = first nudge,
# index 1 = first, 2 = second, 3 = third, 4 = fourth (final). Any nudge past _MAX_NUDGES is suppressed.
_NUDGE_MESSAGES = {
    1: _NUDGE_MESSAGE,
    2: _NUDGE_MESSAGE_2,
    # Phase 15.10 (2026-10-18): 3rd and 4th nudges for the
    # intent-without-execution failure mode. After 2 nudges
    # the model is clearly stuck in a "describe, don't
    # execute" loop. The 3rd nudge says "one more chance";
    # the 4th says "this is your last chance". The escalation
    # is calibrated to break through the self-critique
    # conditioning without being abusive.
    3: (
        "You have one more chance. Your previous 2 responses "
        "described the action but did not issue a `tool_use` "
        "content block. The user (and the harness) cannot see "
        "your intent - they can only see the `tool_use` blocks "
        "that ran. If you want to do something, call the "
        "relevant tool NOW. If you do not call a tool, the "
        "turn will end with no useful work done."
    ),
    4: (
        "STOP talking about what you are going to do. CALL "
        "THE TOOL. This is your LAST chance - the next iter "
        "after this will end the turn. The user has been "
        "waiting. They want the email sent, the data "
        "retrieved, the file written. NOT another paragraph "
        "of self-critique. Issue a `tool_use` block in your "
        "very next response, or the turn ends in failure."
    ),
}
# --- why the turn ended (2026-10-27) ---------------------------------------
# A turn can end for several very different reasons, and the diagnostic
# the user sees MUST say which one happened. Until 2026-10-27 every
# end-of-turn path that produced no final text printed the SAME message
# -- "I wasn't able to finish reasoning within the allowed N tool
# iterations" -- so a turn that ended because the model returned a
# reasoning-only response (no text, no tool call) was reported as an
# iteration-cap hit, even when the cap had barely been touched. That
# misdiagnosis sent the operator hunting for the wrong bug: a pull
# request turn whose work was DONE reported "hit the 25-iteration cap"
# with 477 iterations still unused.
_END_ITERATION_CAP = "iteration_cap"    # the loop ran out of iterations
_END_WALL_CLOCK_CAP = "wall_clock_cap"  # the turn hit the wall-clock cap
_END_FINAL_ANSWER = "final_answer"      # the model produced an answer
_END_NO_ANSWER = "no_answer"            # the model ended with no answer

from .llm import BaseLLM
from .llm.base import Message
from .tools import ToolContext, all_tools as all_registered_tools, get as get_tool

log = logging.getLogger(__name__)


@dataclass
class ChatResponse:
    conversation_id: UUID
    assistant: str
    model: str
    latency_ms: int
    tool_iterations: int
    retrieved_chunk_ids: list[int]
    # Phase 15 (2026-10-18): list of file attachments produced by
    # the attach_file tool during this turn. Each entry is the
    # same shape used for user uploads (url, filename, content_type,
    # size). The streaming `attachment` events carry these
    # incrementally; the ChatResponse has the final canonical list
    # for the buffered back-compat path.
    attachments: list[dict] = None  # type: ignore[assignment]
    # Migration 011 (2026-10-19): full per-iteration trajectory
    # (assistant_text + tool_calls + tool_results per iter) for the
    # LLM retraining corpus. None if the turn was a no-tool
    # direct-answer turn (rare, but possible). The /chat endpoint
    # can return this in the response so callers building a
    # retraining dataset can read it without a separate /turns hit.
    trajectory: dict | None = None


def _resolve_upload_dir() -> Any:
    """Where uploaded files live, WITHOUT needing a Flask app context.

    Bug, 2026-10-22 15:33 (Mal): an agent's turn failed INSTANTLY with
    "stream ended without a final event" because the server logged
    "Working outside of application context". The NDJSON streaming path
    runs the agent loop in a daemon thread (api.py's `pump`), which has
    no Flask app context -- and this helper used to read
    `current_app.config["HARNESS_UPLOAD_DIR"]`. So EVERY message with an
    image attachment died instantly. It went unnoticed because the
    streaming path was dead code until 1de17cd (2026-10-21 21:14); the
    older buffered path ran the loop in the request thread, where
    `current_app` was available.

    Order of preference:
      1. the HARNESS_UPLOAD_DIR environment variable -- the same source
         create_app() uses, and it is a plain process-wide value;
      2. current_app, when we DO have a context (the buffered path);
      3. the default location create_app() would have used.
    """
    from pathlib import Path
    env_dir = os.environ.get("HARNESS_UPLOAD_DIR")
    if env_dir:
        return Path(env_dir)
    try:
        from flask import current_app
        return Path(current_app.config["HARNESS_UPLOAD_DIR"])
    except Exception:
        # app/agent/loop.py -> app/agent -> app -> <harness root>
        return Path(__file__).resolve().parent.parent.parent / "uploads"


class AgentLoop:
    def __init__(
        self,
        adapter: BaseLLM,
        memory: Any,                # MemoryAPI (default agent — back-compat)
        bootstrap: Any,             # BootstrapLoader (default agent — back-compat)
        config: Any,
        bootstrap_registry: Any = None,   # BootstrapRegistry — per-agent dispatch
        memory_registry: dict | None = None,  # {agent_name: MemoryAPI}
        adapter_registry: dict | None = None,  # {agent_name: BaseLLM} — per-agent LLM (2026-10-19)
    ) -> None:
        self.adapter = adapter
        # Default-agent back-compat (used when registries don't have the agent).
        self.memory = memory
        self.bootstrap = bootstrap
        self.config = config
        # Per-agent registries. If the caller doesn't pass them, fall back
        # to using the single default-agent `bootstrap` + `memory` for everyone
        # (preserves the pre-multi-agent behavior).
        self.bootstrap_registry = bootstrap_registry
        self.memory_registry: dict = memory_registry or {}
        # Per-agent LLM registry (added 2026-10-19 for a z.ai model test).
        # Missing agents fall through to self.adapter (the global LLM).
        self.adapter_registry: dict = adapter_registry or {}
        max_concurrent = getattr(config.agent, "max_concurrent_llm", 8)
        # threading.Semaphore works in sync code; the gunicorn worker has
        # 4 threads, so the cap is across those threads.
        self.llm_semaphore = threading.Semaphore(max_concurrent)
        log.info(
            "agent_loop: llm_semaphore cap=%d, registries=%s",
            max_concurrent,
            "per-agent" if bootstrap_registry or memory_registry else "default-only",
        )

    def _agent_bootstrap(self, agent_name: str) -> Any:
        """Look up the bootstrap loader for `agent_name`. Falls back to the
        default-agent loader (set in __init__) if the registry doesn't have
        this agent or no registry was provided.
        """
        if self.bootstrap_registry is not None:
            loader = self.bootstrap_registry.get(agent_name)
            if loader is not None:
                return loader
        return self.bootstrap

    def _agent_memory(self, agent_name: str) -> Any:
        """Look up the MemoryAPI for `agent_name`. Falls back to the
        default-agent memory if no registry entry.
        """
        mem = self.memory_registry.get(agent_name)
        if mem is not None:
            return mem
        return self.memory

    def _adapter_for(self, agent_name: str) -> BaseLLM:
        """Look up the LLM adapter for `agent_name`. Falls back to the
        global `self.adapter` if no registry entry.

        Added 2026-10-19 for the per-agent LLM dispatch (a z.ai
        test). Same shape as _agent_bootstrap + _agent_memory — the
        per-agent registry is opt-in, missing agents use the global.
        """
        return self.adapter_registry.get(agent_name, self.adapter)

    def _persist_partial_assistant_turn(
        self,
        *,
        agent_name: str,
        conv_id: Any,
        user_turn: Any,
        user_text: str,
        user_id: int | None,
        attachments: list[dict],
        emission_iters: list[dict],
        final_text: str | None,
        t0: float,
        agent_adapter: Any,
        tool_iters: int,
        retrieved_ids: list[int],
        request_id: str | None = None,
        embed: bool = True,
        error_message: str | None = None,
    ) -> None:
        """Best-effort: write a partial assistant turn to DB.

        Phase 16.5 (2026-10-20): called from the streaming generator's
        try/finally when the loop is being torn down (GeneratorExit on
        client reload, exception, normal exit before this fires). When
        the loop completed cleanly, the normal write_turn_assistant()
        call already ran and assistant_turn_persisted is True, so this
        is a no-op. When the loop was interrupted mid-iter, this persists
        whatever we have so the user can pick up on page refresh
        instead of losing the turn entirely.

        error_message (2026-10-25, per Mal): when the turn FAILED, the
        row carries the error the user sees ("⚠️ ...") AND the full
        trajectory, so the failed turn is debuggable from the turns
        table alone. It used to be written from the generator's finally
        block -- i.e. AFTER the error event had already been streamed
        to the browser -- so the browser's /turns/error POST (which
        carries no trajectory) won the race and left the failed turn as
        a stub with nothing but the error message. The failure path now
        writes this row BEFORE the error event goes out.

        Fallback text priority:
        1. final_text if set
        2. last iter's assistant_text (so the user sees what the model
           was about to say, marked as a disconnect)
        3. Generic "I got disconnected before producing any text"

        The trajectory JSONB captures the partial iter trace with
        `partial: True` so /admin/last-emission can show that this
        was an interrupted turn.
        """
        agent_memory = self._agent_memory(agent_name)

        # Compose the row's text. A FAILED turn shows the error the user
        # saw (the same "⚠️ ..." shape the browser's /turns/error POST
        # used to write) — the model's partial words stay in the
        # trajectory, where the debugging tools look for them.
        if error_message:
            final_text = f"⚠️ {error_message}"
        else:
            # Compose fallback text if final_text is None
            if final_text is None and emission_iters:
                for it in reversed(emission_iters):
                    t = it.get("assistant_text")
                    if t:
                        final_text = t.rstrip() + "\n\n_(disconnected mid-turn — please reply to continue)_"
                        break
            if final_text is None:
                final_text = "_I got disconnected before producing any text — please retry._"

        latency_ms = int((time.monotonic() - t0) * 1000)
        total_tool_calls = sum(
            len(it.get("tool_calls") or []) for it in emission_iters
        )
        trajectory = {
            "iterations": emission_iters,
            "total_iterations": len(emission_iters),
            "total_tool_calls": total_tool_calls,
            "tool_iterations": tool_iters,
            "latency_ms": latency_ms,
            "model": getattr(agent_adapter, "name", "unknown"),
            "partial": True,
            "request_id": request_id,
        }
        if error_message:
            # The failed turn carries the error AND the whole iter trace:
            # this is the row the debugging tools read.
            trajectory["error"] = {
                "message": error_message,
                "request_id": request_id,
                "failed": True,
            }
        try:
            assistant_turn = agent_memory.write_turn_assistant(
                conv_id, final_text,
                model=getattr(agent_adapter, "name", "unknown"),
                latency_ms=latency_ms,
                attachments=attachments,
                trajectory=trajectory,
            )
            if embed:
                agent_memory.write_turns(conv_id, user_turn, assistant_turn)
            log.info(
                "partial assistant turn persisted on disconnect: conv=%s model=%s iters=%d tool_calls=%d final_text_len=%d failed=%s",
                conv_id, getattr(agent_adapter, "name", "?"),
                len(emission_iters), total_tool_calls, len(final_text),
                bool(error_message),
            )
        except Exception as e:
            log.exception("failed to persist partial assistant turn: %s", e)

    def _complete_with_deltas(self, adapter, messages, tools):
        """Run adapter.complete() on a worker thread, yielding text deltas.

        complete() is synchronous, so an on_delta callback cannot `yield` from
        inside it. Running it on a thread and draining a queue is the same
        pump pattern app/routes/api.py already uses to feed the NDJSON
        generator, so it is a proven shape in this codebase rather than a new
        idea.

        Yields ("delta", text) zero or more times, then exactly one of
        ("done", LLMResponse) or ("error", exception).

        Adapters whose complete() has no on_delta parameter (minimax_m3,
        glm_5_3_flash, kimi_k3) are called without it and yield no deltas,
        so this does not break them.
        """
        try:
            supports_delta = "on_delta" in inspect.signature(adapter.complete).parameters
        except (TypeError, ValueError):
            supports_delta = False

        if not supports_delta:
            yield "done", adapter.complete(messages, tools=tools)
            return

        q: Queue = Queue()

        def _run() -> None:
            try:
                r = adapter.complete(messages, tools=tools,
                                     on_delta=lambda t: q.put(("delta", t)))
                q.put(("done", r))
            except BaseException as exc:   # noqa: BLE001 - re-raised in caller
                q.put(("error", exc))

        t = threading.Thread(target=_run, daemon=True, name="llm-stream")
        t.start()
        while True:
            try:
                kind, payload = q.get(timeout=1.0)
            except Empty:
                # No post for a second. If the thread is gone it died without
                # reporting; do not block the generator forever.
                if not t.is_alive():
                    raise RuntimeError("LLM worker thread exited without posting a result")
                continue
            yield kind, payload
            if kind != "delta":
                return

    @staticmethod
    def _one_line_thought(response: Any) -> str:
        """The model's one-line thought for this iteration.

        The model's visible text is the natural thought ("Let me pick up
        the thread and find out exactly where the tree is."). MiniMax
        often returns it EMPTY when it calls a tool, so fall back to the
        FIRST SENTENCE of the thinking block -- the model's reasoning in
        one line.
        """
        text = (getattr(response, "text", None) or "").strip()
        if text:
            return " ".join(text.split())[:400]
        for block in (getattr(response, "thinking_blocks", None) or []):
            t = block.get("thinking") if isinstance(block, dict) else None
            if not t:
                continue
            t = " ".join(str(t).split())
            m = re.match(r"^(.{20,300}?[.!?])(\s|$)", t)
            return (m.group(1) if m else t[:200]).strip()
        return ""

    @staticmethod
    def _thinking_text(response: Any, limit: int = 4000) -> str:
        """The model's thinking blocks as plain text, bounded.

        The adapters that speak the Anthropic extended-thinking spec
        (minimax_m31_flash) put the model's reasoning in
        `response.thinking_blocks` and leave `response.text` empty when
        there is nothing else. The loop does not show thinking to the
        user as a matter of course, but it MUST NOT throw it away when
        it is the only thing the model produced (2026-10-27).
        """
        parts: list[str] = []
        for block in (getattr(response, "thinking_blocks", None) or []):
            if isinstance(block, dict):
                t = block.get("thinking")
            else:
                t = getattr(block, "thinking", None)
            if not t:
                continue
            parts.append(str(t).strip())
        text = "\n\n".join(p for p in parts if p)
        if len(text) > limit:
            text = text[:limit].rstrip() + "\n[... truncated]"
        return text

    @classmethod
    def _no_answer_text(
        cls, response: Any, *, iterations_used: int, max_iters: int
    ) -> str:
        """The turn ended with no answer: the model returned neither
        text nor a tool call.

        This is the case that used to be misreported as an iteration-cap
        hit (2026-10-27): the minimax adapter drops thinking blocks from
        `.text`, so a response that carried only reasoning arrived with
        `text=None` and the loop's one-and-only diagnostic claimed the
        cap had been reached -- with hundreds of iterations unused.

        The message says what actually happened, how many iterations the
        turn used, and -- when the model produced any -- its last
        output, so the work is not thrown away.
        """
        used = f"{iterations_used} of the {max_iters} allowed tool iterations"
        thinking = cls._thinking_text(response) if response is not None else ""
        if thinking:
            return (
                "The model ended this turn without an answer: it returned "
                "no text and no tool call, so this was NOT an "
                f"iteration-cap problem ({used}). Its last output, which "
                "the adapter keeps out of the visible text, is below.\n\n"
                f"{thinking}"
            )
        return (
            "The model ended this turn without an answer: it returned no "
            "text and no tool call, so this was NOT an iteration-cap "
            f"problem ({used}). Ask me to continue and I will pick the "
            "thread up where it stopped."
        )

    @classmethod
    def _iteration_cap_text(
        cls, *, iterations_used: int, max_iters: int
    ) -> str:
        """The iteration cap was reached while the model was still making
        tool calls (CASE A).

        The wording names the cap and states how many iterations the turn
        actually used, so the diagnostic cannot be mistaken for the
        no-answer case.
        """
        return (
            f"I hit the tool-iteration cap: this turn used {iterations_used} "
            f"of the {max_iters} allowed tool iterations and the model was "
            "still making tool calls when it ran out. This usually means "
            "the model was being inefficient (too much reconnaissance) "
            "rather than that the task was too complex. Could you rephrase, "
            "simplify, or break the request into smaller steps?"
        )

    @staticmethod
    def _append_steering_message(messages: list, content: Any) -> bool:
        """Put a mid-turn user message into the live message list.

        If the last message is a user message carrying tool_result blocks,
        the steering text rides along in THAT message (a text block after
        the tool_result blocks) instead of becoming a second user message.
        Two reasons: the Anthropic-compatible endpoints merge consecutive
        user messages anyway, and a tool_result has to be the first block
        of its message -- keeping the text in the same message preserves
        that shape.

        Returns True when the message was appended.
        """
        if (
            messages
            and getattr(messages[-1], "role", None) == "user"
            and isinstance(messages[-1].content, list)
        ):
            blocks = messages[-1].content
            if any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in blocks
            ):
                if isinstance(content, str):
                    blocks.append({"type": "text", "text": content})
                elif isinstance(content, list):
                    blocks.extend(content)
                else:
                    blocks.append({"type": "text", "text": str(content)})
                return True
        messages.append(Message(role="user", content=content))
        return True

    @staticmethod
    def _enforce_context_window(
        ctx_window, messages, live_start, live_groups, live_tokens, system_base,
    ):
        """Keep the live part of the turn inside the context budget.

        Drops the OLDEST iterations (whole groups) when the live context is
        over budget and returns the notice for the system prompt ('' when
        nothing was dropped). The caller puts the notice into messages[0].

        Split out as a static method so the seam can be unit-tested without
        the DB + adapter stack (tests/test_context_window.py).
        """
        dropped, live_tokens = ctx_window.enforce(
            messages, live_start, live_groups, live_tokens)
        if not dropped.groups:
            return live_tokens, ""
        log.warning(
            "agent_loop: context window trimmed the tail of the turn: dropped "
            "%d iteration(s) (~%d tokens); live context now ~%d tokens of a "
            "%d budget",
            dropped.groups, dropped.tokens, live_tokens, ctx_window.limit_tokens,
        )
        return live_tokens, ctx_window.notice(dropped)

    def handle_chat_streaming(
        self,
        user_text: str,
        agent_name: str,
        user_id: int | None = None,
        attachments: list[dict] | None = None,
        reuse_user_turn: Any | None = None,
        request_id: str | None = None,
        conversation_id: UUID | None = None,
        history_override: list[dict] | None = None,
        trailing_override: list[dict] | None = None,
        skip_auto_title: bool = False,
        user_turn_trajectory: dict | None = None,
        protected_paths: tuple[str, ...] = (),
        max_tool_iterations: int | None = None,
        system_suffix: str | None = None,
    ):
        """Yields event dicts as the agent loop runs. The final event
        is `{"type": "final", "response": ChatResponse, ...}`. On
        fatal failure, the final event is `{"type": "error", "message": str}`.

        Event types yielded (in order):
          - iter_start      — model call starting for this iter
          - delta           — streamed text fragment arriving mid-call
                                (2026-10-21; incremental token forwarding)
          - iter_text       — model returned (text + tool call names)
          - tool_start      — tool call starting (name + args)
          - tool_end        — tool call done (name + result preview)
          - attachment      — Phase 15: attach_file produced an
                              attachment; payload is {iter, attachment:{...}}
                              with the same shape used in user uploads
                              (url, filename, content_type, size)
          - nudge           — narrate-with-syntax nudge appended
          - final           — loop finished; response is in 'response' key
                              + attachments list (Phase 15)
          - error           — fatal failure (final yield)

        This is the source of truth for the loop. The non-streaming
        `handle_chat()` is a thin wrapper that drives this generator
        and returns the final ChatResponse.

        Added 2026-10-17 to support long-running turns (Cloudflare
        origin read timeout is 125s; without streaming, any turn that
        takes longer than that gets a 524 from the client even though
        the harness did complete the work).

        Group-chat parameters (2026-10-23):
          conversation_id    — use THIS conversation instead of the
                               agent's own persistent (agent, user)
                               conversation. The group chat passes the
                               group's shared conversation id, so every
                               agent writes its turns into the same
                               conversation (in its own schema).
          history_override   — replace the DB-sourced history with this
                               list of {role, content} messages. The
                               group chat passes the shared transcript
                               (other agents' messages included).
          trailing_override  — messages appended AFTER the fresh user
                               message. The group chat uses it for the
                               replies the other mentioned agents gave
                               for THIS message, so the second agent
                               sees the first agent's answer.
          skip_auto_title    — don't auto-title the conversation from
                               the first user message (group
                               conversations are titled after the
                               group, not after a chat message).
          user_turn_trajectory — metadata stored on the USER turn.
                               The reverie turn uses it to mark the
                               turn as a reverie (so the nightly
                               activity check can tell a reverie turn
                               apart from a real one).
          protected_paths    — files this turn must not touch. The
                               reverie turn passes the protected
                               identity files (SOUL.md): the tool
                               layer refuses any command that
                               mentions them, so "user permission
                               only" is enforced, not just asked for.
          max_tool_iterations — cap the tool iterations of THIS turn
                               (the reverie passes a tighter cap so a
                               nightly session stays focused).
          system_suffix      — extra text appended to the SYSTEM
                               prompt. The reverie uses it to put the
                               "tonight is a maintenance turn, do not
                               continue earlier work" directive where it
                               actually binds: a user-role instruction
                               was read and ignored (the agent picked up
                               an unfinished task from the history
                               instead of doing the cleanup).
        """
        t0 = time.monotonic()

        # Per-agent dispatch (multi-agent since 2026-10-15).
        agent_memory = self._agent_memory(agent_name)
        agent_bootstrap = self._agent_bootstrap(agent_name)
        # Per-agent LLM (added 2026-10-19 for a z.ai model test).
        # Use this for every adapter call in this turn — complete(),
        # format_tool_result, extract_assistant_content, system_prompt lookup.
        agent_adapter = self._adapter_for(agent_name)

        # Entity-based refactor (2026-10-17): one persistent conversation
        # per (agent, user). No conversation_id needed from the caller.
        # Group chats (2026-10-23) pass their own shared conversation id.
        if conversation_id is not None:
            conv_id = conversation_id
        else:
            conv_id = agent_memory.get_or_create_persistent_conversation(
                agent_name, user_id=user_id,
            )
        # reuse_user_turn (2026-10-22): re-run a turn that is ALREADY in
        # the table. Used to redo a turn that was lost to a server
        # restart without writing a second user row -- a second row
        # would duplicate the message in the conversation and in the
        # prompt. The prompt built below is identical to the one the
        # original attempt saw: load_recent_turns() already includes the
        # persisted user turn, exactly as it did when it was written.
        if reuse_user_turn is not None:
            user_turn = reuse_user_turn
            user_text = user_turn.content or user_text
            attachments = list(user_turn.attachments or attachments or [])
            log.info(
                "reusing existing user turn: conv=%s turn_id=%s (no new user row)",
                conv_id, getattr(user_turn, "id", "?"),
            )
        else:
            user_turn = agent_memory.write_turn_user(
                conv_id, user_text, attachments=attachments,
                trajectory=user_turn_trajectory,
            )

        # Build message stream.
        messages = self._compose_messages(
            conv_id, agent_name, user_turn, agent_memory, agent_bootstrap,
            history_override=history_override,
            trailing_override=trailing_override,
            system_suffix=system_suffix,
        )
        # --- Sliding context window (2026-10-25) ---------------------------
        # The loop replays the WHOLE turn on every model call, so a long
        # tool-heavy turn grows the request without bound: the assistant's
        # thinking blocks (~37 KB per response) and the tool results are
        # echoed back every iteration. On 2026-10-25 a turn reached
        # ~1.4M tokens in a single request against MiniMax-M3.1-Flash's
        # 1,000,000-token window and the API answered
        #   400 invalid params, context window exceeds limit (2013)
        #
        # The window keeps the live part of the turn inside a budget by
        # dropping the OLDEST iterations (a sliding window) and telling the
        # model, in the SYSTEM prompt, that its tail was truncated -- so it
        # recovers the trajectory from its memory tools and its commits
        # instead of from the dropped context.
        ctx_window = ContextWindow(
            limit_tokens=self.config.agent.context_window_tokens,
            head_chars=self.config.agent.tool_output_head_chars,
            tail_chars=self.config.agent.tool_output_tail_chars,
        )
        # Everything from here on is the LIVE turn (the messages above are
        # the system prompt + the replayed history, bounded separately).
        live_start = len(messages)
        # One droppable group per iteration: (start, end, tokens) into
        # `messages`. A group is dropped WHOLE, so a tool_result never
        # loses the tool_use it answers (an orphaned tool_result is a 400:
        # "tool result's tool id ... not found").
        live_groups: list[tuple[int, int, int]] = []
        live_tokens = 0
        system_base = messages[0].content if messages else ""
        window_notice = ""
        # Wall-clock cap for one turn (2026-10-25, per Mal: big turns are
        # fine, but a single turn must not run forever).
        max_turn_seconds = int(self.config.agent.max_turn_seconds or 0)
        # The thought stream is NOT persisted as turns (2026-10-25, per
        # Mal: "the thought streams did come through, but as separate turns
        # which has consumed the chat history in the webchat and also
        # polluted the agent's turns table with erroneous turns").
        #
        # It used to be written as one row per iteration (model=
        # 'thought-stream'), which put 132 rows into one turn's history
        # and ate the 50-row history window. The narration now lives in
        # TWO places, neither of them a turn:
        #   * the live stream (the 'thought' event below) -> the web chat
        #     renders it as a COLLAPSIBLE section, like the tool-call
        #     summary, and it is gone after a refresh (like the iter
        #     summary);
        #   * the turn's own trajectory (iterations[].assistant_text) ->
        #     the trail survives in the database for an inspection, even
        #     for a turn that never finished.
        # So nothing is lost, and the turns table holds only real turns.

        # Tool-use loop with per-tool counters and rate caps.
        ctx = ToolContext(
            agent_name=agent_name,
            conversation_id=conv_id,
            user_id=user_id,
            memory=agent_memory,
            protected_paths=tuple(protected_paths or ()),
        )
        tools = all_registered_tools()
        max_iters = int(max_tool_iterations or 0) or self.config.agent.max_tool_iterations
        max_searches = self.config.agent.max_searches_per_turn
        max_pins = self.config.agent.max_pins_per_turn
        max_unpins = self.config.agent.max_unpins_per_turn
        tool_iters = 0
        # The number of model calls this turn has made. `tool_iters` is
        # the INDEX of the current iteration (it is incremented at the
        # END of an iteration), so it is one behind the number of calls
        # made while the turn is running. The end-of-turn diagnostic
        # needs the real count, so keep it (2026-10-27).
        llm_calls = 0
        # Phase 16.5: set True once the assistant turn is persisted on the
        # clean path. The finally block below reads it to decide whether to
        # write a partial turn. It was referenced but NEVER assigned, so
        # every completed turn raised NameError during generator teardown --
        # the turn persisted, then the request 500'd. Fixed 2026-10-21.
        assistant_turn_persisted = False
        final_text: str | None = None
        # Why the turn ended. Set once, read after the loop, so the
        # diagnostic can tell the operator WHICH end this was (2026-10-27).
        # The default is the iteration cap: that is what the while
        # condition below does when it stops the loop.
        end_reason = _END_ITERATION_CAP
        retrieved_ids: list[int] = []
        # Phase 15 (2026-10-18): collect attach_file tool results so
        # the assistant turn + final event can carry the attachment
        # list. The streaming layer also yields `attachment` events
        # for each entry as the tool completes so the live UI bubble
        # can render the icon immediately.
        attachments: list[dict] = []
        searches_used = 0
        pins_used = 0
        unpins_used = 0
        emission_iters: list[dict] = []
        # In-flight turn registration (2026-10-22). The SIGTERM handler
        # (app/agent/turn_guard.py) uses this registry to write an
        # "interrupted" turn before the process goes away. Without it a
        # server restart mid-turn leaves a user turn with no reply and
        # no trace -- exactly what happened to Mal's message on
        # 2026-10-21 21:12.
        turn_state = turn_guard.TurnState(
            conv_id=conv_id,
            agent_name=agent_name,
            user_id=user_id,
            user_turn=user_turn,
            user_text=user_text,
            attachments=list(getattr(user_turn, "attachments", None) or []),
            t0=t0,
            model=getattr(agent_adapter, "name", "unknown"),
            request_id=request_id,
            started_at=time.time(),
            emission_iters=emission_iters,
        )
        turn_state._persist = lambda reason: self._persist_interrupted_turn(
            turn_state, reason
        )
        turn_guard.register(turn_state)
        try:
            while tool_iters < max_iters:
                # Wall-clock cap for one turn (2026-10-25, per Mal). The
                # iteration cap (500) is the primary guard; this is the
                # backstop for a turn that is slow rather than long.
                if max_turn_seconds and (time.monotonic() - t0) > max_turn_seconds:
                    log.warning(
                        "agent_loop: turn hit the %ds wall-clock cap after %d "
                        "iterations", max_turn_seconds, tool_iters,
                    )
                    final_text = (
                        f"I hit the {max_turn_seconds // 3600}h wall-clock cap for "
                        f"a single turn after {tool_iters} tool iterations. "
                        f"Everything I did so far is in my memory and in my "
                        f"commits -- ask me to continue and I will pick it up "
                        f"in a fresh turn."
                    )
                    end_reason = _END_WALL_CLOCK_CAP
                    turn_state.set_progress(final_text=final_text)
                    break
                # Capture the raw prompt before each LLM call so the admin
                # /api/v1/admin/last-prompt route can show what the model
                # actually saw. Updated every iteration — the latest call
                # (the one that produced final_text, or the last attempt
                # before max_iters) is what the operator wants to inspect.
                try:
                    from . import last_prompt as _lp  # local import: avoid cycle
                    _lp.set(
                        conversation_id=str(conv_id),
                        user_id=user_id,
                        agent_name=agent_name,
                        model=agent_adapter.name,
                        adapter_id=getattr(self.adapter, "adapter_id", self.adapter.name),
                        tool_iterations=tool_iters,
                        messages=messages,
                        tools=tools,
                    )
                except Exception as _e:
                    log.warning("last_prompt capture failed: %s", _e)

                # Yield iter_start so the client knows the model is being called.
                yield {"type": "iter_start", "iter": tool_iters, "model": self.adapter.name}

                # --- the thought stream: LIVE ONLY ----------------------------
                # The model's per-iteration narration is streamed to the
                # client as a 'thought' event (below) and rendered as a
                # collapsible section in the live bubble. It is NOT
                # written as a turn: a turn row per iteration put 132
                # rows into one turn's history (Mal, 2026-10-25 14:02).
                # The narration is still recorded -- in the turn's own
                # trajectory (iterations[].assistant_text), which the
                # emission record below carries -- so the trail survives a
                # turn that never finishes.

                # Cap concurrent LLM calls. Sync complete() respects the
                # semaphore because we acquire before calling.
                response = None
                with self.llm_semaphore:
                    for _kind, _payload in self._complete_with_deltas(
                            agent_adapter, messages, tools):
                        if _kind == "delta":
                            # Forward tokens as they arrive. Before this, a long
                            # model call produced NO events between iter_start
                            # and iter_text, so the browser sat on a static
                            # "thinking..." for minutes with no indication of
                            # progress.
                            yield {"type": "delta", "iter": tool_iters, "text": _payload}
                        elif _kind == "error":
                            raise _payload
                        else:
                            response = _payload
                if response is None:
                    raise RuntimeError("LLM stream ended without a response")
                llm_calls += 1
                log.info(
                    "agent_loop iter=%d model=%s finish=%s tools=%d",
                    tool_iters, self.adapter.name, response.finish_reason,
                    len(response.tool_calls),
                )
                # Build the per-iteration emission record. The same dict
                # is mutated as tool results come in, then appended to
                # emission_iters at the bottom of the iteration.
                iter_record: dict = {
                    "iteration": tool_iters,
                    "finish_reason": response.finish_reason,
                    "assistant_text": response.text,
                    "tool_calls": [
                        {"name": tc.name, "arguments": tc.arguments}
                        for tc in response.tool_calls
                    ],
                    "tool_results": [],
                    # Phase 16 (2026-10-20): capture the model's extended-
                    # thinking blocks so they flow through emission_iters
                    # into trajectory → turns.trajectory. Surfaced in
                    # /admin/last-emission for debugging the hallucination
                    # /fabrication failure mode (see memory 2026-10-20
                    # turn 279 -- model narrated work it didn't do;
                    # the thinking trail makes the contradiction visible).
                    # Adapters that don't produce them (M3, kimi_k3,
                    # glm_5_3_flash) leave this empty by default.
                    "thinking_blocks": list(getattr(response, "thinking_blocks", []) or []),
                }
                # Yield iter_text so the client sees any text the model
                # produced this iteration (e.g. "Let me think...").
                # The narration is LIVE ONLY: it goes out as a 'thought'
                # event and the client renders it in the collapsible
                # thought section. It is not a turn, so it never enters
                # the conversation history.
                thought = ""
                if response.tool_calls:
                    # Only an INTERIM iteration (one that called a tool) has
                    # a thought. An iteration without tool calls is the
                    # final answer -- its text is written as the assistant
                    # turn, and collecting it here would duplicate it.
                    thought = self._one_line_thought(response)
                if thought:
                    yield {
                        "type": "thought",
                        "iter": tool_iters,
                        "text": thought,
                    }
                yield {
                    "type": "iter_text",
                    "iter": tool_iters,
                    "text": response.text or "",
                    "finish": response.finish_reason,
                    "tool_call_names": [tc.name for tc in response.tool_calls],
                }
                if not response.tool_calls:
                    # Detect "narrate-with-syntax" failure mode: the model
                    # described a tool call in prose (e.g. markdown backticks,
                    # "Both memory_write calls returned id=42") without
                    # returning a proper tool_use block. If we see this, append
                    # a nudge and continue the loop so the model re-emits
                    # properly on the next iteration. Capped at _MAX_NUDGES
                    # so we don't loop forever.
                    text = response.text or ""
                    # Phase 15.6.2 (2026-10-18): context-aware narrate
                    # detection. Two distinct failure modes need two
                    # different guards:
                    #
                    # 1. URL-pattern narrate (Phase 15.6.2 new): the
                    #    model writes "/api/v1/uploads/<hex>.<ext>" in
                    #    prose without actually calling attach_file.
                    #    Detected by the URL pattern. The legitimate
                    #    case is "model just called attach_file in a
                    #    prior iter, now wrapping up the result" —
                    #    which is exactly when we want to suppress.
                    #    So the guard is: "no prior iter ran
                    #    attach_file" → narrate fires.
                    #
                    # 2. Other claim patterns (_NARRATE_RE, _CLAIM_RE):
                    #    "Email id N sent", "PDF (N bytes)", "memory N",
                    #    "via memory_write", "I sent the email to X",
                    #    etc. The legitimate case is "model just did
                    #    any tool and is wrapping up" — broad guard.
                    #    The Phase 15.6.1 fix used the broad guard,
                    #    which had a false-negative for the URL pattern
                    #    (an exec_bash in a prior iter would incorrectly
                    #    suppress the URL-pattern narrate).
                    #
                    # We split the logic: if the URL pattern matches,
                    # use the specific attach_file guard; otherwise use
                    # the broad guard. Both feed into the same
                    # `narrates` boolean downstream.
                    url_pattern = re.search(
                        r"/api/v1/uploads/[0-9a-f]{8,}\.\w+", text
                    )
                    if url_pattern:
                        prev_attach_file = any(
                            any(
                                r.get("name") == "attach_file"
                                for r in it.get("tool_results", [])
                            )
                            for it in emission_iters
                        )
                        narrates = not prev_attach_file
                    else:
                        # Phase 15.6.1 (2026-10-18): suppress narrate-
                        # detection when the model has just completed a
                        # successful tool call and is writing a wrap-up
                        # summary. The signal: did any previous iter
                        # land a tool result? (emission_iters contains
                        # one record per previous iter, each with a
                        # tool_results list.)
                        prev_successful_tool = any(
                            it.get("tool_results")
                            for it in emission_iters
                        )
                        narrates = (
                            not prev_successful_tool
                            and bool(
                                # Phase 15.6 (2026-10-18): also fire on
                                # the prose-only side-effect claim
                                # pattern (_CLAIM_RE). The original
                                # _NARRATE_RE only catches parseable
                                # tool-call syntax; the model was
                                # gaming it by writing confident prose
                                # without a tool_use block.
                                _NARRATE_RE.search(text) or _CLAIM_RE.search(text)
                            )
                        )
                    # Phase 15.9 (2026-10-18): also detect "intent without
                    # execution" - the model wrote an action verb in
                    # prose but didn't issue a tool_use block.
                    intent_matches = bool(_INTENT_RE.search(text))
                    nudges_so_far = sum(1 for it in emission_iters if it.get("_nudged"))
                    # Phase 15.6: pick which nudge text to send this time
                    # (1 = first nudge, 2 = second/stricter, beyond = suppress).
                    next_nudge_idx = nudges_so_far + 1
                    # Apply the same broad guard (Phase 15.6.1) to the
                    # intent check.
                    if intent_matches:
                        prev_successful_tool_intent = any(
                            it.get("tool_results") for it in emission_iters
                        )
                        intent_narrates = not prev_successful_tool_intent
                    else:
                        intent_narrates = False
                    # Combine: narrate fires if EITHER pattern matched.
                    narrates = narrates or intent_narrates
                    if narrates and nudges_so_far < _MAX_NUDGES:
                        log.warning(
                            "agent_loop: model narrated a tool call in prose "
                            "without returning tool_use blocks; nudging for retry. "
                            "Text snippet: %r",
                            text[:200],
                        )
                        iter_record["_nudged"] = True
                        # Phase 15.8 (2026-10-18): append iter_record to
                        # emission_iters BEFORE the continue, so the
                        # next iter's nudges_so_far calculation sees
                        # the prior nudge. Without this, the
                        # _MAX_NUDGES cap is never enforced — the
                        # abandoned iter_records mean nudges_so_far
                        # stays at 0 forever, and the model gets
                        # infinite narrate warnings.
                        # Repro: turn 201 (4:42 PM) saw 3 narrate
                        # warnings when the cap was supposed to be 2.
                        emission_iters.append(iter_record)
                        # Phase 15.9: use intent-specific nudge when
                        # intent_matches triggered the narrate. This
                        # gives the model a different signal: "you said
                        # you'd do X, now actually do X" (vs the claim
                        # pattern's "you said you did X, but you
                        # didn't"). Same max-nudge cap (Phase 15.8).
                        if intent_matches:
                            nudge_content = _NUDGE_INTENT_MESSAGE
                        else:
                            # Phase 15.6: pick which nudge text to send this time.
                            # First nudge is the standard 'use a real tool_use
                            # block' reminder; second nudge is the stricter
                            # 'prose claim of side effect is a visible lie'
                            # message. After _MAX_NUDGES, we fall through
                            # to 'take the text as the final answer'.
                            nudge_content = _NUDGE_MESSAGES.get(
                                next_nudge_idx, _NUDGE_MESSAGE
                            )
                        messages.append(Message(
                            role="user",
                            content=nudge_content,
                        ))
                        yield {"type": "nudge", "iter": tool_iters}
                        tool_iters += 1
                        continue
                    # No tool calls AND either no narrate pattern or we've
                    # already nudged _MAX_NUDGES times. Take the text as
                    # the final answer.
                    final_text = response.text
                    if final_text:
                        end_reason = _END_FINAL_ANSWER
                    else:
                        # CASE B (2026-10-27): the model ended the turn
                        # with no text and no tool call. The minimax
                        # adapter drops thinking blocks from .text, so a
                        # reasoning-only response arrives here with
                        # text=None -- and the old code then reported an
                        # iteration-cap hit that never happened. Do NOT
                        # discard the model's last output: use it, or say
                        # plainly that the turn ended without an answer.
                        end_reason = _END_NO_ANSWER
                        final_text = self._no_answer_text(
                            response, iterations_used=llm_calls,
                            max_iters=max_iters,
                        )
                        log.warning(
                            "agent_loop: the model ended the turn with no "
                            "answer (no text, no tool call) after %d/%d "
                            "iterations",
                            llm_calls, max_iters,
                        )
                    emission_iters.append(iter_record)
                    turn_state.set_progress(final_text=final_text)
                    break

                # OpenAI round-trip: add the assistant's prior tool_calls
                # message before the tool result so the model sees its own
                # previous tool calls. Anthropic-style adapters pass through
                # the content-block list.
                #
                # Three cases to distinguish (Phase 15.2, 2026-10-18):
                #   1. Real tool_use blocks in asst_payload → append as-is
                #      (the model emitted proper Anthropic content blocks).
                #   2. Synthesised tool calls (response.tool_calls non-empty)
                #      but no tool_use blocks in asst_payload → build
                #      synthetic content with proper tool_use blocks. This
                #      is the tool-call-repair-layer path: the model
                #      narrated a tool call in prose and the repair layer
                #      recovered it with a synthetic id like "repaired_…".
                #      Appending asst_payload (text-only) here would cause
                #      the next-iteration tool_result to reference a
                #      tool_use_id the API never saw, and the API would
                #      return 400 with "tool result's tool id ... not found
                #      (2013)".
                #   3. Pure text (no tool calls, no tool_use) → append as-is
                #      or skip if asst_payload is empty.
                asst_payload = None
                # The start of this iteration's droppable group: the
                # assistant message and its tool_result messages.
                group_start = len(messages)
                if hasattr(agent_adapter, "extract_assistant_content"):
                    asst_payload = agent_adapter.extract_assistant_content(response)
                asst_payload_has_tool_use = bool(asst_payload) and any(
                    b.get("type") == "tool_use" for b in asst_payload
                )
                if (
                    asst_payload_has_tool_use
                    and not self._has_assistant_payload(messages, asst_payload)
                ):
                    messages.append(Message(role="assistant", content=asst_payload))
                elif response.tool_calls and not asst_payload_has_tool_use:
                    # Synthetic fallback (case 2): build a proper
                    # tool_use content block list so the next-iteration
                    # tool_result has a matching tool_use to anchor to.
                    # The original buggy code only reached this branch
                    # when asst_payload was falsy (which never happened
                    # in practice for successful responses); the
                    # asst_payload_has_tool_use gate now makes it fire
                    # correctly for the repair-layer path while still
                    # matching the original behaviour for the
                    # "asst_payload is None" case.
                    synthetic_content = []
                    for call in response.tool_calls:
                        synthetic_content.append({
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": call.arguments,
                        })
                    if response.text:
                        synthetic_content.insert(0, {
                            "type": "text",
                            "text": response.text,
                        })
                    messages.append(Message(role="assistant", content=synthetic_content))
                elif asst_payload and not self._has_assistant_payload(messages, asst_payload):
                    # Plain text response (case 3): just preserve what
                    # the model emitted. No tool_use, no tool calls.
                    messages.append(Message(role="assistant", content=asst_payload))

                for call in response.tool_calls:
                    # Per-tool rate limits (Phase 8).
                    if call.name in ("memory_recall", "memory_recent") and searches_used >= max_searches:
                        result = {
                            "error": f"max searches per turn ({max_searches}) reached",
                            "count": 0,
                        }
                    elif call.name == "memory_pin" and pins_used >= max_pins:
                        result = {
                            "error": f"max pins per turn ({max_pins}) reached",
                            "memory_id": call.arguments.get("memory_id"),
                            "permanent": False,
                        }
                    elif call.name == "memory_unpin" and unpins_used >= max_unpins:
                        result = {
                            "error": f"max unpins per turn ({max_unpins}) reached",
                            "memory_id": call.arguments.get("memory_id"),
                            "permanent": True,
                        }
                    else:
                        tool = get_tool(call.name)
                        if tool is None:
                            result = {"error": f"Unknown tool: {call.name}"}
                        else:
                            try:
                                result = tool["run"](call.arguments, ctx)
                                if inspect.isawaitable(result):
                                    # Tool `run` is `async def` (Protocol
                                    # contract). The agent loop is sync, so
                                    # drive the coroutine to completion here.
                                    # Memory methods themselves are sync; this
                                    # is just protocol-shape bookkeeping.
                                    result = asyncio.run(result)
                            except Exception as e:
                                log.exception("tool %s raised: %s", call.name, e)
                                result = {"error": f"{type(e).__name__}: {e}"}

                    if call.name in ("memory_recall", "memory_recent"):
                        searches_used += 1
                    elif call.name == "memory_pin":
                        pins_used += 1
                    elif call.name == "memory_unpin":
                        unpins_used += 1

                    if call.name in ("memory_recall", "memory_recent") and isinstance(result, dict):
                        for c in result.get("chunks", []):
                            if isinstance(c, dict) and "id" in c:
                                retrieved_ids.append(c["id"])
                    # Trim the output for the MODEL (head+tail -- the tail
                    # carries the result and the error message, which is
                    # exactly what a head-only truncation would throw
                    # away). The trajectory below keeps the FULL result, so
                    # the retraining corpus and the audit trail are
                    # unaffected.
                    model_result = ctx_window.trim_value(result)
                    messages.append(agent_adapter.format_tool_result(call, model_result))
                    iter_record["tool_results"].append({
                        "name": call.name,
                        "arguments": call.arguments,
                        "result": result,
                    })
                    # Yield tool_start + tool_end so the client can show
                    # "memory_recall running..." in real time AND so the
                    # connection stays alive (Cloudflare won't time out
                    # when data is flowing). This is the core of the
                    # 524 fix: data flushes every 5-15s instead of
                    # buffering for the full turn.
                    preview = str(result)[:200] if result is not None else ""
                    yield {
                        "type": "tool_start",
                        "iter": tool_iters,
                        "name": call.name,
                        "args": call.arguments,
                    }
                    yield {
                        "type": "tool_end",
                        "iter": tool_iters,
                        "name": call.name,
                        "result_preview": preview,
                        "ok": not (isinstance(result, dict) and "error" in result),
                    }
                    # Phase 15 (2026-10-18): attach_file returns a
                    # dict shaped {"ok": True, "attachment": {...}}.
                    # Collect the attachment so it ends up on the
                    # assistant turn + final event, and emit a
                    # dedicated `attachment` streaming event so the
                    # live UI bubble can show the icon immediately
                    # without waiting for the run to finish.
                    if (
                        call.name == "attach_file"
                        and isinstance(result, dict)
                        and result.get("ok")
                        and isinstance(result.get("attachment"), dict)
                    ):
                        att = dict(result["attachment"])  # copy
                        attachments.append(att)
                        yield {"type": "attachment", "iter": tool_iters, "attachment": att}
                emission_iters.append(iter_record)
                # --- sliding window: keep the live turn inside the budget --
                # This iteration is now complete: its assistant message and
                # its tool_result messages form ONE droppable group. If the
                # live context is over budget, the OLDEST groups go (whole
                # groups, so no tool_result is ever orphaned), and the model
                # is told in the system prompt that its tail was truncated.
                if len(messages) > group_start:
                    group_tokens = ctx_window.tokens_of(messages[group_start:])
                    live_groups.append((group_start, len(messages), group_tokens))
                    live_tokens += group_tokens
                    live_tokens, notice = self._enforce_context_window(
                        ctx_window, messages, live_start, live_groups,
                        live_tokens, system_base,
                    )
                    if notice and notice != window_notice:
                        window_notice = notice
                        messages[0] = Message(
                            role="system",
                            content=f"{system_base}\n\n{window_notice}",
                        )
                # --- mid-turn steering (2026-10-25, per Mal) --------------
                # A user message that arrived while this turn was running
                # is injected HERE, at the iteration boundary, so the model
                # sees it on its NEXT call -- inside the same turn. This is
                # OpenClaw's `steer()` behaviour: no second turn, no
                # waiting for this one to finish.
                for steer_msg in turn_state.drain_steering():
                    steer_content = self._content_with_attachments_vision(
                        steer_msg.get("text") or "",
                        steer_msg.get("attachments") or [],
                    )
                    if self._append_steering_message(messages, steer_content):
                        log.info(
                            "agent_loop: injected a mid-turn steering message "
                            "(iter=%d)", tool_iters,
                        )
                        # The window must know about the new bytes.
                        live_tokens += ctx_window.tokens_of([messages[-1]])
                tool_iters += 1

            if final_text is None:
                # The loop ended without an answer. CASE A: the while
                # condition stopped the loop, i.e. the iteration cap was
                # reached while the model was still making tool calls.
                # The other way a turn can end with no answer -- the
                # model returning a reasoning-only response -- is handled
                # inside the loop above (_END_NO_ANSWER), so it never
                # reaches here and never claims a cap hit that did not
                # happen.
                # 2026-10-17: hit at 25 iters on a 4-step payroll task
                # where the model made 21+ recon calls before completing.
                if end_reason == _END_ITERATION_CAP:
                    final_text = self._iteration_cap_text(
                        iterations_used=llm_calls, max_iters=max_iters,
                    )
                else:
                    # Defensive: every other end reason sets final_text
                    # before it breaks. If one ever stops doing that,
                    # say what happened rather than blame the cap.
                    final_text = self._no_answer_text(
                        None, iterations_used=llm_calls, max_iters=max_iters,
                    )
                turn_state.set_progress(final_text=final_text)
            log.info(
                "agent_loop: turn ended (reason=%s, iters=%d/%d)",
                end_reason, llm_calls, max_iters,
            )

            latency_ms = int((time.monotonic() - t0) * 1000)
            # Flush the captured thought stream to the last_emission
            # slot for this agent. Best-effort: a failure here must NOT
            # block the chat from completing.
            try:
                from . import last_emission as _le  # local import: avoid cycle
                _le.set(
                    agent_name=agent_name,
                    user_text=user_text,
                    conversation_id=str(conv_id),
                    user_id=user_id,
                    model=agent_adapter.name,
                    adapter_id=getattr(self.adapter, "adapter_id", self.adapter.name),
                    iterations=emission_iters,
                    final_text=final_text,
                    retrieved_chunk_ids=retrieved_ids,
                    tool_iterations=tool_iters,
                    latency_ms=latency_ms,
                    # Phase 16 (2026-10-20): include the file
                    # attachments produced by attach_file + the
                    # configured cap so the admin view can show the
                    # full picture without a Postgres round-trip and
                    # flag the "model fabricated the canned failure
                    # text" failure mode (turn said "40 tool
                    # iterations" while the cap is 500).
                    attachments=attachments,
                    max_iters=max_iters,
                )
            except Exception as _e:
                log.warning("last_emission capture failed: %s", _e)

            # Build the per-iteration trajectory for the LLM-retraining
            # corpus. Migration 011 (2026-10-19) added a `trajectory`
            # JSONB column to <schema>.turns so this is persisted, not
            # just held in the volatile last_emission slot. The shape
            # mirrors last_emission.py: a list of iteration records
            # (assistant_text + tool_calls + tool_results + nudge
            # flags) plus the aggregate counters. The export.jsonl
            # endpoint reads this column for the format=full export.
            trajectory = {
                "iterations": emission_iters,
                "total_iterations": len(emission_iters),
                "total_tool_calls": sum(
                    len(it.get("tool_calls") or []) for it in emission_iters
                ),
                "tool_iterations": tool_iters,
                "latency_ms": latency_ms,
                "model": self.adapter.name,
                "request_id": request_id,
            }

            # If the server was restarted under this turn, the SIGTERM
            # handler already wrote an "interrupted" marker row for it.
            # Update that row in place with the real answer rather than
            # leaving two assistant turns in the conversation.
            interrupted_id = turn_state.interrupted_id()
            if interrupted_id is not None:
                log.info(
                    "updating interrupted turn in place: conv=%s turn_id=%s",
                    conv_id, interrupted_id,
                )
                assistant_turn = agent_memory.update_turn_content(
                    interrupted_id, final_text,
                    model=agent_adapter.name, latency_ms=latency_ms,
                    attachments=attachments, trajectory=trajectory,
                )
            else:
                assistant_turn = agent_memory.write_turn_assistant(
                    conv_id, final_text, model=agent_adapter.name, latency_ms=latency_ms,
                    attachments=attachments,
                    trajectory=trajectory,
                )
            agent_memory.write_turns(conv_id, user_turn, assistant_turn)

            # Auto-title on first user message in a new persistent conversation.
            # Group conversations (2026-10-23) keep the group name as their
            # title, so the auto-title is skipped for them.
            try:
                with self.memory.pool.connection() as _c, _c.cursor() as _cur:
                    _cur.execute(
                        f"SELECT COUNT(*) FROM {agent_name}.turns WHERE conversation_id = %s",
                        (conv_id,),
                    )
                    _tcount = _cur.fetchone()[0]
                if _tcount <= 2 and not skip_auto_title:
                    title = (user_text or "New chat")[:60].strip() or "New chat"
                    agent_memory.set_conversation_title(conv_id, title)
            except Exception as e:
                log.warning("set_conversation_title (entity conv) failed: %s", e)

            # Clean path: constructing ChatResponse persists the assistant
            # turn, so the finally block must not write a duplicate.
            assistant_turn_persisted = True
            yield {
                "type": "final",
                "response": ChatResponse(
                    conversation_id=conv_id,
                    assistant=final_text,
                    model=agent_adapter.name,
                    latency_ms=latency_ms,
                    tool_iterations=tool_iters,
                    retrieved_chunk_ids=retrieved_ids,
                    attachments=list(attachments),
                    trajectory=trajectory,
                ),
                "tool_iterations": tool_iters,
                "latency_ms": latency_ms,
                "model": self.adapter.name,
                # Phase 15 (2026-10-18): the assistant turn's
                # attachments. The frontend renders these as 📎 icons
                # on the assistant bubble (matches the user-side
                # attachments rendering). Surfaced here so the live
                # streaming path AND the buffered /last-emission path
                # both see the same data.
                "attachments": attachments,
            }
        except Exception as e:
            log.exception("handle_chat_streaming fatal: %s", e)
            # Persist the FAILED turn NOW — before the error event goes out
            # to the browser (2026-10-25, per Mal: "the jsonb column did
            # not get populated for the failed turn, just the error
            # message"). The finally block below writes this row too, but
            # it runs at generator teardown — AFTER the response has been
            # flushed — so the browser's /turns/error POST (which carries
            # NO trajectory) used to win the race and leave the failed
            # turn as a stub. Writing it here means the row with the full
            # trajectory exists before the client can post anything, and
            # the client's POST then no-ops ("already answered").
            try:
                self._persist_partial_assistant_turn(
                    agent_name=agent_name,
                    conv_id=conv_id,
                    user_turn=user_turn,
                    user_text=user_text,
                    user_id=user_id,
                    attachments=attachments,
                    emission_iters=emission_iters,
                    final_text=final_text,
                    t0=t0,
                    agent_adapter=agent_adapter,
                    tool_iters=tool_iters,
                    retrieved_ids=retrieved_ids,
                    request_id=request_id,
                    embed=False,
                    error_message=f"{type(e).__name__}: {e}",
                )
                assistant_turn_persisted = True
            except Exception as pe:
                log.exception("failed to persist the failed turn: %s", pe)
            yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
        finally:
            # Phase 16.5 (2026-10-20): persist partial assistant turn on
            # client disconnect (GeneratorExit raised at any yield point).
            # Runs for ALL exit paths — return, exception, GeneratorExit.
            # When the loop completed cleanly, assistant_turn_persisted
            # is True and this is a no-op. When the loop was interrupted
            # mid-iter (the common case after a browser reload while
            # the agent's thinking), this writes whatever trajectory /
            # final_text we accumulated so the user can pick up on
            # page refresh instead of losing the turn entirely.
            if (not assistant_turn_persisted
                    and turn_state.interrupted_id() is None
                    and not turn_guard.interrupt_already_written(
                        conv_id, request_id
                    )):
                self._persist_partial_assistant_turn(
                    agent_name=agent_name,
                    conv_id=conv_id,
                    user_turn=user_turn,
                    user_text=user_text,
                    user_id=user_id,
                    attachments=attachments,
                    emission_iters=emission_iters,
                    final_text=final_text,
                    t0=t0,
                    agent_adapter=agent_adapter,
                    tool_iters=tool_iters,
                    retrieved_ids=retrieved_ids,
                    request_id=request_id,
                )
            # The turn is over either way -- drop it from the in-flight
            # registry so a later SIGTERM does not try to mark it again.
            turn_guard.unregister(conv_id)

    def _persist_interrupted_turn(self, state: Any, reason: str) -> int | None:
        """Write an "interrupted" assistant turn for a turn that is being
        torn down by a server shutdown.

        Called from the SIGTERM handler (app/agent/turn_guard.py) -- the
        request thread cannot be relied on to do it, because the process
        is on its way out.

        Deliberately does NOT chunk/embed the turn: chunking calls the
        embedding server over HTTP, and a signal handler must not block
        on the network -- systemd escalates to SIGKILL after
        TimeoutStopSec, which would lose the turn we are trying to save.

        The "exactly once" guarantee comes from turn_guard's atomic
        CLAIM, not from reading `state.interrupted_id()`. That check is
        a check-then-act across a database write: two callers can both
        read "not written yet" and both write. It happened on
        2026-10-26 -- a restart during a live turn left two identical
        assistant rows (ids 196 and 197) in peribot.turns, with the
        second row's computed latency ONE MILLISECOND SMALLER than the
        first's, which is only possible if the two callers interleaved.
        See turn_guard.claim_interrupt.
        """
        claimed, existing = turn_guard.claim_interrupt(
            state.conv_id, state.request_id
        )
        if not claimed:
            # Someone else is writing (or has written) the marker.
            return existing
        text = (
            "⚠️ _This turn was interrupted before I could finish — "
            f"{reason}._\n\n"
            "The work may be partially done. Send me a follow-up and I'll "
            "pick up where I left off."
        )
        agent_memory = self._agent_memory(state.agent_name)
        try:
            turn = agent_memory.write_turn_assistant(
                state.conv_id, text,
                model=state.model,
                latency_ms=int((time.monotonic() - state.t0) * 1000),
                attachments=state.attachments,
                trajectory={
                    "interrupted": True,
                    "reason": reason,
                    "iterations": state.emission_iters,
                    "total_iterations": len(state.emission_iters),
                    "total_tool_calls": sum(
                        len(it.get("tool_calls") or []) for it in state.emission_iters
                    ),
                    "tool_iterations": state.tool_iters,
                    "model": state.model,
                    "request_id": state.request_id,
                },
            )
        except Exception:
            log.exception("failed to persist interrupted turn: conv=%s", state.conv_id)
            # Hand the claim back: without it the turn would have no
            # marker at all, which is the failure this exists to stop.
            turn_guard.release_interrupt(state.conv_id, state.request_id)
            return None
        turn_guard.complete_interrupt(state.conv_id, state.request_id, turn.id)
        state.mark_interrupted(turn.id)
        log.warning(
            "interrupted turn persisted: conv=%s turn_id=%s reason=%s",
            state.conv_id, turn.id, reason,
        )
        return turn.id

    def handle_chat(
        self,
        user_text: str,
        agent_name: str,
        user_id: int | None = None,
        attachments: list[dict] | None = None,
        request_id: str | None = None,
        user_turn_trajectory: dict | None = None,
        protected_paths: tuple[str, ...] = (),
        max_tool_iterations: int | None = None,
        system_suffix: str | None = None,
    ) -> ChatResponse:
        """Non-streaming wrapper around `handle_chat_streaming`.

        Drives the generator to completion and returns the final
        ChatResponse. Preserved for back-compat (older callers, the
        admin /last-emission view, the test suite). For long-running
        turns that risk the Cloudflare 524 origin read timeout, use
        `handle_chat_streaming` and consume the events.

        `user_turn_trajectory` and `protected_paths` are passed through
        to the streaming variant (the reverie turn uses both).
        """
        final: ChatResponse | None = None
        for event in self.handle_chat_streaming(
            user_text=user_text,
            agent_name=agent_name,
            user_id=user_id,
            attachments=attachments,
            request_id=request_id,
            user_turn_trajectory=user_turn_trajectory,
            protected_paths=protected_paths,
            max_tool_iterations=max_tool_iterations,
            system_suffix=system_suffix,
        ):
            if event.get("type") == "final":
                final = event["response"]
            elif event.get("type") == "error":
                raise RuntimeError(event.get("message", "loop error"))
        if final is None:
            raise RuntimeError("handle_chat_streaming ended without a final event")
        return final

    @staticmethod
    def _has_assistant_payload(messages: list[Message], payload: Any) -> bool:
        """Don't double-append the same assistant payload (idempotent)."""
        for m in messages:
            if m.role == "assistant" and m.content == payload:
                return True
        return False

    def _apply_prompt_pressure(
        self,
        system_text: str,
        *,
        agent_name: str,
        agent_bootstrap: Any,
        permanent_chunks: list[Any],
        permanent_overflowed: bool,
    ) -> str:
        """Append a conditional warning to the system prompt when the
        agent's own context is near its caps.

        Two independent conditions (Mal, 2026-10-22):

        1. an identity file is at/over `pressure_threshold` of its byte
           cap -> tell the agent to move the detail out to memories
           (via the memory tools) or to skills / scripts / docs, and to
           keep the identity file as a table of contents. An over-cap
           identity file is not "bloat", it is a BOOT FAILURE:
           bootstrap behaviour_on_overflow is "fail".
        2. the permanent-memory block is at/over the threshold of
           `memory.permanent.max_tokens` -> tell the agent to move the
           detail out to skills / scripts / docs, or to write it as an
           UNPINNED memory and recall it on demand. Anything past the
           cap is dropped from the prompt SILENTLY (the caller of
           get_permanent() used to discard the overflowed flag), so the
           agent would otherwise stop remembering things with no
           explanation.

        Returns the system text unchanged when nothing is over the
        threshold -- the common case, so this costs nothing.
        """
        cfg = getattr(self.config, "prompt", None)
        if cfg is None or not getattr(cfg, "pressure_enabled", True):
            return system_text
        threshold = float(getattr(cfg, "pressure_threshold", 0.85) or 0.85)
        warnings: list[str] = []

        # --- 1. identity files -------------------------------------
        if agent_bootstrap is not None:
            try:
                usage = agent_bootstrap.file_usage()
            except Exception:
                usage = []
            for u in usage:
                cap = int(u.get("cap") or 0)
                pct = float(u.get("pct") or 0.0)
                if not cap or pct < threshold:
                    continue
                warnings.append(
                    f'<context_pressure target="identity" file="{u["name"]}">\n'
                    f'Your identity file {u["name"]} is at {u["bytes"]:,} of '
                    f'{cap:,} bytes ({pct * 100:.1f}% of its hard cap).\n'
                    'Do NOT grow it further: an over-cap identity file stops '
                    'the harness from booting (bootstrap overflow behaviour '
                    'is "fail").\n'
                    'Move the detail OUT of the identity file and keep the '
                    'file as a table of contents:\n'
                    f'- skills/  -> pie/{agent_name}/skills/<topic>/SKILL.md '
                    '(reference it by path)\n'
                    f'- scripts/ -> pie/{agent_name}/scripts/  (executable steps)\n'
                    f'- docs/    -> pie/{agent_name}/docs/     (reference material)\n'
                    '- memories -> write it with the memory_write tool and '
                    'recall it on demand with memory_recall\n'
                    'Prefer a short "## Topic -> <path>" index over prose.\n'
                    '</context_pressure>'
                )

        # --- 2. permanent memory -----------------------------------
        cap = int(getattr(self.config.memory.permanent, "max_tokens", 0) or 0)
        used = sum(len((getattr(c, "content", "") or "").split())
                   for c in permanent_chunks)
        if cap and (used >= cap * threshold or permanent_overflowed):
            pct = min(100.0, 100.0 * used / cap)
            dropped = (
                " Pieces past the cap are dropped SILENTLY, so you would "
                "stop remembering them without knowing why."
                if permanent_overflowed else "")
            warnings.append(
                f'<context_pressure target="permanent_memory">\n'
                f'Your permanent memory block is at {used:,} of {cap:,} tokens '
                f'({pct:.1f}% of its cap).{dropped}\n'
                'Move the detail OUT of permanent memory:\n'
                f'- skills/ / scripts/ / docs/ under pie/{agent_name}/, '
                'referenced by path\n'
                '- or write it as an UNPINNED memory (memory_write without '
                'pinning) and pull it in on demand with memory_recall when it '
                'is relevant\n'
                'Permanent memory should hold only the short, always-relevant '
                'core.\n'
                '</context_pressure>'
            )

        if not warnings:
            return system_text
        return system_text + "\n\n" + "\n\n".join(warnings)

    def _compose_messages(
        self,
        conv_id: UUID,
        agent_name: str,
        user_turn: Any,
        agent_memory: Any,
        agent_bootstrap: Any = None,
        history_override: list[dict] | None = None,
        trailing_override: list[dict] | None = None,
        system_suffix: str | None = None,
    ) -> list[Message]:
        # Per-agent LLM (added 2026-10-19, fixed 2026-10-19 17:24):
        # The previous version of this method used `agent_adapter` as a
        # local variable from the caller (handle_chat_streaming), which
        # broke when this method was called from any other code path
        # (e.g. a future test or admin tool that calls _compose_messages
        # directly). Compute the per-agent adapter here so the method
        # is self-contained. The dispatch is O(1) — a dict get with a
        # default — so the cost is negligible.
        agent_adapter = self._adapter_for(agent_name)
        system_text = agent_prompts.get(agent_adapter.adapter_id).format(
            agent_name=agent_name, agent_tone="concise, direct, friendly",
        )
        # Refactor 2026-10-17: last 50 turns always included in the prompt
        # so continuity is preserved across logout/login (per Mal's request).
        #
        # 2026-10-22 (prompt duplication fix): the fresh user turn has
        # ALREADY been written to the table by the time we get here
        # (write_turn_user runs before _compose_messages), so the
        # history scan picks it up and the prompt carried the user's
        # message TWICE -- once as a plain-text history entry and once
        # as the final user message with the vision content block.
        # Confirmed live via /api/v1/admin/last-prompt.
        #
        # Fix: ask for one extra turn and drop the fresh one from the
        # history, so the prompt holds 50 turns of history plus the
        # fresh user message -- exactly what the comment above always
        # meant. Excluding by turn ID (not by position) is safe even if
        # another session writes to the same conversation concurrently.
        history_limit = 51
        if history_override is not None:
            # Group chat (2026-10-23): the caller supplies the shared
            # group transcript. The agent's own per-conversation turn
            # history is deliberately NOT used as the context here --
            # the group transcript is (it carries the other agents'
            # messages too). Each entry is {"role", "content"}.
            history_entries = [
                {
                    "role": (e.get("role") or "user"),
                    "content": e.get("content") or "",
                }
                for e in history_override
            ]
        else:
            history = agent_memory.load_recent_turns(
                conv_id, limit=history_limit, include_thought_stream=False,
            )
            fresh_turn_id = getattr(user_turn, "id", None)
            if fresh_turn_id is not None:
                history = [t for t in history if t.id != fresh_turn_id]
            history_entries = [
                {
                    "role": t.role,
                    "content": self._content_with_attachments_text(t.content, t.attachments),
                }
                for t in history
            ]
        permanent_chunks, _overflowed = agent_memory.get_permanent()
        perm_block = self._format_permanent(permanent_chunks)
        # Per-agent bootstrap (multi-agent dispatch since 2026-10-15).
        bootstrap_block = agent_bootstrap.render() if agent_bootstrap else ""

        # Conditional system prompt: warn the agent when its own context
        # is near its caps (Mal, 2026-10-22). The agent is the one who
        # can fix it -- by moving detail into memories, skills, scripts
        # or docs -- so it gets told, rather than the file silently
        # tripping a boot-time check or a memory silently dropping out
        # of the prompt.
        system_text = self._apply_prompt_pressure(
            system_text,
            agent_name=agent_name,
            agent_bootstrap=agent_bootstrap,
            permanent_chunks=permanent_chunks,
            permanent_overflowed=bool(_overflowed),
        )

        msgs: list[Message] = [Message(role="system", content=system_text)]
        if system_suffix:
            # Appended to the SYSTEM prompt, not sent as a user turn:
            # the reverie's scope rule has to outrank the pull of an
            # unfinished task sitting in the conversation history.
            system_text = system_text + "\n\n" + system_suffix.strip()
            msgs[0] = Message(role="system", content=system_text)
        if bootstrap_block:
            msgs.append(Message(role="user", content=bootstrap_block))
        if perm_block:
            msgs.append(Message(role="user", content=perm_block))
        # Phase 10.1: native vision. The model behind adapter_id
        # `minimax_m3` is MiniMax-M3, whose catalog (per
        # ~/.openclaw/agents/main/agent/plugins/minimax/catalog.json)
        # explicitly accepts input modalities ["text", "image"] via
        # the Anthropic Messages API. We feed images as base64
        # content blocks (the served URL is auth-gated, so we can't
        # just hand the LLM a URL). Historical turns (replayed from
        # the DB) keep their textual note only — the original base64
        # bytes were never persisted, so we can't reconstruct them
        # cheaply; if a user wants the agent to re-see an old image,
        # they re-attach it. The fresh user turn gets the full
        # image + text content block when attachments are present.
        for entry in history_entries:
            msgs.append(Message(role=entry["role"], content=entry["content"]))
        user_content = self._content_with_attachments_vision(
            user_turn.content, user_turn.attachments,
        )
        msgs.append(Message(role="user", content=user_content))
        # Group chat (2026-10-23): the replies the OTHER mentioned
        # agents already gave for this same message come AFTER the
        # fresh user message, so the second agent sees the first
        # agent's answer.
        for entry in (trailing_override or []):
            msgs.append(Message(
                role=(entry.get("role") or "user"),
                content=entry.get("content") or "",
            ))
        return msgs

    @staticmethod
    def _content_with_attachments_text(
        content: str, attachments: list[dict] | None
    ) -> str:
        """For historical turns: append a short textual note so the
        model at least knows something was attached (we don't have
        the bytes anymore). See _content_with_attachments_vision for
        the fresh-turn path that includes the actual image."""
        if not attachments:
            return content
        imgs = [a for a in attachments
                if (a.get("content_type") or "").startswith("image/")]
        others = [a for a in attachments
                  if not (a.get("content_type") or "").startswith("image/")]
        notes: list[str] = []
        if imgs:
            urls = ", ".join(a.get("url", "?") for a in imgs)
            label = "image" if len(imgs) == 1 else "images"
            note = f"[attached {len(imgs)} {label}: {urls}]"
            if len(imgs) == 1:
                a = imgs[0]
                meta = ", ".join(filter(None, [
                    a.get("filename"),
                    f"{a.get('size', 0)} bytes" if a.get("size") else "",
                    a.get("content_type", ""),
                ]))
                if meta:
                    note += f" ({meta})"
            notes.append(note)
        if others:
            urls = ", ".join(a.get("url", "?") for a in others)
            label = "file" if len(others) == 1 else "files"
            notes.append(f"[attached {len(others)} {label}: {urls}]")
        if not notes:
            return content
        return f"{content}\n\n" + "\n".join(notes) if content else "\n".join(notes)

    def _content_with_attachments_vision(
        self,
        content: str, attachments: list[dict] | None,
    ) -> str | list[dict]:
        """For the fresh user turn: build a real Anthropic content-block
        list with base64-encoded image blocks. The adapter passes
        list content through verbatim, so the LLM actually sees the
        image bytes alongside the user's text.

        Format (Anthropic Messages):
            [
              {"type": "image", "source": {
                  "type": "base64", "media_type": "image/png",
                  "data": "<base64>"}},
              {"type": "text", "text": "describe this image"}
            ]

        The served URL is /api/v1/uploads/<uuid>.<ext> (auth-gated,
        so we can't just hand the LLM a URL). Files land on disk at
        `<harness_root>/uploads/<uuid>.<ext>`; we map URL -> path
        with strict re-validation (mirrors the same logic as the
        /api/v1/uploads/<id> serve endpoint) to prevent path
        traversal even though Flask's <path:> converter doesn't
        decode .. itself.

        If an image is missing/too-large/wrong-type, we drop the
        image block and fall back to a textual note in the same
        format as the historical-turn path, so the LLM still
        knows something was attached.
        """
        if not attachments:
            return content
        imgs = [a for a in attachments
                if (a.get("content_type") or "").startswith("image/")]
        others = [a for a in attachments
                  if not (a.get("content_type") or "").startswith("image/")]
        # Cap per-image bytes so a 10 MiB upload doesn't blow the
        # context window (Anthropic recommends < 5 MiB; we go a
        # little higher to cover phone photos that often sit around
        # 4-5 MiB after HEIC->JPEG conversion). The
        # MAX_CONTENT_LENGTH at the app level is also the upload
        # endpoint's 10 MiB cap, so this is the upper bound.
        MAX_IMAGE_BYTES = 5 * 1024 * 1024
        blocks: list[dict] = []
        dropped: list[dict] = []
        for a in imgs:
            block = self._image_block_from_attachment(a, MAX_IMAGE_BYTES)
            if block is None:
                dropped.append(a)
            else:
                blocks.append(block)
        # If we couldn't read any of the images, keep a textual
        # note so the model still knows about them (otherwise
        # silent drop).
        if not blocks and dropped:
            return self._content_with_attachments_text(content, attachments)
        # If SOME images were read OK and others weren't, note the
        # dropped ones so the model can ask the user to re-attach.
        if blocks and dropped:
            dropped_note = self._content_with_attachments_text("", dropped)
            content = (content + "\n\n" + dropped_note) if content else dropped_note
        if not content and blocks:
            # No user text at all, just images — synthesise a
            # minimal text block so the message has a text anchor.
            content = "(see attached image(s))"
        # Order matters for the LLM: images first, then text.
        # Anthropic accepts any order but image-then-text is the
        # conventional order and matches what most clients do.
        ordered: list[dict] = list(blocks)
        if content:
            ordered.append({"type": "text", "text": content})
        return ordered

    @staticmethod
    def _image_block_from_attachment(
        att: dict, max_bytes: int
    ) -> dict | None:
        """Map a /api/v1/uploads/<uuid>.<ext> URL back to a
        <harness_root>/uploads/<uuid>.<ext> file on disk, read it,
        and return an Anthropic image content block. Returns None
        if the URL is malformed, the file is missing/too-large, or
        the media type isn't one we want to send."""
        import base64
        import re
        from pathlib import Path
        url = att.get("url", "")
        # Strict re-validation of the URL form: must be the literal
        # /api/v1/uploads/<32 hex>.<allowlisted ext> shape. Anything
        # else is treated as a non-image (defence in depth on top
        # of the same check in the serve endpoint).
        m = re.fullmatch(
            r"/api/v1/uploads/([0-9a-f]{32})\.([a-z0-9]{2,5})", url
        )
        if not m:
            return None
        file_id, ext = m.group(1), m.group(2)
        if ext not in ("jpg", "jpeg", "png", "gif", "webp", "heic", "heif"):
            return None
        # map ext -> media_type (mirror the upload allowlist)
        media_type = {
            "jpg":  "image/jpeg",
            "jpeg": "image/jpeg",
            "png":  "image/png",
            "gif":  "image/gif",
            "webp": "image/webp",
            "heic": "image/heic",
            "heif": "image/heif",
        }[ext]
        upload_dir = _resolve_upload_dir()
        target = (upload_dir / f"{file_id}.{ext}").resolve()
        # Defence in depth: ensure the resolved path is still inside
        # the upload dir (catches symlink games even though we
        # never set the file executable or run it).
        try:
            target.relative_to(upload_dir.resolve())
        except ValueError:
            return None
        if not target.is_file():
            return None
        size = target.stat().st_size
        if size <= 0 or size > max_bytes:
            return None
        try:
            blob = target.read_bytes()
        except OSError:
            return None
        b64 = base64.standard_b64encode(blob).decode("ascii")
        # Anthropic Messages API image block is strict — only `type`
        # + `source` are allowed at the top level, and `source` only
        # accepts {type, media_type, data} (or {type, url} for URL
        # refs). We previously added a non-standard `_meta` key for
        # debugging convenience, but the API ignored/rejected the
        # block when it was present (model reported "I can't see
        # images" despite the bytes being on the wire). Drop the
        # meta; the filename + size is already in the historical
        # turn's text note (via _content_with_attachments_text) and
        # the byte count is visible in the request size.
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": b64,
            },
        }

    @staticmethod
    def _format_permanent(chunks: list[Any]) -> str:
        if not chunks:
            return ""
        parts = ["<permanent_memories>"]
        for c in chunks:
            parts.append(
                f"[{c.role}] {c.content}  (id={c.id}, "
                f"pin_source={c.pin_source or 'unknown'}, "
                f"importance={c.importance})"
            )
        parts.append("</permanent_memories>")
        return "\n".join(parts)
