A squash commit is authored by the pull request's author, and the pull
request is opened by the workflow's bot — so the agent's name is lost
the moment the change lands on `main`.

The commit MESSAGE carries it instead, and the squash message is built
from the branch's commit messages, so it survives the merge.

While proving it, the name extraction turned out to be leaving a
leading space on the name — and every test of it passed, because the
test helper stripped the output. The extraction is fixed, and the test
no longer strips the leading whitespace, so that bug is caught now.

Version 0.1.19.
