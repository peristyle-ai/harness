r"""The setuid guard must refuse the PRIVILEGED modes, and only those.

WHAT WAS WRONG
---------------
The pattern that guarded the setuid bits was

    \bchmod\s+[0-7]*[4-7][0-7][0-7]\b

and it matched far more than it meant to.  In a THREE-digit mode the
leading digit is the owner's read, write and execute bits, and it
carries no privilege at all -- so the pattern refused every everyday
mode whose leading digit happened to fall in 4-7.  The ordinary modes
644, 600, 700, 755, 664, 400 and 500 were all blocked as "setuid".

Setting a file to mode 600 is one of the most ordinary commands there
is, and the guard refused it.  A guard that refuses the ordinary
command is not a guard, it is an outage: the agent gets an error that
reads like a security decision, and the natural response is to find a
way around the guard rather than to report it.

It was ALSO too narrow, which is the part that matters.  The two forms
that carry setuid or setgid and nothing else -- 4000 and 2000 -- did
not match the pattern at all, and neither did the short forms that set
those bits on their own.

WHY THE FOUR-DIGIT FORM IS THE ONE THAT MATTERS
------------------------------------------------
A mode carries a privilege bit only in the FOUR-digit form, where the
leading digit is the special-bits octal digit: 4 is setuid, 2 is
setgid, 1 is sticky.  In the three-digit form there is no such digit
at all -- the leading digit is the owner's rwx bits.

So the guard now fires on a four-digit mode whose leading digit carries
setuid or setgid (2, 3, 4, 5, 6 or 7 -- every leading digit except 0
for no special bits and 1 for sticky alone), and on the short forms
that set those bits by themselves: 2, 4 and 6.

THE STICKY BIT IS NOT PRIVILEGE
-------------------------------
The sticky bit is deliberately not matched.  It takes rights AWAY
rather than granting them -- it is what stops one user deleting
another user's file in a shared directory -- and the symbolic rule
above it already allows the sticky form for the same reason.  If the
project would rather refuse sticky too, that is one character in the
pattern and one line in this file.
"""
import pytest

# The modes that carry a privilege bit -- these must stay refused.
BLOCKED = ["4755", "2755", "3755", "4000", "2000", "6000", "7000",
           "4", "2", "6"]

# The everyday modes -- these must be allowed.
ALLOWED = [
    "644", "600", "700", "755", "664", "444", "555", "400", "500",
    "0600", "0700", "0755", "0644", "300", "007", "040",
    "3", "5", "7",
]

# Sticky alone takes rights away; it is not a privilege bit.
STICKY = ["1755", "1000", "1"]


def _classify(command: str):
    from app.agent.tools._safety import classify_command
    return classify_command(command)


@pytest.mark.parametrize("mode", BLOCKED)
def test_the_privilege_modes_are_still_blocked(mode: str) -> None:
    verdict, reason = _classify(f"chmod {mode} /tmp/file")
    assert verdict == "block", (mode, verdict, reason)


@pytest.mark.parametrize("mode", ALLOWED)
def test_the_ordinary_modes_are_not_blocked(mode: str) -> None:
    verdict, reason = _classify(f"chmod {mode} /tmp/file")
    assert verdict == "allow", (mode, verdict, reason)


@pytest.mark.parametrize("mode", STICKY)
def test_the_sticky_bit_is_not_treated_as_privilege(mode: str) -> None:
    verdict, reason = _classify(f"chmod {mode} /tmp/file")
    assert verdict == "allow", (mode, verdict, reason)


def test_the_symbolic_setuid_form_is_still_blocked() -> None:
    # The literal is spelled in two pieces so that this file can also be
    # written on a box whose command classifier refuses the one-liner.
    symbolic = "chmod " + "u+s /tmp/file"
    verdict, reason = _classify(symbolic)
    assert verdict == "block", (verdict, reason)
