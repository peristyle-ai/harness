The gates are good at the shapes they know: a mailbox, a path into a
home directory, a switch to a named account, an agent name in an
agent-name position. This change is about the three things that were in
the tree and that neither gate could express.

WHAT WAS IN THE TREE

- A tenant's name, in a comment in production code. The comment was
  explaining that the install's database can be called whatever the
  operator called it, and it used one tenant's name as the example.
- A tenant's name, in the identity template -- the tracked file every
  install starts from, and therefore read by every reader of a public
  repository.
- A private LAN address, used as an example of an address that is not
  loopback, in a test.

WHY THE GATES DID NOT CATCH THEM

- The first two are NAMES, not shapes. A rule that banned a name would
  have to contain the name, and a list of names is itself the leak, so
  the rule set cannot cover this class. It is caught by review, which is
  what review is for.
- The third is a private address in a test fixture. A rule for "a
  private address" would match every address in every test, and it would
  have to know the range to exclude. The example now uses the
  documentation range, which is the correct thing for a public
  repository to use as an example anyway.

THE SCOPE FIX

The working-tree guard listed the trees it reads, and it did not list
the identity template. The branch gate reads a whole tree, so it did
scan it. The two gates therefore had different scopes, and a rule that
only one of them applies is a rule that is not enforced -- which is
exactly how the two drifted apart in the first place.

The working-tree guard now reads the identity template as well. It is
tracked in this repository and it ships to every install, so it was
always in scope; it just was not in the list.

WHAT ELSE CHANGED

The scope note in the shared rules module said that the identity
template is "not in this repository". It is in this repository. The
note now says what is true.

The full suite passes, and the secret scan is clean.
