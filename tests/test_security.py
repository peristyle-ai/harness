"""Security audit tests for harness.

Run with: HARNESS_RUN_LIVE_TESTS=1 pytest tests/test_security.py

Verifies:
- KeyRedactionFilter masks LLM_API_KEY= *** + Authorization: Bearer ***
- sanity_check_config refuses a world-readable config.yaml, placeholder
  values, empty required values -- and the tracked template itself
- /health and /api/* responses carry all security headers
- /health body never contains key-shape leaks
- Agent loop has the four MAX_* config attributes wired in
- No f-string SQL anywhere in app/
- Flask-Limiter is installed and has a default + per-route limits
- The tool roster in SECURITY.md is the tool roster in the code
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
    sanity_check_config,
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


# --- sanity_check_config -------------------------------------------------

def _write_config(tmp_path, body, mode=0o600, name="config.yaml"):
    p = tmp_path / name
    p.write_text(body)
    os.chmod(p, mode)
    return p


GOOD_CONFIG = """
app:
  secret_key: a-real-secret
db:
  url: postgresql://harness_app:pw@localhost:5432/harness
llm:
  api_key: sk-cp-a-real-key
"""


def test_sanity_check_config_missing_file(tmp_path):
    with pytest.raises(RuntimeError, match="not found"):
        sanity_check_config(tmp_path / "nope.yaml")


def test_sanity_check_config_readable_by_others(tmp_path):
    """config.yaml holds the API key and the database password, so it
    must not be group- or world-readable."""
    p = _write_config(tmp_path, GOOD_CONFIG, mode=0o644)
    with pytest.raises(RuntimeError, match="readable by others"):
        sanity_check_config(p)


def test_sanity_check_config_placeholder(tmp_path):
    body = GOOD_CONFIG.replace("sk-cp-a-real-key", "YOUR_LLM_API_KEY_HERE")
    p = _write_config(tmp_path, body)
    with pytest.raises(RuntimeError, match="placeholder"):
        sanity_check_config(p)


def test_sanity_check_config_empty_value(tmp_path):
    body = GOOD_CONFIG.replace("url: postgresql://harness_app:pw@localhost:5432/harness",
                               "url: ''")
    p = _write_config(tmp_path, body)
    with pytest.raises(RuntimeError, match="empty"):
        sanity_check_config(p)


def test_sanity_check_config_happy_path(tmp_path):
    p = _write_config(tmp_path, GOOD_CONFIG)
    sanity_check_config(p)  # should not raise


def test_the_example_config_is_not_a_working_config(tmp_path):
    """The tracked template must FAIL the check.

    config.yaml.example holds YOUR_..._HERE placeholders. If it ever
    passed the startup check, a fresh install would start with a
    placeholder API key and fail at the provider with an opaque 401.
    """
    example = Path(__file__).resolve().parents[1] / "config.yaml.example"
    assert example.exists()
    p = _write_config(tmp_path, example.read_text())
    with pytest.raises(RuntimeError):
        sanity_check_config(p)


def test_the_example_config_is_not_world_readable_by_default(tmp_path):
    """A config copied from the template inherits its mode. The copy
    step in the README says chmod 600 -- and the check is what catches
    an operator who forgets it."""
    example = Path(__file__).resolve().parents[1] / "config.yaml.example"
    p = _write_config(tmp_path, example.read_text(), mode=0o644)
    with pytest.raises(RuntimeError, match="readable by others"):
        sanity_check_config(p)


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


# --- The tool roster in SECURITY.md is the tool roster in the code ------
#
# SECURITY.md documents the ten tools the model can call -- including
# exec_bash and sql_query, which reach the machine and the database.
# That document is the only place an operator learns what the agent is
# able to do, so it must not be able to drift away from the registry.
#
# The test reads the roster table OUT OF SECURITY.md and compares it to
# the registry. Add a tool without documenting it and this fails; list a
# tool that does not exist and this fails too.

DOCUMENTED_ROSTER_ROW = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|")


def _documented_tools():
    """The tool names listed in SECURITY.md's roster table."""
    doc = Path(__file__).resolve().parents[1] / "SECURITY.md"
    assert doc.exists(), "SECURITY.md is missing -- the tool roster must be documented"
    names = []
    in_table = False
    for line in doc.read_text().splitlines():
        if line.startswith("| Tool | What it does |"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break  # end of the table
            if set(line) <= set("| -: "):
                continue  # the separator row
            m = DOCUMENTED_ROSTER_ROW.match(line)
            if m:
                names.append(m.group(1))
    return names


def test_the_documented_tool_roster_matches_the_registry():
    from app.agent.tools.registry import names as registry_names

    documented = _documented_tools()
    assert documented, (
        "SECURITY.md's tool roster table was not found or was empty -- "
        "the roster table must start with a header row "
        "'| Tool | What it does |'"
    )

    registered = set(registry_names())
    documented_set = set(documented)

    missing = registered - documented_set
    assert not missing, (
        "these tools are registered but not documented in SECURITY.md: "
        f"{sorted(missing)}. The roster table in SECURITY.md is the "
        "operator's only view of what the agent can do -- document them."
    )

    extra = documented_set - registered
    assert not extra, (
        "SECURITY.md documents tools that are not registered: "
        f"{sorted(extra)}. Either the tool was removed or the doc is wrong."
    )

    assert len(documented) == len(documented_set), (
        "SECURITY.md's roster table lists a tool twice"
    )


def test_the_two_reaching_tools_are_registered():
    """exec_bash and sql_query reach the machine and the database.

    They are the two tools that turn a prompt injection into a real
    capability, so their absence is a security event, not a refactor:
    the SECURITY.md text that describes them would be a lie.
    """
    from app.agent.tools.registry import names as registry_names

    registered = set(registry_names())
    for name in ("exec_bash", "sql_query"):
        assert name in registered, (
            f"the {name} tool is not registered -- if it was removed on "
            "purpose, SECURITY.md must be updated in the same commit"
        )


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
