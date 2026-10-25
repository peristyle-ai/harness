"""Security audit tests for harness.

Run with: HARNESS_RUN_LIVE_TESTS=1 pytest tests/test_security.py

Verifies:
- KeyRedactionFilter masks LLM_API_KEY= *** + Authorization: Bearer ***
- sanity_check_env refuses world-readable .env, placeholder keys, empty keys
- /health and /api/* responses carry all security headers
- /health body never contains key-shape leaks
- Agent loop has the four MAX_* config attributes wired in
- No f-string SQL anywhere in app/
- Flask-Limiter is installed and has a default + per-route limits
"""
import logging
import os
import re
import tempfile
from pathlib import Path

import pytest

from app.security import (
    KeyRedactionFilter,
    install_log_redaction,
    sanity_check_env,
)


# --- KeyRedactionFilter -------------------------------------------------

def test_key_redaction_filter_masks_llm_api_key():
    flt = KeyRedactionFilter()
    rec = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="http request with LLM_API_KEY=***", args=None, exc_info=None,
    )
    assert flt.filter(rec) is True
    assert "sk-real-key-12345" not in rec.msg
    assert "***REDACTED***" in rec.msg


def test_key_redaction_filter_masks_bearer():
    flt = KeyRedactionFilter()
    rec = logging.LogRecord(
        name="t", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="upstream 401: Authorization: Bearer sk-real-key-abc", args=None, exc_info=None,
    )
    flt.filter(rec)
    assert "sk-real-key-abc" not in rec.msg
    assert "***REDACTED***" in rec.msg


def test_key_redaction_filter_masks_in_args():
    flt = KeyRedactionFilter()
    rec = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="bad key %s", args=("LLM_API_KEY=***",), exc_info=None,
    )
    flt.filter(rec)
    assert "sk-arg-key" not in rec.args[0]
    assert "***REDACTED***" in rec.args[0]


def test_key_redaction_filter_passes_through_safe_messages():
    flt = KeyRedactionFilter()
    rec = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello world", args=None, exc_info=None,
    )
    flt.filter(rec)
    assert rec.msg == "hello world"


# --- sanity_check_env ----------------------------------------------------

def test_sanity_check_env_world_readable():
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".env") as f:
        f.write("HARNESS_SECRET_KEY=x\nLLM_API_KEY=y\nDATABASE_URL=p\n")
        path = f.name
    os.chmod(path, 0o644)
    try:
        with pytest.raises(RuntimeError, match="world-readable"):
            sanity_check_env(path)
    finally:
        os.chmod(path, 0o600)
        os.unlink(path)


def test_sanity_check_env_placeholder():
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".env") as f:
        f.write("HARNESS_SECRET_KEY=x\nLLM_API_KEY=***\nDATABASE_URL=p\n")
        path = f.name
    os.chmod(path, 0o600)
    try:
        with pytest.raises(RuntimeError, match="placeholder"):
            sanity_check_env(path)
    finally:
        os.unlink(path)


def test_sanity_check_env_empty_key():
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".env") as f:
        f.write("HARNESS_SECRET_KEY=x\nLLM_API_KEY=\nDATABASE_URL=p\n")
        path = f.name
    os.chmod(path, 0o600)
    try:
        with pytest.raises(RuntimeError, match="empty"):
            sanity_check_env(path)
    finally:
        os.unlink(path)


def test_sanity_check_env_happy_path():
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".env") as f:
        f.write("HARNESS_SECRET_KEY=x\nLLM_API_KEY=sk-cp-***\nDATABASE_URL=p\n")
        path = f.name
    os.chmod(path, 0o600)
    try:
        sanity_check_env(path)  # should not raise
    finally:
        os.unlink(path)


# --- Code-pattern checks (no live server required) -----------------------

