"""Text that Postgres can actually store.

WHY THIS EXISTS
---------------
Postgres `text` AND `jsonb` both refuse U+0000: a string containing one
raises

    psycopg.errors.UntranslatableCharacter: unsupported Unicode escape
    sequence

and nothing gets stored. That is not a rare edge case. A tool that reads
a binary file -- an ELF header, a `.pyc`, a core dump, a JPEG -- puts NUL
bytes into the turn, and then the turn cannot be persisted AT ALL: the
reply is lost, the turn is lost, and the error message that replaces it
carries the same bytes, so the failure repeats while it is being
reported. On 2026-11-03 this hit two agents on the same install: the
agent that was asked to debug the rendering problem lost its own answer
to the rendering problem (the "chicken and egg" case).

Two different failures live here, and they are different in kind:

* **Invalid UTF-8** (a byte like 0xa0 that is not a character) breaks the
  DECODE. `subprocess.run(text=True)` decodes strictly, so the tool
  raises `UnicodeDecodeError` before it can return anything at all.
* **A NUL** is perfectly valid UTF-8, so it survives any decode and only
  explodes at the database, much later and in a different place.

So the boundary rule is: **nothing enters the harness carrying a NUL**,
and every tool output is decoded leniently. These helpers are that
boundary.
"""

from __future__ import annotations

NUL = "\x00"

# What a NUL becomes: visible, greppable, and storable. A bare NUL is
# invisible in a terminal AND unstorable, so a reader would never know
# something was dropped; "\0" says exactly what happened.
NUL_ESCAPE = "\\0"


def nul_safe(text: str | None) -> str | None:
    """Return `text` with every NUL replaced by a visible escape.

    None passes through (it is a legitimate "no value").
    """
    if text is None:
        return None
    if NUL not in text:
        return text
    return text.replace(NUL, NUL_ESCAPE)


def nul_safe_deep(value):
    """Recursively make a structure safe to store.

    The trajectory is JSONB, and JSONB refuses NULs too -- so the
    sanitising has to happen BEFORE `json.dumps`, not after it (a NUL
    inside a JSON string is legal JSON and still rejected by Postgres).
    """
    if isinstance(value, str):
        return value.replace(NUL, NUL_ESCAPE)
    if isinstance(value, dict):
        return {k: nul_safe_deep(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [nul_safe_deep(v) for v in value]
    return value
