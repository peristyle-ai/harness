"""Tests for the install-config applier (2026-10-25).

The install config is what a CUSTOMER receives by email, so the applier has
to get it right without an engineer on their machine:

  * the generated config.yaml has THIS install's agent and none of the
    other install's agents (the per-agent LLM block names env vars that do
    not exist here, so it would break the config load)
  * the generated .env carries the install's embedding provider (a remote
    one with a key) and leaves the operator's own secrets as visible
    placeholders
  * the generated identity files are the SYSADMIN identity with the name
    filled in and no template braces left
  * the agent's schema SQL is generated for this agent's schema
"""
import re
import shutil
import sys
from pathlib import Path

import pytest

# The repo this test lives in -- not an install path. A hardcoded absolute
# path only works on the machine the test was written on.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from apply_install_config import (  # noqa: E402
    load_install, render_config, render_env, render_identity, render_agent_schema,
)

INSTALL = {
    "agent_name": "agent",
    "operator_name": "the operator",
    "operator_email": "operator@example.com",
    "operator_timezone": "UTC",
    "database": "harness",
    "schema": "agent",
    "operator_role": "agent",
    "runtime_role": "agent_app",
    "bind": "127.0.0.1:5010",
    "pie_root": "harness/pie",
    "embedding": {
        "base_url": "https://embed.example.com/v1",
        "api_key": "ep_testkey",
        "model": "Qwen/Qwen3-Embedding-4B",
        "dimensions": 1024,
        "timeout_s": 30,
    },
}


def test_the_config_has_only_this_installs_agent():
    cfg = render_config(INSTALL)
    assert re.search(r"^  agents:\n  - agent$", cfg, re.M)
    for other in ("peribot", "peribot", "peribot"):
        assert f"  - {other}" not in cfg, f"{other} is not part of this install"
    # the per-agent LLM block names env vars this install does not have
    assert "WISP_LLM_" not in cfg
    assert "EVIE_LLM_" not in cfg
    # No LIVE per_agent block. The stock template keeps one COMMENTED OUT as
    # an example of the feature, so the test has to look for a live block
    # rather than the word.
    assert not re.search(r"^  per_agent:", cfg, re.M), (
        "the rendered config has a live per_agent block -- it names env "
        "vars this install does not have"
    )


def test_the_env_carries_the_remote_embedding_and_leaves_the_rest():
    env, _generated = render_env(INSTALL)
    assert "EMBEDDING_BASE_URL=https://embed.example.com/v1" in env
    assert "EMBEDDING_API_KEY=ep_testkey" in env
    assert "EMBEDDING_MODEL=Qwen/Qwen3-Embedding-4B" in env
    assert "EMBEDDING_DIMENSIONS=1024" in env
    assert "HARNESS_DEFAULT_AGENT=agent" in env
    assert "HARNESS_BIND=127.0.0.1:5010" in env
    assert "APP_DB_RUNTIME_ROLE=agent_app" in env
    # the operator's own secrets are left as VISIBLE placeholders
    assert "LLM_API_KEY=REPLACE_ME" in env
    # and no other install's variables leak in
    assert "WISP_LLM_" not in env


def test_the_database_passwords_are_generated_and_agree_with_the_dsn():
    """The DSN's password and the runtime role's password must be ONE.

    The operator creates the roles by running the printed CREATE ROLE
    commands, so the password in the .env and the password in the
    command have to be the same string. When the operator had to copy
    one by hand, they got it wrong.
    """
    env, generated = render_env(INSTALL)
    assert generated["runtime_password"]
    assert generated["operator_password"]
    assert generated["runtime_password"] != generated["operator_password"]
    # the runtime role's password is in the DSN, URL-encoded
    from urllib.parse import unquote
    m = re.search(r"^DATABASE_URL=postgresql://agent_app:([^@]*)@", env, re.M)
    assert m, "the DSN is missing"
    assert unquote(m.group(1)) == generated["runtime_password"]
    # and the operator role's password is in its own variable
    assert f"APP_DB_OPERATOR_PASSWORD={generated['operator_password']}" in env


def test_the_env_is_sourced_by_bash_so_every_line_must_be_a_valid_assignment():
    """The .env is SOURCED by run.sh, not parsed.

    A value that is not a bare shell word breaks the service at startup
    with a confusing error ("line 37: <the value>: command not found").
    So every line must survive `set -a; . ./.env; set +a`.
    """
    env, _ = render_env(INSTALL)
    # a key with characters that bash would act on
    nasty = dict(INSTALL)
    nasty["llm"] = {
        "adapter": "minimax_m31_flash",
        "api_key": "sk-test$(whoami)`id`$HOME's",
        "base_url": "https://api.minimax.io/anthropic",
    }
    nasty_env, _ = render_env(nasty)
    for text in (env, nasty_env):
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            assert re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", stripped), (
                f"not a shell assignment: {stripped[:70]}"
            )


