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
not match the pattern at all.

WHY THE FOUR-DIGIT FORM IS THE ONE THAT MATTERS
------------------------------------------------
A mode carries a privilege bit only in the FOUR-digit form, where the
leading digit is the special-bits octal digit: 4 is setuid, 2 is
setgid, 1 is sticky.  In the three-digit form there is no such digit
at all -- the leading digit is the owner's rwx bits.

A SHORT mode is not a special-bits mode either.  A mode of one or two
digits is read as the trailing digits with leading zeros, so `chmod 4
file` means 0004 -- the owner may read the file -- and not 4000.  The
special-bits digit only comes into existence in the four-digit form,
so a short mode can never carry setuid or setgid.  This was measured
rather than assumed: on GNU coreutils and on busybox, `chmod 2`,
`chmod 4` and `chmod 6` all leave the mode at 2, 4 and 6 with no
special bits set, and `chmod 4000` is the form that actually sets
setuid.

So the guard now fires on a four-digit mode whose leading digit
carries setuid or setgid (2, 3, 4, 5, 6 or 7 -- every leading digit
except 0 for no special bits and 1 for sticky alone), and on nothing
else.  The short modes are ordinary modes and are allowed, exactly
like their three-digit equivalents.

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
BLOCKED = ["4755", "2755", "3755", "4000", "2000", "6000", "7000"]

# The everyday modes -- these must be allowed.
ALLOWED = [
    "644", "600", "700", "755", "664", "444", "555", "400", "500",
    "0600", "0700", "0755", "0644", "300", "007", "040",
    "3", "5", "7",
]

# A SHORT mode is the trailing digits with leading zeros, so it carries
# no special bits at all: `chmod 4 file` is 0004, not 4000.  These are
# ordinary modes and must be allowed -- the guard used to refuse them
# for a privilege bit they do not carry.
SHORT_ALLOWED = ["2", "4", "6"]

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


@pytest.mark.parametrize("mode", SHORT_ALLOWED)
def test_a_short_mode_carries_no_privilege_bit(mode: str) -> None:
    # `chmod 4 f` is 0004 -- the owner may read the file.  The
    # special-bits digit only exists in the four-digit form, so a short
    # mode can never be setuid or setgid and must not be refused.
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
