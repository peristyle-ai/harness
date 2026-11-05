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
and setgid with no other bits, and neither matched.

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
- every three-digit mode is allowed, including the leading-zero forms;
- the sticky bit is **not** matched.

On sticky: it takes rights *away* rather than granting them -- it is
what stops one user deleting another user's file in a shared directory
-- and the symbolic rule immediately above already allows the sticky
form for the same reason. If the project would rather refuse sticky
as well, that is one character in the pattern and one line in the test.

The symbolic rule (`u+s`), the `777` rule and the recursive-`chmod`
soft-flag are untouched.

## A short mode is not a special-bits mode

An earlier draft of this change also refused the **short** forms `2`,
`4` and `6`, on the theory that they set setuid or setgid on their
own. They do not, and that rule was removed.

A mode of one or two digits is read as the *trailing* digits with
leading zeros, so `chmod 4 file` means `0004` -- the owner may read
the file -- and not `4000`. The special-bits digit only comes into
existence in the four-digit form. This was measured, not assumed:

| command | resulting mode | setuid/setgid? |
|---|---|---|
| `chmod 2 f` | `2` | no |
| `chmod 4 f` | `4` | no |
| `chmod 6 f` | `6` | no |
| `chmod 2000 f` | `2000` | setgid |
| `chmod 4000 f` | `4000` | setuid |

Measured on GNU coreutils and, independently, on busybox. The same
table is why the old pattern's refusal of the short forms was not
noticed for so long: it never refused them, and the new rule would
have started to -- for no security gain, and at the cost of refusing
three ordinary modes.

## Tests

`tests/test_chmod_mode_scope.py` -- 33 cases:

- the privileged modes stay refused (the four-digit setuid, setgid and
  both-together forms, including `4000` and `2000` with no other bits);
- the everyday modes are allowed, including `0600`, `0700` and `0755`;
- the short modes `2`, `4` and `6` are allowed, because a short mode
  carries no privilege bit;
- the sticky forms are allowed;
- the symbolic `u+s` form stays refused.

The file fails 18 of its cases against the old pattern and passes all
of them against the new one, so it can actually fail.

The rest of the suite is unchanged: the same tests pass before and
after this change (433 passed / 16 skipped before, 466 passed / 16
skipped after).

## For the reviewer

The change is generic and names no install.

One note for whoever looks at this next: the guard matches a command
*string*, so it is a classifier and not a sandbox. `chmod --reference`
and a `cp` that preserves modes can still carry a setuid bit onto a
file without the four-digit form ever appearing in the command. That
is a separate concern from this fix and is not addressed here.
