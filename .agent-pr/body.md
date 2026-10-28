Both edits are one concern: the agent-facing text that ships to every
install must not hard-code one install's Unix account.

1. app/agent/tools/exec_bash.py - the tool description said "as the
   peristyle user". That string ships to every install, so on any other
   install it told the model a lie about the account it runs as. It is
   what led an agent to conclude that no peristyle account existed on a
   box that has one. The description now derives the name at runtime from
   the process's real uid, so it names the account this install actually
   runs as. The module docstring and the cwd comment were reworded the
   same way.

2. app/agent/tools/_safety.py - the env-var blocklist comment said "the
   peristyle runtime inherits a bunch of env vars". Same mistake, same
   fix: it now says "the harness service".

WHAT DOES NOT CHANGE: the safety classifier. The hard-block list in
app/agent/tools/_safety.py (privilege escalation, destructive filesystem
commands, remote-code pipes, the protected system files, setuid bits) and
the confirm list are untouched. Sixteen new tests pin them, so a future
edit cannot quietly loosen them.

TESTS: tests/test_exec_bash_description.py (3 new behaviours, 17 pins)
and tests/test_safety_comment.py (2). Every new behaviour test was seen
failing on the old code before the fix was applied.

This supersedes #12 and #13: one concern, one pull request, one release
(0.1.6 -> 0.1.7).
