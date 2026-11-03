A turn could not be persisted at all if it carried a NUL byte, and the error message that replaced it carried the same bytes -- so the failure repeated while it was being reported.

On 2026-11-03 this hit two agents on one install: an agent asked to debug a rendering problem lost its own answer to the rendering problem, because the answer contained the very bytes it was reporting on.

Postgres cannot store a NUL, in text or in jsonb. A NUL gets into a turn the moment a tool reads a BINARY file -- an ELF header, a .pyc, a core dump. The bytes travel through the tool result into the trajectory, and then the turn cannot be persisted.

Two failures, different in kind: invalid UTF-8 breaks the DECODE (subprocess text=True decodes strictly, so the tool raised UnicodeDecodeError before it could return anything), while a NUL is valid UTF-8 and only explodes at the database, much later.

The fix is one boundary rule: nothing enters the harness carrying a NUL, and every tool output is decoded leniently.

The order matters: json.dumps turns a NUL into \u0000, which is legal JSON and still rejected by jsonb -- so sanitising AFTER the serialisation would be too late. The sanitising happens BEFORE it.

The regression test was seen failing on the pre-fix code, and it fails for the right reason rather than as a collection error. The fix was also proved end to end against a real Postgres, on both the text and the jsonb path.
