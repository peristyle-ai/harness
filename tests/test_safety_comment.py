"""The shipped source must not name one install's Unix account.

The env-var blocklist in ``app/agent/tools/_safety.py`` applies to every
install -- it strips secret-looking variables from the environment the
model's shell inherits.  Its comment, however, described "the peristyle
runtime", which reads as if the blocklist were specific to one install.
The same mistake in the exec_bash tool description once made a reader
believe the harness ran as an account that was not there.

The behaviour is right and unchanged; only the wording is fixed.  These
tests pin the wording so it cannot drift back.
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

# Unix accounts that exist on the boxes this repository is deployed to.
# None of them may appear in a comment that describes install-wide
# behaviour.
INSTALL_ACCOUNTS = ("peristyle", "mal_external")


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
    for account in INSTALL_ACCOUNTS:
        assert not re.search(rf"\b{re.escape(account)}\b", comment, re.I), (
            "the env-var blocklist applies to every install, so its comment "
            f"must not name one install's Unix account ({account!r})"
        )


def test_env_blocklist_comment_still_explains_the_stripping():
    comment = _comment_above("EXEC_BASH_SAFE_ENV_KEYS")
    assert "printenv" in comment, (
        "the comment must keep naming the threat it removes: the model "
        "could printenv the secrets the service inherited"
    )
    assert re.search(r"\benv\b", comment, re.I)
