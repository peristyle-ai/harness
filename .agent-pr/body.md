The tool LLM-facing description said the shell command runs as the peristyle user. That string ships to every install, but each install runs as its own Unix account: this one runs as mal_external, the production one as peristyle. On this install the description was simply false, and an agent that believed it drew a wrong conclusion about the machine - it concluded the production account did not exist and that a sibling install was a leftover directory.

The fix derives the account name at runtime from the process real uid (pwd.getpwuid(os.getuid())) and interpolates it into the description, so every install tells the model the truth about who it is running as. The module docstring and the inline comment are corrected the same way.

What did NOT change: the tool safety classifier. The hard-block list (privilege escalation, destructive filesystem commands, remote-code pipes, the protected system files, setuid bits) and the soft-flag list are untouched - this PR changes the DESCRIPTION only. The new test file pins all sixteen hard-blocked commands so a future edit cannot quietly loosen them.

Tests: tests/test_exec_bash_description.py - 20 tests. The three that describe the new behaviour were watched failing on the old code first; the rest pin the hard blocks and the allow path. Full suite: 201 passed, 9 skipped. Secret scan clean.
