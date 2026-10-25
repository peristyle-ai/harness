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
    "agent_name": "peribot",
    "operator_name": "the operator",
    "operator_email": "operator@example.com",
    "operator_timezone": "UTC",
    "database": "harness",
    "schema": "peribot",
    "operator_role": "peribot",
    "runtime_role": "peribot_app",
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
    assert re.search(r"^  agents:\n  - peribot$", cfg, re.M)
    for other in ("peribot", "peribot", "peribot"):
        assert f"  - {other}" not in cfg, f"{other} is not part of this install"
    # the per-agent LLM block names env vars this install does not have
    assert "WISP_LLM_" not in cfg
    assert "EVIE_LLM_" not in cfg
    assert "per_agent:" not in cfg


def test_the_env_carries_the_remote_embedding_and_leaves_the_rest():
    env = render_env(INSTALL)
    assert "EMBEDDING_BASE_URL=https://embed.example.com/v1" in env
    assert "EMBEDDING_API_KEY=ep_testkey" in env
    assert "EMBEDDING_MODEL=Qwen/Qwen3-Embedding-4B" in env
    assert "EMBEDDING_DIMENSIONS=1024" in env
    assert "HARNESS_DEFAULT_AGENT=peribot" in env
    assert "HARNESS_BIND=127.0.0.1:5010" in env
    assert "APP_DB_RUNTIME_ROLE=peribot_app" in env
    # the operator's own secrets are left as VISIBLE placeholders
    assert "LLM_API_KEY=REPLACE_ME" in env
    assert "DATABASE_URL=postgresql://peribot_app:REPLACE_ME@" in env
    # and no other install's variables leak in
    assert "WISP_LLM_" not in env


def test_the_identity_is_the_sysadmin_one_with_the_name_filled_in():
    files = render_identity(INSTALL)
    assert set(files) == {"AGENTS.md", "HEARTBEAT.md", "IDENTITY.md",
                          "MEMORY.md", "SOUL.md", "USER.md"}
    ident = files["IDENTITY.md"]
    assert "peribot" in ident
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
    assert 'CREATE SCHEMA IF NOT EXISTS "peribot"' in sql
    assert "peribot" not in sql, "the template schema's name must be replaced"
    assert '"peribot".conversations' in sql
    assert '"peribot".turns' in sql
    # the psql variable must be fully substituted -- an unsubstituted
    # :agent_name would make the file depend on how it is invoked. The
    # COMMENTS document the variable, so they are excluded.
    code = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    assert ":agent_name" not in code
    assert ":'agent_name'" not in code
    # the agent's name must also be the default of its agent_name column
    assert "DEFAULT 'peribot'::text" in sql


def test_an_install_config_without_the_required_keys_is_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("install:\n  agent_name: x\n")
    with pytest.raises(SystemExit):
        load_install(p)
