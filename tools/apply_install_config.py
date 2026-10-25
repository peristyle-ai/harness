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
import secrets
import sys
from pathlib import Path
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
# means -- so an operator who ran `grep REPLACE_ME .env` would not see the
# LLM key, would ship the literal "sk-...", and would get an opaque 401
# from the provider at runtime instead of a clear "fill this in".
PLACEHOLDER_VALUES = {"", "sk-...", "ep-...", "REPLACE_ME", "changeme", "..."}


def _is_placeholder(value: str) -> bool:
    return (value or "").strip() in PLACEHOLDER_VALUES


def _sh_quote(value: str) -> str:
    """Quote a value for the .env, which is SOURCED BY BASH.

    run.sh does `set -a; . ./.env; set +a`, so a value that is not a
    bare shell word is a bug: a `$` would be expanded, a space would
    split the line, and a quote would end it. Single quotes make the
    value literal -- the only escape needed inside them is a quote
    itself.
    """
    if value == "":
        return ""
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
        return value  # already safe unquoted
    return "'" + value.replace("'", "'\''") + "'"


def _gen_password() -> str:
    """A password for a database role.

    URL-safe so it can go straight into DATABASE_URL without encoding,
    and shell-safe so the .env does not need quoting.
    """
    return secrets.token_urlsafe(18).replace("-", "_").replace("~", "_")


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


