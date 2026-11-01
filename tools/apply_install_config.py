#!/usr/bin/env python3
"""Apply an install config to a harness checkout.

Per Mal 2026-10-25: a customer should be able to install the harness from a
clone plus ONE file (the install config) without an engineer on their
machine. This writes, from that one file:

  * config.yaml    — the harness config for THIS install (one agent, no
                     per-agent LLM overrides, the embedding block)
  * (no .env)      — the values live in config.yaml, which is the only
                     the rest left as clearly-marked placeholders
  * pie/<agent>/identity/  — the agent's identity files, from the default
                     template (the sysadmin identity) with the name filled in
  * schema/init.sql — the whole schema (the shared public tables and the
    agent's schema), applied once per agent

It never invents a secret: anything the operator has to choose is left as a
visible placeholder and reported at the end.

    python3 tools/apply_install_config.py install/agent.yaml
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from pathlib import Path

import yaml
from urllib.parse import quote

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


# A value that still looks like the EXAMPLE's placeholder is a placeholder.
# The example ships "sk-..." and "ep-...", which is NOT what "REPLACE_ME"
# means -- so an operator who ran `grep YOUR_ config.yaml` would not see the
# LLM key, would ship the literal "sk-...", and would get an opaque 401
# from the provider at runtime instead of a clear "fill this in".
PLACEHOLDER_VALUES = {"", "sk-...", "ep-...", "REPLACE_ME", "changeme", "..."}


def _is_placeholder(value: str) -> bool:
    return (value or "").strip() in PLACEHOLDER_VALUES


def _gen_password() -> str:
    """A password for a database role.

    URL-safe so it can go straight into the postgresql:// URL in
    config.yaml without encoding.
    """
    return secrets.token_urlsafe(18).replace("-", "_").replace("~", "_")


def render_config(inst: dict, generated: dict[str, str]) -> str:
    """config.yaml for this install -- ONE file, with the real values.

    The template (config.yaml.example) carries literal values, so this
    loads it as YAML, puts this install's values into it, and writes it
    back. Doing it on the parsed structure rather than by string
    substitution is what keeps the YAML valid whatever the values are.

    The per-agent LLM overrides are dropped: this install has one agent,
    so there is nothing to override -- and every agent now gets its own
    HTTP client on its own, whether it has a per_agent entry or not.
    """
    cfg = yaml.safe_load((ROOT / "config.yaml.example").read_text())

    agent = inst["agent_name"]
    db = inst["database"]
    llm = inst.get("llm") or {}
    emb = inst["embedding"]

    app = cfg["app"]
    app["agents"] = [agent]
    app["default_agent"] = agent
    app["bind"] = inst.get("bind", "127.0.0.1:5010")
    app["pie_root"] = inst.get("pie_root", "harness/pie")
    app["installation_database"] = db
    app["secret_key"] = generated["secret_key"]

    d = cfg["db"]
    runtime_role = inst.get("runtime_role", agent + "_app")
    d["runtime_role"] = runtime_role
    d["operator_role"] = inst.get("operator_role", agent)
    d["database"] = db
    d["url"] = (f"postgresql://{runtime_role}:"
                f"{quote(generated['runtime_password'], safe='')}"
                f"@127.0.0.1:5432/" + db)
    # An empty operator password means peer auth over the unix socket,
    # which is the normal case for a local install.
    d["operator_password"] = generated["operator_password"]

    if llm:
        cfg["llm"]["adapter"] = llm.get("adapter", "minimax_m31_flash")
        cfg["llm"]["api_key"] = llm.get("api_key", "YOUR_LLM_API_KEY_HERE")
        cfg["llm"]["base_url"] = llm.get("base_url", "https://api.minimax.io/anthropic")
        cfg["llm"]["model"] = llm.get("model", "MiniMax-M3.1-Flash")
        cfg["llm"]["max_tokens"] = int(llm.get("max_tokens", 524288))
        cfg["llm"]["timeout_s"] = int(llm.get("timeout_s", 180))
        cfg["llm"]["temperature"] = float(llm.get("temperature", 0.7))

    cfg["embedding"]["base_url"] = emb["base_url"]
    cfg["embedding"]["api_key"] = emb.get("api_key", "YOUR_EMBEDDING_API_KEY_HERE")
    cfg["embedding"]["model"] = emb.get("model", "Qwen/Qwen3-Embedding-4B")
    cfg["embedding"]["dimensions"] = int(emb.get("dimensions", 1024))
    cfg["embedding"]["timeout_s"] = int(emb.get("timeout_s", 30))

    # The per-agent block goes: one agent, one API key. (And every agent
    # gets its own HTTP client regardless -- see app/extensions.py.)
    cfg["llm"].pop("per_agent", None)

    cfg["security"]["internal_token"] = generated["internal_token"]
    cfg["reverie"]["user_email"] = inst.get("operator_email", "")

    header = (
        "# The harness config for THIS install. Generated by\n"
        "#   tools/apply_install_config.py from config.yaml.example\n"
        "#\n"
        "# It holds the API keys and the database password, so it is mode\n"
        "# 0600 and it is NOT tracked in git. Edit it in place.\n"
        "#\n"
        "# config.yaml.example is the annotated template this came from --\n"
        "# it explains what each value is for. This file has the same\n"
        "# structure with this install's real values in it.\n"
        "#\n"
    )
    body = yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False,
                           width=100, allow_unicode=True)
    return header + body


def generate_secrets(inst: dict) -> dict:
    """The secrets this install needs, generated rather than guessed.

    The runtime role's password and the operator role's password have to
    agree with the DATABASE_URL in config.yaml, and a human copying a
    password between two places gets it wrong. The session secret and the
    internal token are generated because there is nothing to decide
    about them and a predictable one is a weak one.

    Re-running the applier on an install that is ALREADY WORKING must
    not rotate the database password: the roles were created with the
    old one, so a new password in config.yaml would lock the app out of
    its own database. An existing, non-placeholder password is KEPT --
    and the operator password with it, so the two still agree.
    """
    agent = inst["agent_name"]
    runtime_role = inst.get("runtime_role", agent + "_app")

    runtime_pw = _gen_password()
    operator_pw = _gen_password()

    existing_dsn = ""
    existing_op = ""
    cfg_path = ROOT / "config.yaml"
    if cfg_path.exists():
        try:
            old = yaml.safe_load(cfg_path.read_text()) or {}
        except (OSError, Exception):
            old = {}
        old_db = old.get("db") or {}
        m = re.search(r"^postgresql://[^:]+:([^@]*)@", str(old_db.get("url") or ""), re.M)
        if m:
            existing_dsn = m.group(1)
        existing_op = str(old_db.get("operator_password") or "").strip().strip("'\"")
    if existing_dsn and existing_dsn != "REPLACE_ME":
        runtime_pw = existing_dsn
        if existing_op:
            operator_pw = existing_op

    return {
        "runtime_password": runtime_pw,
        "operator_password": operator_pw,
        "secret_key": secrets.token_hex(32),
        "internal_token": secrets.token_hex(32),
    }


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

    # the quoted-identifier form -- :"agent_name" -> "agent"
    text = text.replace(':"agent_name"', f'"{agent}"')
    # the string-literal form -- :'agent_name' -> 'agent'
    text = text.replace(":'agent_name'", f"'{agent}'")
    # the \gexec index builders -- :'agent_name' || '_suffix' -> 'agent' || '_suffix'
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


def print_next_steps(inst: dict, generated: dict[str, str]) -> None:
    """Print the steps the operator still has to run, on this machine.

    Kept separate from main() so it can be tested WITHOUT writing into
    the checkout: main() writes config.yaml and the identity files
    into ROOT, and a test that did that would overwrite the repo.
    """
    agent = inst["agent_name"]
    op_role = inst["operator_role"]
    rt_role = inst.get("runtime_role", agent + "_app")
    db = inst["database"]
    print("Next (on this machine):")
    print(f"  1. create the ROLES and the DATABASE, and install the EXTENSIONS")
    print(f"     (once, as a superuser -- pgvector's vector extension is not a")
    print(f"     trusted extension, so only a superuser may create it).")
    print(f"     The passwords below are the ones already written into config.yaml:")
    print(f"       sudo -u postgres psql -c \"CREATE ROLE {op_role} LOGIN "
          f"PASSWORD '{generated['operator_password']}';\"")
    print(f"       sudo -u postgres psql -c \"CREATE ROLE {rt_role} LOGIN "
          f"PASSWORD '{generated['runtime_password']}' NOSUPERUSER NOCREATEDB "
          f"NOCREATEROLE;\"")
    print(f"       sudo -u postgres psql -c \"CREATE DATABASE {db} OWNER {op_role};\"")
    print(f"       sudo -u postgres psql -d {db} -f schema/extensions.sql")
    print(f"  2. create the Python environment (the service runs from it):")
    print(f"       python3 -m venv .venv")
    print(f"       .venv/bin/pip install -r requirements.txt")
    print(f"  3. fill in the remaining placeholders in config.yaml (the LLM")
    print(f"     key, the embedding key, the TLS certificate and key):")
    print(f"       grep -n YOUR_ config.yaml")
    print(f"  4. apply the schema AND the runtime role's grants:")
    print(f"       bash tools/migrate.sh {agent}")
    print(f"  5. create the first admin:")
    print(f"       .venv/bin/python3 tools/create_user.py --email you@example.com \\")
    print(f"              --password '...' --role admin")
    print(f"  6. start the harness (systemd --user):")
    print(f"       mkdir -p ~/.config/systemd/user")
    print(f"       cp systemd/harness.service ~/.config/systemd/user/")
    print(f"       systemctl --user daemon-reload")
    print(f"       systemctl --user enable --now harness.service")
    print(f"  7. open http://{inst.get('bind', '127.0.0.1:5010')} and sign in")

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("install_config", help="e.g. install/agent.yaml")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing config.yaml")
    args = ap.parse_args()

    inst = load_install(Path(args.install_config))
    agent = inst["agent_name"]
    print(f"install config : {args.install_config}")
    print(f"agent          : {agent}")
    print(f"embedding      : {inst['embedding']['base_url']}")
    print(f"database       : {inst['database']} (schema {inst['schema']})")

    generated = generate_secrets(inst)
    writes = {
        ROOT / "config.yaml": render_config(inst, generated),
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

    # config.yaml is the INSTALL's own config: it is not tracked in git,
    # so it is the only copy of this install's settings. Overwriting it
    # silently would destroy an existing install's configuration.
    if not args.force:
        clash = [p for p in (ROOT / "config.yaml",) if p.exists()]
        if clash:
            die("refusing to overwrite " + ", ".join(str(p.relative_to(ROOT)) for p in clash)
                + " -- this install already has one. Pass --force if you "
                  "really mean to replace it.")

    for path, text in writes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(f"  wrote {path.relative_to(ROOT)}")
    # config.yaml holds the API keys and the database password.
    try:
        os.chmod(ROOT / "config.yaml", 0o600)
    except OSError:
        pass

    # The keys the operator still has to fill in. A placeholder that
    # reaches the app is an opaque 401 from the provider at runtime, so
    # it is called out here, by name, at install time.
    written = yaml.safe_load(writes[ROOT / "config.yaml"]) or {}
    for section, key in (("llm", "api_key"), ("embedding", "api_key")):
        val = str((written.get(section) or {}).get(key) or "")
        if _is_placeholder(val):
            print(f"  NOTE: {section}.{key} is still the example value "
                  f"({val!r}). Edit config.yaml and set the real key.")

    print_next_steps(inst, generated)
    print()
    print("The agent's identity files (the sysadmin identity with the name "
          "filled in) are in:")
    print(f"  {identity_dir.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
