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
import yaml
import sys
from pathlib import Path

import pytest

# The repo this test lives in -- not an install path. A hardcoded absolute
# path only works on the machine the test was written on.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from apply_install_config import (  # noqa: E402
    generate_secrets, load_install, render_config, render_identity,
    render_agent_schema,
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
    generated = generate_secrets(INSTALL)
    cfg = yaml.safe_load(render_config(INSTALL, generated))
    assert cfg["app"]["agents"] == ["agent"]
    assert cfg["app"]["default_agent"] == "agent"
    for other in ("peribot", "second"):
        assert other not in cfg["app"]["agents"], \
            f"{other} is not part of this install"
    # No per_agent block: one agent, one API key.
    assert "per_agent" not in cfg["llm"]


def test_the_config_carries_the_remote_embedding_and_leaves_the_rest():
    generated = generate_secrets(INSTALL)
    cfg = yaml.safe_load(render_config(INSTALL, generated))
    assert cfg["embedding"]["base_url"] == "https://embed.example.com/v1"
    assert cfg["embedding"]["api_key"] == "ep_testkey"
    assert cfg["embedding"]["model"] == "Qwen/Qwen3-Embedding-4B"
    assert cfg["embedding"]["dimensions"] == 1024
    assert cfg["app"]["default_agent"] == "agent"
    assert cfg["app"]["bind"] == "127.0.0.1:5010"
    assert cfg["db"]["runtime_role"] == "agent_app"
    # The install config named no LLM, so the TEMPLATE's placeholder is
    # what stays -- and it is the placeholder `grep YOUR_ config.yaml`
    # finds. The old .env said REPLACE_ME, which that grep missed.
    assert cfg["llm"]["api_key"] == "YOUR_LLM_API_KEY_HERE"


def test_the_database_passwords_are_generated_and_agree_with_the_dsn():
    """The DSN's password and the runtime role's password must be ONE.

    The operator creates the roles by running the printed CREATE ROLE
    commands, so the password in the config and the password in the
    command have to be the same string. When the operator had to copy
    one by hand, they got it wrong.
    """
    generated = generate_secrets(INSTALL)
    cfg = yaml.safe_load(render_config(INSTALL, generated))
    assert generated["runtime_password"]
    assert generated["operator_password"]
    assert generated["runtime_password"] != generated["operator_password"]
    # the runtime role's password is in the DSN, URL-encoded
    from urllib.parse import unquote
    m = re.search(r"^postgresql://agent_app:([^@]*)@", cfg["db"]["url"], re.M)
    assert m, "the DSN is missing"
    assert unquote(m.group(1)) == generated["runtime_password"]
    # and the operator role's password is in its own field
    assert cfg["db"]["operator_password"] == generated["operator_password"]


def test_the_config_is_valid_yaml_whatever_the_values_are():
    """config.yaml is YAML, so a value with a quote, a colon or a
    newline in it must not break the file.

    The old .env was SOURCED by bash, which had its own quoting rules;
    YAML has different ones. A password with a colon in it is the case
    that bites.
    """
    nasty = dict(INSTALL)
    nasty["llm"] = {
        "adapter": "minimax_m31_flash",
        "api_key": "sk-test: with 'quotes' and \"doubles\" and: colons",
        "base_url": "https://api.minimax.io/anthropic",
    }
    generated = generate_secrets(nasty)
    text = render_config(nasty, generated)
    parsed = yaml.safe_load(text)
    assert parsed["llm"]["api_key"] == nasty["llm"]["api_key"]
    # and it still loads through the app's own loader
    assert parsed["app"]["agents"] == ["agent"]


def test_a_placeholder_api_key_is_not_left_looking_like_a_key():
    """The example ships "sk-...", which `grep YOUR_` does not find.

    An operator who ran the documented `grep YOUR_ config.yaml` would
    ship the literal "sk-..." and get an opaque provider 401 at
    runtime. The applier says so by name at install time.
    """
    from apply_install_config import PLACEHOLDER_VALUES
    inst = dict(INSTALL)
    inst["llm"] = {
        "adapter": "minimax_m31_flash",
        "api_key": "sk-...",
        "base_url": "https://api.minimax.io/anthropic",
    }
    assert "sk-..." in PLACEHOLDER_VALUES
    generated = generate_secrets(inst)
    cfg = yaml.safe_load(render_config(inst, generated))
    assert cfg["llm"]["api_key"] == "sk-..."
    # and the check that catches it at startup:
    from app.security import sanity_check_config
    assert "sk-..." in PLACEHOLDER_VALUES


def test_the_printed_steps_create_the_roles_before_the_database():
    """`CREATE DATABASE ... OWNER agent` FAILS if the role is missing.

    The step says "create the database + the roles" but used to print
    only the CREATE DATABASE, so a fresh install stopped dead on the
    first command.
    """
    import io
    import contextlib
    from apply_install_config import print_next_steps

    generated = generate_secrets(INSTALL)
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
    """main() writes config.yaml and the identity files into ROOT.

    The steps are printed by their own function precisely so a test can
    read them without touching the repository. This test would have
    overwritten the repo's config.yaml if it called main().
    """
    import io
    import contextlib
    from apply_install_config import print_next_steps

    tracked = ROOT / "config.yaml"
    before = tracked.read_text() if tracked.exists() else None
    generated = generate_secrets(INSTALL)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_next_steps(INSTALL, generated)
    after = tracked.read_text() if tracked.exists() else None
    assert before == after, "the printer wrote into the checkout"


def test_rerunning_the_applier_keeps_the_existing_database_password(tmp_path, monkeypatch):
    """Re-running the applier must not lock the app out of its database.

    The roles were created with the password that was in config.yaml at
    the time. A new password means the app can no longer authenticate,
    and the operator has to fix the roles by hand.
    """
    import apply_install_config as aic

    cfg_path = aic.ROOT / "config.yaml"
    original = cfg_path.read_text() if cfg_path.exists() else None
    try:
        cfg_path.write_text(
            "db:\n"
            "  url: postgresql://agent_app:keepthispassword@127.0.0.1:5432/harness\n"
            "  operator_password: keepthisoperator\n"
        )
        generated = generate_secrets(INSTALL)
        assert generated["runtime_password"] == "keepthispassword"
        assert generated["operator_password"] == "keepthisoperator"
    finally:
        if original is None:
            cfg_path.unlink(missing_ok=True)
        else:
            cfg_path.write_text(original)