def render_env(inst: dict) -> tuple[str, dict[str, str]]:
    """The .env for this install, and the secrets generated into it.

    The generated passwords are returned so the printed steps can
    carry the exact CREATE ROLE commands -- the operator must not
    have to copy a password between two places by hand.
    """
    src = (ROOT / ".env.example").read_text()
    emb = inst["embedding"]
    agent = inst["agent_name"]
    db = inst["database"]

    def set_var(text: str, name: str, value: str) -> str:
        # The value is quoted for bash: the .env is SOURCED, not parsed.
        q = _sh_quote(value)
        if re.search(rf"^{re.escape(name)}=.*$", text, flags=re.M):
            return re.sub(
                rf"^{re.escape(name)}=.*$", lambda _m: f"{name}={q}",
                text, count=1, flags=re.M,
            )
        return text.rstrip() + f"\n{name}={q}\n"

    # --- the install's own values -----------------------------------------
    llm = inst.get("llm") or {}
    if llm:
        src = set_var(src, "LLM_ADAPTER", llm.get("adapter", "minimax_m31_flash"))
        # A value that is still the EXAMPLE's placeholder is NOT a key.
        # Leaving "sk-..." in place would sail past `grep REPLACE_ME .env`
        # and fail at runtime with an opaque provider 401.
        api_key = llm.get("api_key", "")
        src = set_var(src, "LLM_API_KEY",
                      "REPLACE_ME" if _is_placeholder(api_key) else api_key)
        base_url = llm.get("base_url", "")
        src = set_var(src, "LLM_BASE_URL",
                      "REPLACE_ME" if _is_placeholder(base_url) else base_url)
        src = set_var(src, "LLM_MODEL", llm.get("model", "MiniMax-M3.1-Flash"))
        src = set_var(src, "LLM_MAX_TOKENS", str(llm.get("max_tokens", 524288)))
        src = set_var(src, "LLM_TIMEOUT_S", str(llm.get("timeout_s", 180)))
        src = set_var(src, "LLM_TEMPERATURE", str(llm.get("temperature", 0.7)))
    src = set_var(src, "EMBEDDING_BASE_URL", emb["base_url"])
    src = set_var(src, "EMBEDDING_MODEL", emb.get("model", "Qwen/Qwen3-Embedding-4B"))
    src = set_var(src, "EMBEDDING_DIMENSIONS", str(emb.get("dimensions", 1024)))
    emb_key = emb.get("api_key", "")
    src = set_var(src, "EMBEDDING_API_KEY",
                  "REPLACE_ME" if _is_placeholder(emb_key) else emb_key)
    src = set_var(src, "EMBEDDING_TIMEOUT_S", str(emb.get("timeout_s", 30)))
    src = set_var(src, "HARNESS_DEFAULT_AGENT", agent)
    src = set_var(src, "HARNESS_BIND", inst.get("bind", "127.0.0.1:5010"))
    src = set_var(src, "HARNESS_DB", db)
    src = set_var(src, "HARNESS_INSTALLATION_DATABASE", db)
    src = set_var(src, "HARNESS_PIE_ROOT", inst.get("pie_root", "harness/pie"))
    src = set_var(src, "APP_DB_OPERATOR_ROLE", inst.get("operator_role", agent))
    src = set_var(src, "APP_DB_RUNTIME_ROLE", inst.get("runtime_role", f"{agent}_app"))

    # --- the database passwords: GENERATED, not guessed -------------------
    # The operator role's password and the runtime role's password are
    # chosen here, not by the operator: they have to agree with the
    # DATABASE_URL in this same file, and a human copying a password
    # between two places gets it wrong. The .env is chmod 600 and is
    # gitignored, so the passwords live there.
    runtime_pw = _gen_password()
    operator_pw = _gen_password()
    runtime_role = inst.get("runtime_role", agent + "_app")

    # Re-running the applier on an install that is ALREADY WORKING must
    # not rotate the database password: the roles were created with the
    # old one, so a new password in the .env locks the app out of its
    # own database. So an existing, non-placeholder password is KEPT --
    # and the operator password is kept with it, so the two still agree.
    existing_dsn = ""
    existing_op = ""
    env_path = ROOT / ".env"
    if env_path.exists():
        try:
            old_env = env_path.read_text()
        except OSError:
            old_env = ""
        m = re.search(r"^DATABASE_URL=postgresql://[^:]+:([^@]*)@", old_env, re.M)
        if m:
            existing_dsn = m.group(1)
        m = re.search(r"^APP_DB_OPERATOR_PASSWORD=(.+)$", old_env, re.M)
        if m:
            existing_op = m.group(1).strip().strip("'\"")
    if existing_dsn and existing_dsn != "REPLACE_ME":
        runtime_pw = existing_dsn
        if existing_op:
            operator_pw = existing_op

    src = set_var(src, "DATABASE_URL",
                  f"postgresql://{runtime_role}:{quote(runtime_pw, safe='')}"
                  "@127.0.0.1:5432/" + db)
    src = set_var(src, "APP_DB_OPERATOR_PASSWORD", operator_pw)

    # --- the things the operator must choose -------------------------------
    if not llm:
        src = set_var(src, "LLM_API_KEY", "REPLACE_ME")
        src = set_var(src, "LLM_BASE_URL", "REPLACE_ME")
    # The app's own secret: generated, because there is nothing to decide
    # about it and a predictable one is a weak one.
    src = set_var(src, "HARNESS_SECRET_KEY", secrets.token_hex(32))
    # The TLS pair is the operator's: it is the certificate for THEIR
    # hostname, so it cannot be generated here.
    src = set_var(src, "HARNESS_TLS_CERT", "REPLACE_ME")
    src = set_var(src, "HARNESS_TLS_KEY", "REPLACE_ME")
    src = set_var(src, "REVERIE_USER_EMAIL", inst.get("operator_email", ""))

    # The per-agent LLM overrides belong to the OTHER agents: render_config
    # dropped config.yaml's llm.per_agent block (this install has one
    # agent), so the .env must not carry another agent's variables either.
    # A var named <AGENT>_LLM_* is dropped unless <AGENT> is this
    # install's agent.
    own = f"{agent.upper()}_LLM_"
    per_agent = re.compile(r"^[A-Z0-9_]+_LLM_[A-Z0-9_]+=")
    kept = []
    for line in src.splitlines():
        if per_agent.match(line) and not line.startswith(own):
            continue
        kept.append(line)
    src = "\n".join(kept) + "\n"
    return src, {
        "runtime_password": runtime_pw,
        "operator_password": operator_pw,
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


def print_next_steps(inst: dict, generated: dict[str, str]) -> None:
    """Print the steps the operator still has to run, on this machine.

    Kept separate from main() so it can be tested WITHOUT writing into
    the checkout: main() writes config.yaml, .env and the identity files
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
    print(f"     The passwords below are the ones already written into .env:")
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
    print(f"  3. fill in the remaining placeholders in .env (the LLM key, the")
    print(f"     embedding key, the TLS certificate and key):")
    print(f"       grep REPLACE_ME .env")
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
    ap.add_argument("install_config", help="e.g. install/peribot.yaml")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    inst = load_install(Path(args.install_config))
    agent = inst["agent_name"]
    print(f"install config : {args.install_config}")
    print(f"agent          : {agent}")
    print(f"embedding      : {inst['embedding']['base_url']}")
    print(f"database       : {inst['database']} (schema {inst['schema']})")

    env_text, generated = render_env(inst)
    writes = {
        ROOT / "config.yaml": render_config(inst),
        ROOT / ".env": env_text,
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

    print_next_steps(inst, generated)
    print()
    print("The agent's identity files (the sysadmin identity with the name "
          "filled in) are in:")
    print(f"  {identity_dir.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
