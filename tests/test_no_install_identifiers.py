"""The public repository must not carry one install's identifying details.

WHY THIS TEST EXISTS
--------------------
This repository is public.  It is the code for a harness that is
deployed to more than one machine, by more than one operator, and it is
read by strangers.  A comment that names the Unix account, the mailbox
or the hostname of ONE install does not stay in that install: it ships,
it is indexed, and it tells a reader who runs this and who to write to.

So the shipped source and the repository's own documents must describe
the harness GENERICALLY -- "a production install", "the operator",
"the runtime role" -- and never name a specific machine.

HOW IT IS PINNED -- AND WHY THERE IS NO LIST OF NAMES
------------------------------------------------------
The naive version of this test is a list of the accounts and mailboxes
to ban.  That list IS the leak: it names them.

So every rule below is generic.  None of them contains an account
name, a hostname or an address, and neither does this file.  The
rules are:

* **mailbox** -- an address is acceptable only if its domain is a
  placeholder (``example.com`` and friends) or its local part is a
  ROLE word (``security``, ``support``, ``admin`` ...).  A role word
  says "this is a function"; a person's name says "this is a person",
  and a public repository must not publish a person.
* **home path** -- ``/home/<something>`` is acceptable only if
  ``<something>`` is a placeholder (``x``, ``<user>``, ``$USER`` ...).
  A concrete account in a path identifies the machine.
* **sudo switch** -- ``sudo -u <account>`` is acceptable only for a
  well-known system account (``postgres``, ``root``) or a placeholder.
* **systemd user unit** -- ``<account>@<uid>.service`` names the Unix
  account a unit runs as, which is the account of one install.

WHAT THIS DOES NOT COVER, STATED PLAINLY
-----------------------------------------
It does not look for hostnames: a generic hostname shape also matches
hex colours, unicode escapes and test-fixture names, so it is too
noisy to gate on.  Hostnames in the shipped tree are caught by review
and by the home-path rule, which is where a hostname actually shows up.

The checks run over the source tree, the tests and the repository's own
documents (README, SECURITY, CONTRIBUTING, the workflows).  They do NOT
run over ``pie/`` -- each agent's identity directory is its own
repository and is gitignored -- nor over ``agent_template/``, which is
the per-install template written at install time.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

# --- trees that ship ------------------------------------------------------
SOURCE_TREES = ("app", "scripts", "schema", "tools", "tests")

# --- repository-level documents that ship ---------------------------------
DOC_FILES = ("README.md", "SECURITY.md", "CONTRIBUTING.md")

WORKFLOW_TREE = ".github"

# This file is a guard, and it necessarily contains the shapes it looks
# for.  It is excluded from its own scan.
GUARD_FILES = ("test_no_install_identifiers.py",)

TEXT_SUFFIXES = (".py", ".js", ".css", ".html", ".sh", ".yml", ".yaml", ".sql", ".md", ".txt")

# --- the rules ------------------------------------------------------------

EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Domains that mean "this is a placeholder".
PLACEHOLDER_DOMAINS = {
    "example.com", "example.org", "example.net", "example.test",
    "example", "invalid", "localhost", "test", "users.noreply.github.com",
}

# Local parts that name a FUNCTION rather than a person.
ROLE_LOCAL_PARTS = {
    "admin", "abuse", "billing", "bounce", "bouncer", "contact", "devops",
    "donotreply", "hello", "help", "hostmaster", "info", "mail", "mailer-daemon",
    "no-reply", "noreply", "ops", "postmaster", "root", "sales", "security",
    "support", "sysadmin", "team", "webmaster",
}

HOME_PATH_RX = re.compile(r"/home/([A-Za-z0-9_.${}<>-]+)")

# Placeholder account names that may appear in a path.
HOME_PATH_PLACEHOLDERS = {
    "x", "user", "users", "youruser", "your-user", "your_user", "username",
    "account", "harness", "someuser", "some_user", "ubuntu", "deploy",
    "operator", "svc", "service", "runner", "app", "example", "test",
}

SUDO_RX = re.compile(r"sudo\s+(?:-n\s+)?-u\s+([A-Za-z0-9_.-]+)")

# Accounts every Linux box has, and which therefore identify nothing.
SYSTEM_ACCOUNTS = {"postgres", "root", "nobody", "mysql", "systemd-network"}

SYSTEMD_USER_UNIT_RX = re.compile(r"[A-Za-z0-9_.-]+@[0-9]{3,6}\.service")

# A constant whose NAME says it holds install identifiers, assigned a
# collection.  This is the shape of the historical leak: a module-level
# tuple of the Unix accounts this code is deployed under.  The name is
# generic -- it says what the collection IS, never what is in it -- so
# pinning it publishes nothing.
IDENTIFIER_LIST_RX = re.compile(
    r"^[ \t]*[A-Z_]*(?:ACCOUNTS?|UNIX_USERS|DEPLOY_USERS|HOSTNAMES|"
    r"SERVERS|MAILBOXES|INSTALLS|MACHINES)[A-Z_]*[ \t]*=[ \t]*[\[\(\{]",
    re.M,
)

# --- the agent roster -----------------------------------------------------
#
# An agent name is not a secret, but a roster IS an install's identity:
# one install's three agent names tell a reader what that install is.
# The template ships ONE agent, `peribot` (config.yaml.example), and every
# other name in the shipped tree has to be an obvious placeholder.
#
# The roster is therefore pinned by POSITION: a name in one of the four
# places where the code means "an agent name" must be the template's agent
# or a placeholder.  That catches a real roster without the test ever
# naming one.

# `<agent>.conversations`, `<agent>.turns`, ... -- the schema-qualified
# form, which is how an agent name reaches SQL.
SCHEMA_QUALIFIED_RX = re.compile(
    r"\b([a-z][a-z0-9_]{1,24})\.(?:conversations|turns|memory_chunks|"
    r"memories|memory_chunks_pkey)\b"
)

# `agent_name="x"`, `"agent": "x"`, `HARNESS_AGENT", "x"` -- the three
# forms the code passes a name in.
AGENT_NAME_POSITIONS_RX = re.compile(
    r"agent_name\s*=\s*[\"']([a-z][a-z0-9_]*)[\"']"
    r"|[\"']agent[\"']\s*:\s*[\"']([a-z][a-z0-9_]*)[\"']"
    r"|HARNESS_AGENT[A-Z_]*[\"']\s*,\s*[\"']([a-z][a-z0-9_]*)[\"']"
)

# An agent name as the PREFIX of a generated identifier -- the live index
# names are `<agent>_memories_created_at_idx`, so a rewrite rule that
# matches them has to know the agent's name.  A rewrite rule that
# hardcodes one instead of using the parameter is how one install's agent
# name ends up in the shipped tooling.
GENERATED_NAME_PREFIX_RX = re.compile(
    r"\(\s*([a-z][a-z0-9_]{1,24})_\\?w\+\s*\)"
)

# Names that are obviously placeholders rather than an install's roster.
GENERIC_AGENT_NAMES = {
    "alpha", "beta", "gamma", "example", "examples",
    "first", "second", "third",
    "agent_a", "agent_b", "agent_one", "agent_two",
    "mine", "other", "another", "nosuchagent", "testbot",
}


def _shipped_files():
    """Every file that ships with the repository, guards excluded."""
    seen = set()
    for tree in SOURCE_TREES:
        base = ROOT / tree
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in TEXT_SUFFIXES:
                seen.add(path)
    for name in DOC_FILES:
        path = ROOT / name
        if path.exists():
            seen.add(path)
    wf = ROOT / WORKFLOW_TREE
    if wf.exists():
        for path in wf.rglob("*"):
            if path.is_file() and path.suffix in TEXT_SUFFIXES:
                seen.add(path)
    return sorted(p for p in seen if p.name not in GUARD_FILES)


def _corpus():
    """Read every shipped file once: {path: text}."""
    return {p: p.read_text(errors="replace") for p in _shipped_files()}


def test_no_personal_mailbox_is_published():
    offenders = []
    for path, text in _corpus().items():
        for raw in EMAIL_RX.findall(text):
            local, _, domain = raw.rpartition("@")
            domain = domain.lower()
            local = local.lower()
            if domain in PLACEHOLDER_DOMAINS:
                continue
            if local in ROLE_LOCAL_PARTS:
                continue
            offenders.append(f"{path.relative_to(ROOT)}: {raw}")
    assert not offenders, (
        "the shipped source must not publish a personal mailbox: "
        f"{offenders}. A public repository must not publish a person. "
        "Use a role address (security@, support@) or a placeholder "
        "(you@example.com), and point readers at the GitHub private "
        "advisory form."
    )


def test_no_install_account_path_is_published():
    offenders = []
    for path, text in _corpus().items():
        for raw in HOME_PATH_RX.findall(text):
            if raw.lower() in HOME_PATH_PLACEHOLDERS:
                continue
            if raw.startswith(("<", "$", "{")):
                continue
            offenders.append(f"{path.relative_to(ROOT)}: /home/{raw}")
    assert not offenders, (
        "the shipped source must not contain a path into one install's "
        f"home directory: {offenders}. A Unix account identifies the "
        "machine it is on, and a public repository must not name the "
        "accounts of the machines it runs on."
    )


def test_no_sudo_switch_to_a_named_account():
    offenders = []
    for path, text in _corpus().items():
        for raw in SUDO_RX.findall(text):
            if raw.lower() in SYSTEM_ACCOUNTS:
                continue
            if raw.lower() in HOME_PATH_PLACEHOLDERS or raw.startswith(("<", "$")):
                continue
            offenders.append(f"{path.relative_to(ROOT)}: sudo -u {raw}")
    assert not offenders, (
        "the shipped source must not switch to a named Unix account: "
        f"{offenders}. Use the runtime role, or a placeholder."
    )


def test_no_systemd_user_unit_names_an_account():
    offenders = []
    for path, text in _corpus().items():
        for raw in SYSTEMD_USER_UNIT_RX.findall(text):
            offenders.append(f"{path.relative_to(ROOT)}: {raw}")
    assert not offenders, (
        "a systemd user unit names the Unix account it runs as, which is "
        f"one install's account: {offenders}."
    )


def test_no_constant_lists_install_identifiers():
    """A module-level list of accounts is the shape of the old leak.

    The code once carried a constant holding the Unix accounts this
    repository was deployed under -- written down, in the repository,
    so that a test could assert they did not appear in a comment.  The
    list was the leak; the assertion was fine.

    The rule is on the NAME and the SHAPE, never on the contents: a
    constant called ``ACCOUNTS`` (or ``HOSTNAMES``, ``SERVERS`` ...)
    that is assigned a collection is a list of one install's
    identifiers, and the fix is to pin the property by shape instead.
    """
    offenders = []
    for path, text in _corpus().items():
        if IDENTIFIER_LIST_RX.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        "the shipped source must not carry a list of install identifiers "
        f"(accounts, hosts, servers): {offenders}. Pin the property by "
        "shape instead of naming the values."
    )


def test_agent_roster_is_not_hardcoded():
    """The agent roster is configuration, not code.

    One install serves one agent; another serves several.  A roster
    baked into a test or a doc goes stale the moment a second install
    exists, and it names that install's agents.  The roster belongs in
    ``config.yaml`` (``agents:``) and nowhere else.
    """
    doc = (ROOT / "config.yaml.example").read_text()
    assert re.search(r"^\s*agents:\s*$", doc, re.M), (
        "config.yaml.example must carry the agent roster -- that is where "
        "the roster belongs"
    )
    assert "default_agent:" in doc


def _template_agent_names():
    """The agent names the template ships, read from the example config."""
    doc = (ROOT / "config.yaml.example").read_text()
    names = set()
    default = re.search(r"^\s*default_agent:\s*([A-Za-z0-9_]+)\s*$", doc, re.M)
    if default:
        names.add(default.group(1).lower())
    block = re.search(r"^\s*agents:\s*\n((?:\s*-\s*[A-Za-z0-9_]+\s*\n)+)", doc, re.M)
    if block:
        for line in block.group(1).splitlines():
            names.add(line.strip().lstrip("-").strip().lower())
    return names


def test_no_install_agent_roster_is_published():
    """Only the template's agent, or an obvious placeholder.

    The template ships one agent.  A name in an agent-name POSITION that
    is neither that agent nor a placeholder is one install's roster,
    and the repository must not carry it.
    """
    allowed = _template_agent_names() | GENERIC_AGENT_NAMES
    assert allowed, "the template's agent roster could not be read"

    # The schema-qualified form only means a schema in the files that
    # speak SQL.  In JavaScript `data.turns` is an object property, not
    # a Postgres schema, so the same shape there is a false positive.
    sql_suffixes = {".py", ".sql", ".sh"}

    offenders = []
    for path, text in _corpus().items():
        if path.suffix in sql_suffixes:
            for match in SCHEMA_QUALIFIED_RX.finditer(text):
                name = match.group(1).lower()
                if name not in allowed:
                    offenders.append(f"{path.relative_to(ROOT)}: {name}")
        for match in AGENT_NAME_POSITIONS_RX.finditer(text):
            for group in match.groups():
                if group is None:
                    continue
                name = group.lower()
                if name not in allowed:
                    offenders.append(f"{path.relative_to(ROOT)}: {name}")
        if path.suffix in sql_suffixes:
            for match in GENERATED_NAME_PREFIX_RX.finditer(text):
                name = match.group(1).lower()
                if name not in allowed:
                    offenders.append(f"{path.relative_to(ROOT)}: {name}")
    assert not offenders, (
        "the shipped source must not carry one install's agent roster: "
        f"{sorted(set(offenders))}. The template ships "
        f"{sorted(_template_agent_names())}; anything else in an "
        "agent-name position must be an obvious placeholder."
    )
