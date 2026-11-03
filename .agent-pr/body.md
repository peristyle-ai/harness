The guard that refuses `setuid` also refused `600`.

The pattern was

    \bchmod\s+[0-7]*[4-7][0-7][0-7]\b

and it was meant to catch the setuid bit. It did not, in either
direction.

**It refused the ordinary modes.** In a three-digit mode the leading
digit is the owner's read, write and execute bits -- it carries no
privilege at all. But the pattern matched any three-digit mode whose
leading digit fell in 4-7, so the everyday modes `644`, `600`, `700`,
`755`, `664`, `400` and `500` were all blocked and reported as
"setuid". Setting a config file to mode `600` is one of the most
ordinary things an agent does, and it was refused.

**And it let the privileged ones through.** `4000` and `2000` are setuid
and setgid with no other bits, and neither matched. Neither did the
short forms that set those bits on their own.

A guard that refuses the ordinary command is not a guard, it is an
outage: the agent gets an error that reads like a security decision,
and the natural response is to find a way around the guard rather than
to report it.

## What changes

The privilege bits can only be expressed in the **four-digit** form,
where the leading digit is the special-bits octal digit -- `4` setuid,
`2` setgid, `1` sticky. So:

- a four-digit mode whose leading digit carries setuid or setgid is
  refused -- `2`, `3`, `4`, `5`, `6` or `7`, which is every leading
  digit except `0` (no special bits at all) and `1` (sticky alone);
- the short forms that set those bits on their own -- `2`, `4`, `6` --
  are refused;
- every three-digit mode is allowed, including the leading-zero forms;
- the sticky bit is **not** matched.

On sticky: it takes rights *away* rather than granting them -- it is
what stops one user deleting another user's file in a shared directory
-- and the symbolic rule immediately above already allows the sticky
form for the same reason. If the project would rather refuse sticky as
well, that is one character in the pattern and one line in the test.

The symbolic rule (`u+s`), the `777` rule and the recursive-`chmod`
soft-flag are untouched.

## Tests

`tests/test_chmod_mode_scope.py` -- 33 cases:

- the privileged modes stay refused (the four-digit setuid, setgid and
  both-together forms, and the short forms that set those bits alone);
- the everyday modes are allowed, including `0600`, `0700` and `0755`;
- the sticky forms are allowed;
- the symbolic `u+s` form stays refused.

The file fails 21 of its 33 cases against the old pattern and passes
all 33 against the new one, so it can actually fail.

The rest of the suite is unchanged: the same three tests fail before
and after this change (430 passed / 3 failed / 16 skipped before, 463
passed / 3 failed / 16 skipped after).

## For the reviewer

The change is generic and names no install.
