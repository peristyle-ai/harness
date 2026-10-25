#!/usr/bin/env python3
"""Apply an install config to a harness checkout.

Per Mal 2026-10-25: a customer should be able to install the harness from a
clone plus ONE file (the install config) without an engineer on their
machine. This writes, from that one file:

  * config.yaml    — the harness config for THIS install (one agent, no
                     per-agent LLM overrides, the embedding block)
  * .env           — the environment, with the install's values filled in and
                     the rest left as clearly-marked placeholders
  * pie/<agent>/identity/  — the agent's identity files, from the default
                     template (the sysadmin identity) with the name filled in
  * schema/init.sql — the whole schema (the shared public tables and the
    agent's schema), applied once per agent

It never invents a secret: anything the operator has to choose is left as a
visible placeholder and reported at the end.

    python3 tools/apply_install_config.py install/peribot.yaml
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("PyYAML is required: pip install pyyaml", file=sys.stderr)
    raise

ROOT = Path(__file__).resolve().parent.parent


def die(msg: str) -> "None":
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def load_install(path: Path) -> dict:
    if not path.exists():
        die(f"install config not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    inst = data.get("install") or {}
    for key in ("agent_name", "database", "schema", "operator_role", "runtime_role"):
        if not inst.get(key):
            die(f"the install config is missing install.{key}")
    if not (inst.get("embedding") or {}).get("base_url"):
        die("the install config is missing install.embedding.base_url")
    return inst


def render_config(inst: dict) -> str:
    """config.yaml for this install.

    The stock config.yaml is ${VAR}-based, so most of it works as-is. Two
    things differ: the agent list (one agent, not three) and the per-agent
    LLM overrides (they name peribot/peribot/peribot and their env vars, which
    this install does not have — the global llm block applies instead).
    """
    src = (ROOT / "config.yaml").read_text()
    agent = inst["agent_name"]

    # The agent list.
    src = re.sub(
        r"^(  agents:\n)(?:  - .*\n)+",
        lambda m: m.group(1) + f"  - {agent}\n",
        src, count=1, flags=re.M,
    )

    # Drop the per-agent LLM block: it is a wholesale replacement of the
    # global block for those agents, and this install has one agent that
    # uses the global block.
    src = re.sub(
        r"\n  per_agent:\n(?:.*\n)*?(?=[a-z_]+:)",
        "\n",
        src, count=1,
    )
    return src


def render_env(inst: dict) -> str:
    """The .env for this install."""
    src = (ROOT / ".env.example").read_text()
    emb = inst["embedding"]
    agent = inst["agent_name"]
    db = inst["database"]

    def set_var(text: str, name: str, value: str) -> str:
        if re.search(rf"^{re.escape(name)}=.*$", text, flags=re.M):
            return re.sub(
                rf"^{re.escape(name)}=.*$", f"{name}={value}", text, count=1, flags=re.M,
            )
        return text.rstrip() + f"\n{name}={value}\n"

    # --- the install's own values -----------------------------------------
    llm = inst.get("llm") or {}
    if llm:
        src = set_var(src, "LLM_ADAPTER", llm.get("adapter", "minimax_m31_flash"))
        src = set_var(src, "LLM_API_KEY", llm.get("api_key", "REPLACE_ME"))
        src = set_var(src, "LLM_BASE_URL", llm.get("base_url", "REPLACE_ME"))
        src = set_var(src, "LLM_MODEL", llm.get("model", "MiniMax-M3.1-Flash"))
        src = set_var(src, "LLM_MAX_TOKENS", str(llm.get("max_tokens", 524288)))
        src = set_var(src, "LLM_TIMEOUT_S", str(llm.get("timeout_s", 180)))
        src = set_var(src, "LLM_TEMPERATURE", str(llm.get("temperature", 0.7)))
    src = set_var(src, "EMBEDDING_BASE_URL", emb["base_url"])
    src = set_var(src, "EMBEDDING_MODEL", emb.get("model", "Qwen/Qwen3-Embedding-4B"))
    src = set_var(src, "EMBEDDING_DIMENSIONS", str(emb.get("dimensions", 1024)))
    src = set_var(src, "EMBEDDING_API_KEY", emb.get("api_key", ""))
    src = set_var(src, "EMBEDDING_TIMEOUT_S", str(emb.get("timeout_s", 30)))
    src = set_var(src, "HARNESS_DEFAULT_AGENT", agent)
    src = set_var(src, "HARNESS_BIND", inst.get("bind", "127.0.0.1:5010"))
    src = set_var(src, "HARNESS_DB", db)
    src = set_var(src, "HARNESS_INSTALLATION_DATABASE", db)
    src = set_var(src, "HARNESS_PIE_ROOT", inst.get("pie_root", "harness/pie"))
    src = set_var(src, "APP_DB_OPERATOR_ROLE", inst.get("operator_role", agent))
    src = set_var(src, "APP_DB_RUNTIME_ROLE", inst.get("runtime_role", f"{agent}_app"))

    # --- the things the operator must choose -------------------------------
    src = set_var(src, "DATABASE_URL",
                  f"postgresql://{inst.get('runtime_role', agent + '_app')}:"
                  "REPLACE_ME@127.0.0.1:5432/" + db)
    if not llm:
        src = set_var(src, "LLM_API_KEY", "REPLACE_ME")
        src = set_var(src, "LLM_BASE_URL", "REPLACE_ME")
    src = set_var(src, "HARNESS_SECRET_KEY", "REPLACE_ME")
    src = set_var(src, "HARNESS_TLS_CERT", "REPLACE_ME")
    src = set_var(src, "HARNESS_TLS_KEY", "REPLACE_ME")
    src = set_var(src, "REVERIE_USER_EMAIL", inst.get("operator_email", ""))
    return src


def render_identity(inst: dict) -> dict:
    """The agent's identity files, from the template with the placeholders in."""
    tpl_dir = ROOT / (inst.get("identity_template") or "agent_template")
    if not tpl_dir.is_dir():
        die(f"identity template not found: {tpl_dir}")
    agent = inst["agent_name"]
    subs = {
        "{Name}": agent,
        "{Operator name}": inst.get("operator_name", "the operator"),
        "{Operator timezone}": inst.get("operator_timezone", "UTC"),
        "{PORT}": str(inst.get("bind", "127.0.0.1:5010")).rsplit(":", 1)[-1],
        "{the operator's business}": inst.get("operator_business", "their own business"),
    }
    out = {}
    for src in sorted(tpl_dir.glob("*.template")):
        text = src.read_text()
        for k, v in subs.items():
            text = text.replace(k, v)
        out[src.name[: -len(".template")]] = text
    return out


