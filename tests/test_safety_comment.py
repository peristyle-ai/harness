"""The shipped source must not name one install's Unix account.

The env-var blocklist in ``app/agent/tools/_safety.py`` applies to every
install -- it strips secret-looking variables from the environment the
model's shell inherits.  Its comment, however, described "the peristyle
runtime", which reads as if the blocklist were specific to one install.
The same mistake in the exec_bash tool description once made a reader
believe the harness ran as an account that was not there.

The behaviour is right and unchanged; only the wording is fixed.  These
tests pin the wording so it cannot drift back.

WHY THERE IS NO LIST OF ACCOUNT NAMES HERE
------------------------------------------
The obvious way to write the first test is to list the Unix accounts
that exist on the boxes this repository is deployed to and assert that
none of them appears in the comment.  That list IS the leak: an account
name identifies an install, and a public repository must not carry the
account names of the machines it runs on.

So the invariant is pinned by SHAPE instead:

* the vendor's name must not appear in a comment that describes
  install-wide behaviour -- that is the wording that actually leaked;
* no account-shaped token may appear either.  A Unix account on these
  boxes is spelled with an underscore or a digit (``some_account``,
  ``someaccount2``), and that shape is what the check below looks for.

The first rule is exact and catches the historical leak.  The second is
a shape rule, so it also catches an account nobody wrote down.
"""

import pathlib
import re

SAFETY = (
    pathlib.Path(__file__).resolve().parents[1]
    / "app"
    / "agent"
    / "tools"
    / "_safety.py"
)

# The vendor's name.  It is not a secret -- it is in the repository's
# own name and in the product's -- but it must not appear in a comment
# that describes behaviour which applies to EVERY install, because that
# is what makes the comment read as if it were about one of them.
VENDOR = "peristyle"

# The shape of a Unix account name: a lowercase token with an underscore
# or a digit in it.  Words in prose do not look like this; accounts do.
ACCOUNT_SHAPE = re.compile(r"(?<![\w/])[a-z][a-z0-9]*[_\d][a-z0-9_]*(?![\w])")

# The trees an account name would NOT be in.  If a candidate appears
# anywhere else in the shipped source it is a product identifier -- a
# tool name, a role name, a config key -- not an account.
SOURCE_TREES = ("app", "scripts", "schema")


def _appears_elsewhere(token: str) -> bool:
    """Is *token* used anywhere else in the shipped source?

    The file under test does NOT count as "elsewhere": the account name
    is IN it -- that is the whole point of the check.
    """
    pattern = re.compile(rf"(?<![\w]){re.escape(token)}(?![\w])")
    this_file = pathlib.Path(__file__).resolve()
    for tree in SOURCE_TREES:
        for path in (this_file.parents[1] / tree).rglob("*.py"):
            if path.resolve() in (this_file, SAFETY.resolve()):
                continue
            try:
                if pattern.search(path.read_text()):
                    return True
            except (OSError, UnicodeDecodeError):
                continue
    return False


def _comment_above(marker: str) -> str:
    """Return the contiguous comment block immediately above *marker*."""
    head = SAFETY.read_text().split(marker)[0]
    block = []
    for line in reversed(head.rstrip().splitlines()):
        if line.lstrip().startswith("#"):
            block.append(line)
        else:
            break
    return "\n".join(reversed(block))


def test_env_blocklist_comment_names_no_install_account():
    comment = _comment_above("EXEC_BASH_SAFE_ENV_KEYS")
    assert comment, "the env-var blocklist must stay documented"
    assert not re.search(rf"\b{re.escape(VENDOR)}\b", comment, re.I), (
        "the env-var blocklist applies to every install, so its comment "
        f"must not name the vendor ({VENDOR!r})"
    )


def test_env_blocklist_comment_names_no_account_shaped_token():
    comment = _comment_above("EXEC_BASH_SAFE_ENV_KEYS")
    offenders = sorted(
        token for token in set(ACCOUNT_SHAPE.findall(comment))
        if not _appears_elsewhere(token)
    )
    assert not offenders, (
        "the env-var blocklist comment must not contain an account-shaped "
        f"token: {offenders}. A Unix account identifies the machine it is "
        "on, and a public repository must not name the accounts of the "
        "machines it runs on."
    )


def test_env_blocklist_comment_still_explains_the_stripping():
    comment = _comment_above("EXEC_BASH_SAFE_ENV_KEYS")
    assert "printenv" in comment, (
        "the comment must keep naming the threat it removes: the model "
        "could printenv the secrets the service inherited"
    )
    assert re.search(r"\benv\b", comment, re.I)
