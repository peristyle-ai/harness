The harness is reached through a reverse proxy, and the proxy is what
decides who may reach it: TLS termination in front, and the browser
checks and the secure cookie behind it. A service that listens on a
wildcard address is reachable on every interface the host has,
including the ones the proxy is there to protect.

That is not a hypothetical here. The bind address used to fall back to
a wildcard, so an install whose own configuration said a loopback
address still listened on every interface -- the fallback silently won.
The configuration was right and the service was wrong.

WHAT CHANGES

- `run.sh` falls back to a loopback address. A wildcard is still
  possible, and still a decision: an operator who wants it writes it
  into `config.yaml` or exports `HARNESS_BIND`. What is gone is the
  accident.
- `tests/test_bind_default.py` (new) pins the property, so the default
  cannot go back to being wrong by hand. It checks the fallback in
  `run.sh`, the shipped `config.yaml.example`, the default in
  `install.sh`, and the whole shipped tree for a line that configures a
  wildcard bind. Prose may still discuss a wildcard -- this file's own
  docstring does -- but no line of code may use one.

THE RULE HAS BEEN SEEN FAILING

The test file includes a case that runs the wildcard rule over the
wildcard it forbids and asserts that it fails, and the change was
checked against the previous `run.sh`: two of the tests fail there and
pass here. A rule set that quietly stopped matching would make every
other assertion in the file pass, and that is the failure mode worth
guarding against.

The full suite passes with this change.