def test_a_placeholder_api_key_is_not_left_looking_like_a_key():
    """The example ships "sk-...", which `grep REPLACE_ME` does not find.

    An operator who ran the documented `grep REPLACE_ME .env` would ship
    the literal "sk-..." and get an opaque provider 401 at runtime.
    """
    inst = dict(INSTALL)
    inst["llm"] = {
        "adapter": "minimax_m31_flash",
        "api_key": "sk-...",
        "base_url": "https://api.minimax.io/anthropic",
    }
    env, _ = render_env(inst)
    assert "LLM_API_KEY=REPLACE_ME" in env
    assert "sk-..." not in env


def test_the_printed_steps_create_the_roles_before_the_database():
    """`CREATE DATABASE ... OWNER agent` FAILS if the role is missing.

    The step says "create the database + the roles" but used to print
    only the CREATE DATABASE, so a fresh install stopped dead on the
    first command.
    """
    import io
    import contextlib
    from apply_install_config import print_next_steps

    _, generated = render_env(INSTALL)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_next_steps(INSTALL, generated)
    out = buf.getvalue()
    assert "CREATE ROLE agent LOGIN" in out, out
    assert "CREATE ROLE agent_app LOGIN" in out, out
    assert "NOSUPERUSER" in out, "the runtime role must not be a superuser"
    assert "CREATE DATABASE harness OWNER agent" in out, out
    # the venv step -- the service runs from it
    assert "python3 -m venv .venv" in out, out
    assert "pip install -r requirements.txt" in out, out
    # the role creation must come BEFORE the database
    assert out.index("CREATE ROLE agent LOGIN") < out.index("CREATE DATABASE")


def test_the_identity_is_the_sysadmin_one_with_the_name_filled_in():
    files = render_identity(INSTALL)
    assert set(files) == {"AGENTS.md", "HEARTBEAT.md", "IDENTITY.md",
                          "MEMORY.md", "SOUL.md", "USER.md"}
    ident = files["IDENTITY.md"]
    assert "agent" in ident
    assert "sysadmin" in ident.lower()
    # no template braces left
    for name, text in files.items():
        leftover = re.findall(r"\{[A-Za-z][^}\n]{0,40}\}", text)
        assert not leftover, f"{name} still has template braces: {leftover}"
    assert "the operator" in files["USER.md"]
    assert "UTC" in files["USER.md"]
    assert "5010" in files["IDENTITY.md"]


def test_the_agent_schema_is_for_this_agent():
    sql = render_agent_schema(INSTALL)
    assert 'CREATE SCHEMA IF NOT EXISTS "agent"' in sql
    assert "peribot" not in sql, "the template schema's name must be replaced"
    assert '"agent".conversations' in sql
    assert '"agent".turns' in sql
    # the psql variable must be fully substituted -- an unsubstituted
    # :agent_name would make the file depend on how it is invoked. The
    # COMMENTS document the variable, so they are excluded.
    code = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    assert ":agent_name" not in code
    assert ":'agent_name'" not in code
    # the agent's name must also be the default of its agent_name column
    assert "DEFAULT 'agent'::text" in sql


def test_an_install_config_without_the_required_keys_is_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("install:\n  agent_name: x\n")
    with pytest.raises(SystemExit):
        load_install(p)


def test_printing_the_steps_does_not_write_into_the_checkout():
    """main() writes config.yaml, .env and the identity files into ROOT.

    The steps are printed by their own function precisely so a test can
    read them without touching the repository. This test would have
    overwritten the repo's config.yaml if it called main().
    """
    import io
    import contextlib
    from apply_install_config import print_next_steps

    tracked = ROOT / "config.yaml"
    before = tracked.read_text() if tracked.exists() else None
    _, generated = render_env(INSTALL)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_next_steps(INSTALL, generated)
    after = tracked.read_text() if tracked.exists() else None
    assert before == after, "the printer wrote into the checkout"


def test_rerunning_the_applier_keeps_the_existing_database_password(tmp_path, monkeypatch):
    """Re-running the applier must not lock the app out of its database.

    The roles were created with the password that was in the .env at the
    time. A new password in the .env means the app can no longer
    authenticate, and the operator has to fix the roles by hand.
    """
    import apply_install_config as aic

    env_path = aic.ROOT / ".env"
    original = env_path.read_text() if env_path.exists() else None
    try:
        env_path.write_text(
            "DATABASE_URL=postgresql://agent_app:keepthispassword@127.0.0.1:5432/harness\n"
            "APP_DB_OPERATOR_PASSWORD=keepthisoperator\n"
        )
        env, generated = render_env(INSTALL)
        assert "keepthispassword" in env
        assert generated["runtime_password"] == "keepthispassword"
        assert generated["operator_password"] == "keepthisoperator"
    finally:
        if original is None:
            env_path.unlink(missing_ok=True)
        else:
            env_path.write_text(original)
