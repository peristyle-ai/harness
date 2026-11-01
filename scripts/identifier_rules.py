"""The shapes that identify one install -- as rules, never as names.

WHY THIS FILE EXISTS
--------------------
This repository is public.  It is the code for a harness that is
deployed to more than one machine, by more than one operator, and it
is read by strangers.  A comment that names the Unix account, the
mailbox or the hostname of ONE install does not stay in that install:
it ships, it is indexed, and it tells a reader who runs this and who
to write to.

So the shipped source and the repository's own documents must describe
the harness GENERICALLY -- "a production install", "the operator",
"the runtime role" -- and never name a specific machine.

WHY THERE IS NO LIST OF NAMES
-----------------------------
The naive version of this is a list of the accounts and mailboxes to
ban.  That list IS the leak: it names them.

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
* **privileged switch** -- switching to a named Unix account is
  acceptable only for a well-known system account (``postgres``,
  ``root``) or a placeholder.
* **systemd user unit** -- ``<account>@<uid>.service`` names the Unix
  account a unit runs as, which is the account of one install.
* **identifier list** -- a constant whose NAME says it holds install
  identifiers, assigned a collection, is a list of one install's
  identifiers.  The rule is on the NAME and the SHAPE, never on the
  contents: the list was the leak, and pinning the property by shape
  keeps the values out of the repository.
* **agent roster** -- an agent name is not a secret, but a roster IS
  an install's identity.  A name in one of the places where the code
  means "an agent name" must be the template's agent or an obvious
  placeholder.

WHERE THE RULES ARE APPLIED
---------------------------
* ``tests/test_no_install_identifiers.py`` -- the working tree.
* ``scripts/check_public_refs.py`` -- every public BRANCH this
  repository keeps.  The working tree is not the whole public
  repository: a branch is a public page, and a branch that was merged
  and then left behind still publishes the tree it was merged from.

The rules live here, once, because two copies of "the rules" is how
they drift apart -- and a rule that only one of them applies is a
rule that is not enforced.

The tree check runs over the source tree, the tests, the identity
template and the repository's own documents (README, SECURITY,
CONTRIBUTING, the workflows).  It does NOT run over ``pie/`` -- each
agent's identity directory is its own repository and is gitignored, so it
is not in this repository.  ``agent_template/`` IS in this repository
(it is tracked, and every install starts from it), so it is scanned.

The branch gate reads a whole tree, so it scans everything in it; the
working-tree guard lists the trees it reads.  The two scopes are kept
the same on purpose: a rule that only one of them applies is a rule that
is not enforced.

WHAT THIS DOES NOT COVER, STATED PLAINLY
-----------------------------------------
It does not look for hostnames: a generic hostname shape also matches
hex colours, unicode escapes and test-fixture names, so it is too
noisy to gate on.  Hostnames in the shipped tree are caught by review
and by the home-path rule, which is where a hostname actually shows up.
"""

from __future__ import annotations

import re
from pathlib import Path

# --- the rules ----------------------------------------------------------

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

# --- the agent roster ----------------------------------------------------
#
# An agent name is not a secret, but a roster IS an install's identity:
# one install's agent names tell a reader what that install is.  The
# template ships ONE agent (config.yaml.example), and every other name
# in the shipped tree has to be an obvious placeholder.
#
# The roster is therefore pinned by POSITION: a name in one of the
# places where the code means "an agent name" must be the template's
# agent or a placeholder.  That catches a real roster without the rule
# set ever naming one.

# `<agent>.conversations`, `<agent>.turns`, ... -- the schema-qualified
# form, which is how an agent name reaches SQL.
SCHEMA_QUALIFIED_RX = re.compile(
    r"\b([a-z][a-z0-9_]{1,24})\.(?:conversations|turns|memory_chunks|"
    r"memories|memory_chunks_pkey)\b"
)

# `agent_name="x"`, `"agent": "x"`, `HARNESS_AGENT", "x"` -- the forms
# the code passes a name in.
AGENT_NAME_POSITIONS_RX = re.compile(
    r"agent_name\s*=\s*[\"']([a-z][a-z0-9_]*)[\"']"
    r"|[\"']agent[\"']\s*:\s*[\"']([a-z][a-z0-9_]*)[\"']"
    r"|HARNESS_AGENT[A-Z_]*[\"']\s*,\s*[\"']([a-z][a-z0-9_]*)[\"']"
)

# An agent name as the PREFIX of a generated identifier -- the live
# index names are `<agent>_memories_created_at_idx`, so a rewrite rule
# that matches them has to know the agent's name.  A rewrite rule that
# hardcodes one instead of using the parameter is how one install's
# agent name ends up in the shipped tooling.
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

# The schema-qualified form only means a schema in the files that speak
# SQL.  In JavaScript `data.turns` is an object property, not a
# Postgres schema, so the same shape there is a false positive.
SQL_SUFFIXES = {".py", ".sql", ".sh"}

# --- the rule names ----------------------------------------------------

RULE_MAILBOX = "mailbox"
RULE_HOME_PATH = "home path"
RULE_PRIVILEGED_SWITCH = "privileged switch"
RULE_SYSTEMD_UNIT = "systemd user unit"
RULE_IDENTIFIER_LIST = "identifier list"
RULE_AGENT_ROSTER = "agent roster"

# --- how a finding is REPORTED -------------------------------------------
#
# A gate that prints the identifier it found has republished it.  A CI
# log on a public repository is public, indexed, and immutable: it
# outlives the branch that carried the identifier, and it outlives the
# commit that removed it.  So the gates report WHERE and WHAT KIND of
# finding it is, and never the value.
#
# The value is still available in-process -- ``find_offenders`` returns
# it -- for anyone who wants to look at the file themselves.  It is
# simply never printed.