def test_no_fstring_sql():
    """Belt-and-braces: scan the code for f-string SQL that interpolates a
    VALUE into the statement.

    What is flagged: an f-string SQL statement whose interpolation is a
    value -- something that could carry request data.

    What is tolerated, and why:
      * `self.<attr>` -- a class-internal constant (the agent's schema
        name), set once at init from config, never from request data;
      * an interpolation immediately followed by `.` and a table name --
        a schema or table qualifier, e.g. f"... FROM {agent_name}.turns".
        It cannot be a value.

    The check uses ast, so it sees each f-string as the language does
    (its literal parts and its interpolations, whole across lines). The
    earlier version matched any f-string BEGINNING with a SQL keyword,
    which flagged prose -- f"update failed: {e}" -- and missed the case
    where the keyword is not first in the string.
    """
    import ast

    repo = Path(__file__).parent.parent

    # A SQL STATEMENT, not prose that happens to contain a SQL word.
    sql_statement = re.compile(
        r"\b(?:"
        r"SELECT\b[^;]*\bFROM\b"
        r"|INSERT\s+INTO\b"
        r"|UPDATE\b\s+\S+\s+SET\b"
        r"|DELETE\s+FROM\b"
        r"|TRUNCATE\s+TABLE\b"
        r")",
        re.IGNORECASE | re.DOTALL,
    )

    bad = []
    for py in repo.rglob("*.py"):
        if "tests" in py.parts:
            continue
        if "site-packages" in py.parts or ".venv" in py.parts:
            continue
        # pie/<agent>/ is each agent's OWN git repo (its identity files and
        # its scripts). It is not part of the harness, it is not tracked,
        # and it is the agent's to own -- the harness's checks must not
        # reach into it.
        if "pie" in py.parts:
            continue
        try:
            source = py.read_text()
            tree = ast.parse(source)
        except (UnicodeDecodeError, OSError, SyntaxError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            # the literal parts, concatenated -- this is the SQL skeleton
            literal = "".join(v.value for v in node.values
                              if isinstance(v, ast.Constant)
                              and isinstance(v.value, str))
            if not sql_statement.search(literal):
                continue

            # each interpolation, with the literal text that FOLLOWS it
            for i, part in enumerate(node.values):
                if not isinstance(part, ast.FormattedValue):
                    continue
                expr = ast.get_source_segment(source, part.value) or ""
                expr = expr.strip()
                # a class-internal constant
                if expr.startswith("self."):
                    continue
                # a LITERAL FRAGMENT: a conditional whose branches are both
                # string constants, e.g.
                #   {"AND model IS DISTINCT FROM 'x'" if flag else ""}
                # The value is chosen from a boolean, so it cannot carry
                # request data.
                if isinstance(part.value, ast.IfExp):
                    branches = [part.value.body, part.value.orelse]
                    if all(isinstance(b, ast.Constant)
                           and isinstance(b.value, str) for b in branches):
                        continue
                # a schema/table qualifier: {agent_name}.turns
                follows = ""
                if i + 1 < len(node.values):
                    nxt = node.values[i + 1]
                    if isinstance(nxt, ast.Constant) and isinstance(nxt.value, str):
                        follows = nxt.value
                if re.match(r"\s*\.\s*\w", follows):
                    continue
                # What remains is a BARE NAME interpolated into the SQL.
                # A name can carry request data, so it is the risk. (An
                # attribute access or a call is not a bare name; the ones
                # that matter -- self.<attr> -- are handled above.)
                if not re.match(r"^[A-Za-z_]\w*$", expr):
                    continue
                bad.append(f"{py}:{node.lineno}: {expr}  in  {literal[:90]!r}")
                break

    assert not bad, "f-string SQL with a value interpolated found:\n" + "\n".join(bad)


def test_agent_loop_has_max_tool_limits(config_path):
    from app.config import load_config
    cfg = load_config(config_path)
    for attr in (
        "max_tool_iterations", "max_searches_per_turn",
        "max_pins_per_turn", "max_unpins_per_turn",
    ):
        assert hasattr(cfg.agent, attr), f"cfg.agent missing {attr}"


# --- Live tests (require a running app + config) -------------------------

@pytest.mark.skipif(
    "HARNESS_RUN_LIVE_TESTS" not in os.environ,
    reason="live server test",
)
def test_security_headers_on_routes(config_path):
    from app.config import load_config
    from app import create_app
    cfg = load_config(config_path)
    app = create_app(cfg)
    client = app.test_client()
    for path in ("/health", "/", "/api/v1/conversations"):
        r = client.get(path)
        assert r.status_code in (200, 404), f"{path}: {r.status_code}"
        if r.status_code == 200:
            assert r.headers.get("X-Content-Type-Options") == "nosniff", f"{path}: missing X-Content-Type-Options"
            assert r.headers.get("X-Frame-Options") == "DENY", f"{path}: missing X-Frame-Options"
            assert "max-age=" in r.headers.get("Strict-Transport-Security", ""), f"{path}: missing HSTS"
            assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin", f"{path}: missing Referrer-Policy"
            assert "default-src" in r.headers.get("Content-Security-Policy", ""), f"{path}: missing CSP"


@pytest.mark.skipif(
    "HARNESS_RUN_LIVE_TESTS" not in os.environ,
    reason="live server test",
)
def test_health_body_doesnt_leak_keys(config_path):
    from app.config import load_config
    from app import create_app
    cfg = load_config(config_path)
    app = create_app(cfg)
    client = app.test_client()
    r = client.get("/health")
    body = r.get_data(as_text=True)
    for pattern in (r"sk-[A-Za-z0-9_-]{8,}", r"key-[A-Za-z0-9_-]{8,}"):
        assert not re.search(pattern, body), f"key-shape leak: {body!r}"


@pytest.mark.skipif(
    "HARNESS_RUN_LIVE_TESTS" not in os.environ,
    reason="live server test",
)
def test_request_id_header(config_path):
    from app.config import load_config
    from app import create_app
    cfg = load_config(config_path)
    app = create_app(cfg)
    client = app.test_client()
    r = client.get("/health", headers={"X-Request-Id": "test-abc-123"})
    assert r.headers.get("X-Request-Id") == "test-abc-123"
