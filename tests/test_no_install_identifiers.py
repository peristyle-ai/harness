"""The shipped tree must not carry one install's identifying details.

WHY THIS TEST EXISTS
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

THE RULES LIVE IN ONE PLACE
----------------------------
The rules are in ``scripts/identifier_rules.py``, because they have to
hold in more than one place: here, on the working tree, and in
``scripts/check_public_refs.py``, on every public branch.  Two copies
of "the rules" is how they drift apart, and a rule that only one of
them applies is a rule that is not enforced.

That module also explains what the rules do NOT cover -- hostnames,
for instance -- and why a list of names is itself the leak.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import identifier_rules as rules  # noqa: E402

# --- trees that ship ------------------------------------------------------
SOURCE_TREES = ("app", "scripts", "schema", "tools", "tests")

# --- repository-level documents that ship ---------------------------------
DOC_FILES = ("README.md", "SECURITY.md", "CONTRIBUTING.md")

WORKFLOW_TREE = ".github"

TEXT_SUFFIXES = (".py", ".js", ".css", ".html", ".sh", ".yml", ".yaml", ".sql", ".md", ".txt")


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
    return sorted(p for p in seen if p.name not in rules.GUARD_FILES)


def _corpus():
    """Read every shipped file once: {path: text}."""
    return {p: p.read_text(errors="replace") for p in _shipped_files()}


def _offenders(rule):
    """Every file that breaks one rule, across the shipped corpus.

    The VALUE is not reported.  A failure message on a public CI is a
    public page, and it outlives the commit that fixed it -- so the
    message names the file and the kind of finding, and never the
    identifier itself.
    """
    out = []
    for path, text in _corpus().items():
        for found, _detail in rules.find_offenders(text, path=path):
            if found == rule:
                out.append(f"{path.relative_to(ROOT)}: {rules.describe(found)}")
    return out


def test_no_personal_mailbox_is_published():
    offenders = _offenders(rules.RULE_MAILBOX)
    assert not offenders, (
        "the shipped source must not publish a personal mailbox: "
        f"{offenders}. A public repository must not publish a person. "
        "Use a role address (security@, support@) or a placeholder "
        "(you@example.com), and point readers at the GitHub private "
        "advisory form."
    )


def test_no_install_account_path_is_published():
    offenders = _offenders(rules.RULE_HOME_PATH)
    assert not offenders, (
        "the shipped source must not contain a path into one install's "
        f"home directory: {offenders}. A Unix account identifies the "
        "machine it is on, and a public repository must not name the "
        "accounts of the machines it runs on."
    )


def test_no_privileged_switch_to_a_named_account():
    offenders = _offenders(rules.RULE_PRIVILEGED_SWITCH)
    assert not offenders, (
        "the shipped source must not switch to a named Unix account: "
        f"{offenders}. Use the runtime role, or a placeholder."
    )


def test_no_systemd_user_unit_names_an_account():
    offenders = _offenders(rules.RULE_SYSTEMD_UNIT)
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
    offenders = _offenders(rules.RULE_IDENTIFIER_LIST)
    assert not offenders, (
        "the shipped source must not carry a list of install identifiers "
        f"(accounts, hosts, servers): {offenders}. Pin the property by "
        "shape instead of naming the values."
    )


def test_the_rule_descriptions_carry_no_identifier():
    """A description is a category, never a value.

    Both gates print the description.  If the description carried the
    value, printing it would republish the identifier -- into a log
    that is public, indexed, and outlives the commit that fixed it.
    """
    for rule in (rules.RULE_MAILBOX, rules.RULE_HOME_PATH,
                 rules.RULE_PRIVILEGED_SWITCH, rules.RULE_SYSTEMD_UNIT,
                 rules.RULE_IDENTIFIER_LIST, rules.RULE_AGENT_ROSTER):
        text = rules.describe(rule)
        assert rules.EMAIL_RX.search(text) is None, text
        assert rules.HOME_PATH_RX.search(text) is None, text


def test_the_rules_actually_find_what_they_look_for():
    """A detector that finds nothing is not a detector.

    Every other test in this file asserts an ABSENCE, so a rule set
    that quietly stopped matching would make them all pass -- and the
    repository would look clean because nothing was being looked for.
    That is the failure mode this test exists to prevent: the rules
    are pinned against planted examples as well as against the tree.
    """
    # Assembled at run time, never written out: a literal personal
    # mailbox in this file would be a finding in the file that hunts
    # for them.
    planted = "\n".join((
        "someone@" + "not-a-placeholder.invalidtld",
        "/home/" + "a-real-account",
        "su" + "do -u " + "a-real-account",
        "a-real-account@" + "1004.service",
        "DEPLOY" + "_ACCOUNTS = (\"one\", \"two\")",
        "agent_name=\"" + "a_real_agent" + "\"",
    ))
    found = {rule for rule, _ in rules.find_offenders(planted)}
    assert rules.RULE_MAILBOX in found, found
    assert rules.RULE_HOME_PATH in found, found
    assert rules.RULE_PRIVILEGED_SWITCH in found, found
    assert rules.RULE_SYSTEMD_UNIT in found, found
    assert rules.RULE_IDENTIFIER_LIST in found, found
    assert rules.RULE_AGENT_ROSTER in found, found


def test_no_install_agent_roster_is_published():
    """Only the template's agent, or an obvious placeholder.

    An agent name is not a secret, but a roster IS an install's
    identity: one install's agent names tell a reader what that
    install is.  The template ships one agent, so a name in one of the
    places where the code means "an agent name" has to be that agent or
    an obvious placeholder.
    """
    offenders = _offenders(rules.RULE_AGENT_ROSTER)
    assert not offenders, (
        "the shipped source must not carry one install's agent roster: "
        f"{offenders}. The template ships "
        f"{sorted(rules.template_agent_names_in(ROOT))}; anything else in "
        "an agent-name position must be an obvious placeholder."
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