RULE_DESCRIPTIONS = {
    RULE_MAILBOX: "a personal mailbox",
    RULE_HOME_PATH: "a path into one install's home directory",
    RULE_PRIVILEGED_SWITCH: "a switch to a named Unix account",
    RULE_SYSTEMD_UNIT: "a systemd user unit naming an account",
    RULE_IDENTIFIER_LIST: "a constant assigned a collection",
    RULE_AGENT_ROSTER: "an agent name in an agent-name position",
}


def describe(rule: str) -> str:
    """What a rule is, without the value it was found in.

    This is the only thing a gate may print about a finding.
    """
    return RULE_DESCRIPTIONS.get(rule, "an install identifier")


# --- what is not scanned ------------------------------------------------

# These files hold the shapes they look for, so they are excluded from
# their own scan.  The exclusion is by basename: the rules module and
# the test that applies it are the two files that cannot obey them.
#
# ``test_check_commit_identities.py`` is here for the same reason: it
# plants an address, to prove that the identity scanner catches one.  A
# file that has to contain the shape to prove the rule works cannot also
# obey the rule about that shape.
GUARD_FILES = ("test_no_install_identifiers.py", "identifier_rules.py",
               "test_check_commit_identities.py")

# The file the template's agent roster is read from.
CONFIG_EXAMPLE = "config.yaml.example"


def template_agent_names(doc: str) -> set[str]:
    """The agent names the template ships, read from the example config.

    The template ships one agent.  Everything else in an agent-name
    position has to be an obvious placeholder.
    """
    names: set[str] = set()
    default = re.search(r"^\s*default_agent:\s*([A-Za-z0-9_]+)\s*$", doc, re.M)
    if default:
        names.add(default.group(1).lower())
    block = re.search(r"^\s*agents:\s*\n((?:\s*-\s*[A-Za-z0-9_]+\s*\n)+)", doc, re.M)
    if block:
        for line in block.group(1).splitlines():
            names.add(line.strip().lstrip("-").strip().lower())
    return names


def template_agent_names_in(root: Path | str) -> set[str]:
    """The template's agent names, read from a checkout or a branch tree."""
    path = Path(root) / CONFIG_EXAMPLE
    if not path.exists():
        return set()
    return template_agent_names(path.read_text(errors="replace"))


def default_allowed_agents() -> set[str]:
    """The agent names this repository's own tree may name."""
    root = Path(__file__).resolve().parents[1]
    return template_agent_names_in(root) | GENERIC_AGENT_NAMES


def _suffix(path: Path | str | None) -> str:
    if path is None:
        return ""
    return Path(str(path)).suffix.lower()


def find_offenders(
    text: str,
    path: Path | str | None = None,
    allowed_agents: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Every rule violation in ``text``, as (rule, detail) pairs.

    One implementation, used by every surface that has to obey these
    rules: the working-tree guard and the public-branch gate.  Two
    implementations of "the rules" is how the two drift apart, and a
    rule that only one of them applies is a rule that is not enforced.

    ``path`` is the file the text came from, when the caller knows it.
    It decides whether the SQL-only agent shapes apply: ``data.turns``
    is a Postgres schema in a ``.py`` file and an object property in a
    ``.js`` file.  ``None`` means "unknown", and is treated as SQL.

    ``allowed_agents`` is the set of agent names that file may name.
    It defaults to the template's agent plus the obvious placeholders.
    """
    out: list[tuple[str, str]] = []

    for raw in EMAIL_RX.findall(text):
        local, _, domain = raw.rpartition("@")
        if domain.lower() in PLACEHOLDER_DOMAINS:
            continue
        if local.lower() in ROLE_LOCAL_PARTS:
            continue
        out.append((RULE_MAILBOX, raw))

    for raw in HOME_PATH_RX.findall(text):
        if raw.lower() in HOME_PATH_PLACEHOLDERS:
            continue
        if raw.startswith(("<", "$", "{")):
            continue
        out.append((RULE_HOME_PATH, f"/home/{raw}"))

    for raw in SUDO_RX.findall(text):
        if raw.lower() in SYSTEM_ACCOUNTS:
            continue
        if raw.lower() in HOME_PATH_PLACEHOLDERS or raw.startswith(("<", "$")):
            continue
        out.append((RULE_PRIVILEGED_SWITCH, f"sudo -u {raw}"))

    for raw in SYSTEMD_USER_UNIT_RX.findall(text):
        out.append((RULE_SYSTEMD_UNIT, raw))

    if IDENTIFIER_LIST_RX.search(text):
        out.append((RULE_IDENTIFIER_LIST, "a constant assigned a collection"))

    allowed = set(allowed_agents) if allowed_agents is not None else default_allowed_agents()

    if path is None or _suffix(path) in SQL_SUFFIXES:
        for match in SCHEMA_QUALIFIED_RX.finditer(text):
            name = match.group(1).lower()
            if name not in allowed:
                out.append((RULE_AGENT_ROSTER, name))
        for match in GENERATED_NAME_PREFIX_RX.finditer(text):
            name = match.group(1).lower()
            if name not in allowed:
                out.append((RULE_AGENT_ROSTER, name))

    for match in AGENT_NAME_POSITIONS_RX.finditer(text):
        for group in match.groups():
            if group is None:
                continue
            name = group.lower()
            if name not in allowed:
                out.append((RULE_AGENT_ROSTER, name))

    return out