def render_agent_schema(inst: dict) -> str:
    """The schema SQL for THIS agent, from the shipped init.

    The init is templated on the agent's schema name (the psql variable
    :"agent_name"), so the generated file carries the real name and is
    self-contained -- an operator can hand it to a DBA without knowing
    about psql variables.
    """
    agent = inst["agent_name"]
    src_path = ROOT / "schema" / "init.sql"
    if not src_path.exists():
        die(f"no schema init to template from: {src_path}")
    text = src_path.read_text()

    # the quoted-identifier form -- :"agent_name" -> "peribot"
    text = text.replace(':"agent_name"', f'"{agent}"')
    # the string-literal form -- :'agent_name' -> 'peribot'
    text = text.replace(":'agent_name'", f"'{agent}'")
    # the \gexec index builders -- :'agent_name' || '_suffix' -> 'peribot' || '_suffix'
    text = text.replace(":'agent_name' ||", f"'{agent}' ||")

    # the check must ignore the COMMENTS (they document the variable) and
    # the group_agents.agent_name COLUMN (which is the harness's own
    # column, not the psql variable)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        if ":agent_name" in line and "group_agents" not in line:
            die(f"the rendered schema still has an unsubstituted "
                f"agent_name: {stripped[:80]}")
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("install_config", help="e.g. install/peribot.yaml")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    inst = load_install(Path(args.install_config))
    agent = inst["agent_name"]
    print(f"install config : {args.install_config}")
    print(f"agent          : {agent}")
    print(f"embedding      : {inst['embedding']['base_url']}")
    print(f"database       : {inst['database']} (schema {inst['schema']})")

    writes = {
        ROOT / "config.yaml": render_config(inst),
        ROOT / ".env": render_env(inst),
        ROOT / "schema" / f"{agent}_schema.sql": render_agent_schema(inst),
    }
    identity = render_identity(inst)
    identity_dir = ROOT / (inst.get("pie_root") or "harness/pie") / agent / "identity"
    for name, text in identity.items():
        writes[identity_dir / name] = text

    if args.dry_run:
        for path in writes:
            print(f"  would write {path.relative_to(ROOT)}")
        return 0

    for path, text in writes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(f"  wrote {path.relative_to(ROOT)}")
    try:
        os.chmod(ROOT / ".env", 0o600)
    except OSError:
        pass

    print()
    print("Next (on this machine):")
    print(f"  1. create the database + the roles:")
    print(f"       sudo -u postgres psql -c \"CREATE DATABASE {inst['database']} "
          f"OWNER {inst['operator_role']};\"")
    print(f"       sudo -u postgres psql -d {inst['database']} "
          f"-v agent_name={agent} -f schema/init.sql")
    print(f"  2. fill in the placeholders in .env (the database password, the LLM")
    print(f"     key + base URL, the app secret, the TLS paths):")
    print(f"       grep REPLACE_ME .env")
    print(f"  3. apply the schema:  bash tools/migrate.sh {agent}")
    print(f"  4. start the harness (systemd --user): see systemd/ in the repo")
    print(f"  5. open http://{inst.get('bind', '127.0.0.1:5010')} and sign in")
    print()
    print("The agent's identity files (the sysadmin identity with the name "
          "filled in) are in:")
    print(f"  {identity_dir.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
