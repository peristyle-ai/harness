"""The shipped default bind address must be loopback, never a wildcard.

WHY THIS TEST EXISTS
--------------------
The harness is a web application with an operator's data behind it.  It
is meant to be reached through a reverse proxy, and the proxy is what
decides who may reach it: TLS termination in front, and the browser
checks and the secure cookie behind it.

A service that listens on ``0.0.0.0`` is reachable on every interface
the host has, INCLUDING the ones the proxy is there to protect.  On a
host with a LAN address, ``0.0.0.0:5010`` is a plain-HTTP admin
surface that any host on the LAN can talk to, and the proxy never sees
the request at all.

That is not a hypothetical here.  The bind address used to fall back
to a wildcard, so an install whose own configuration said
``127.0.0.1:5010`` still listened on every interface -- the fallback
silently won.  The configuration was right and the service was wrong.

WHAT IS PINNED
--------------
* **the fallback** -- the address ``run.sh`` uses when the configuration
  cannot be read must be a loopback address;
* **the shipped configuration** -- ``config.yaml.example`` must bind
  loopback, because that file is what a new install starts from;
* **the installer** -- ``install.sh`` must default to loopback for the
  same reason;
* **the whole shipped tree** -- a wildcard address may not appear on a
  line that configures a bind address.  Prose may discuss it -- this
  file's own docstring does -- but no line of code may use one.

A WILDCARD IS STILL POSSIBLE
----------------------------
An operator who genuinely wants the harness on every interface writes
it into ``config.yaml`` or exports ``HARNESS_BIND``.  That is a
decision, and it leaves a trace.  What this test forbids is the
accident: a wildcard that arrives because a default was wrong.

A RULE THAT HAS NEVER BEEN SEEN FAILING IS NOT A RULE
------------------------------------------------------
``test_this_rule_catches_the_wildcard`` below runs the wildcard rule
over the wildcard it forbids, and asserts that it fails.  A rule set
that quietly stopped matching would make every other assertion in this
file pass.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The trees and documents that ship to a user of the harness.
SOURCE_TREES = ("app", "scripts", "schema", "tools", "tests")
DOC_FILES = ("README.md", "SECURITY.md", "CONTRIBUTING.md")

# Files whose content is a configuration value rather than code.
CONFIG_FILES = ("run.sh", "install.sh", "config.yaml.example")

# This file is a guard and necessarily contains the shapes it looks for.
GUARD_FILES = ("test_bind_default.py",)

TEXT_SUFFIXES = (".py", ".js", ".css", ".html", ".sh", ".yml", ".yaml", ".sql", ".md", ".txt")

# A wildcard address.  Matched as a whole token so that a version number
# or a decimal cannot be mistaken for one.
WILDCARD_RX = re.compile(r"(?<![\w.])0\.0\.0\.0(?![\w.])")

# A loopback address: 127.0.0.0/8, and the IPv6 loopback.
LOOPBACK_RX = re.compile(r"^(?:127\.(?:\d{1,3}\.){2}\d{1,3}|\[?::1\]?)(?::\d+)?$")

# Words that mean "this line is configuring where the service listens".
BIND_CONTEXT_RX = re.compile(r"(?i)\b(?:bind|listen|host|addr|address)\b|--bind")


def _is_comment(line: str, suffix: str) -> bool:
    """True when the line is a comment in the language it is written in."""
    stripped = line.strip()
    if suffix in (".py", ".sh", ".yaml", ".yml", ".toml", ".cfg", ".conf", ".service", ".example"):
        return stripped.startswith("#")
    if suffix in (".md", ".txt"):
        # A markdown heading is not a comment; a markdown paragraph may
        # legitimately discuss a wildcard in prose.
        return False
    return False


def _shipped_files() -> list[pathlib.Path]:
    """Every file that ships, read from disk.

    Read from the filesystem, not from ``git ls-files``: a git call
    that returns nothing (a repository owned by another user, a
    detached worktree) would make every rule in this file pass
    vacuously.  A rule that scans nothing is not a rule.
    """
    files: list[pathlib.Path] = []
    for tree in SOURCE_TREES:
        base = ROOT / tree
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
                continue
            if path.name in GUARD_FILES:
                continue
            files.append(path)
    for name in DOC_FILES:
        path = ROOT / name
        if path.is_file():
            files.append(path)
    for name in CONFIG_FILES:
        path = ROOT / name
        if path.is_file():
            files.append(path)
    if not files:
        raise AssertionError("no shipped files found -- the rules would pass vacuously")
    return files


def _read(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _config_bind(path: pathlib.Path) -> str | None:
    """The ``app.bind`` value of a YAML configuration file."""
    in_app = False
    for raw in _read(path).splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            in_app = line.strip().rstrip(":") == "app"
            continue
        if in_app and ":" in line:
            key, _, value = line.strip().partition(":")
            if key.strip() == "bind":
                return value.strip().strip("\"'")
    return None


def _shell_default(path: pathlib.Path, pattern: str) -> str | None:
    """The default value of a shell parameter expansion, e.g. ``${B:-x}``."""
    match = re.search(pattern, _read(path))
    return match.group(1) if match else None


# --- the rules ------------------------------------------------------------


def test_run_sh_fallback_is_loopback() -> None:
    """run.sh must fall back to loopback when the configuration is unreadable."""
    fallback = _shell_default(
        ROOT / "run.sh",
        r'\[\s*-n\s*"\$BIND"\s*\]\s*\|\|\s*BIND="([^"]+)"',
    )
    assert fallback is not None, "run.sh no longer has a bind fallback -- read it and fix this test"
    assert LOOPBACK_RX.match(fallback), (
        f"run.sh falls back to {fallback!r}; a wildcard bind is a decision, "
        "not a default"
    )


def test_shipped_configuration_binds_loopback() -> None:
    """config.yaml.example is what a new install starts from."""
    bind = _config_bind(ROOT / "config.yaml.example")
    assert bind is not None, "config.yaml.example has no app.bind"
    assert LOOPBACK_RX.match(bind), f"config.yaml.example binds {bind!r}, not loopback"


def test_installer_defaults_to_loopback() -> None:
    """install.sh must not hand a new install a wildcard bind."""
    default = _shell_default(ROOT / "install.sh", r'BIND_ADDR="\$\{BIND:-([^}]+)\}"')
    assert default is not None, "install.sh no longer has a bind default -- read it and fix this test"
    assert LOOPBACK_RX.match(default), f"install.sh defaults to {default!r}, not loopback"


def test_no_shipped_file_configures_a_wildcard_bind() -> None:
    """No line of shipped code may configure a wildcard bind address."""
    offenders = []
    for path in _shipped_files():
        suffix = path.suffix
        for number, line in enumerate(_read(path).splitlines(), start=1):
            if not WILDCARD_RX.search(line):
                continue
            if _is_comment(line, suffix):
                continue
            if suffix in (".md", ".txt"):
                # Prose may discuss a wildcard; only a line that also
                # configures a bind address is an offence.
                if not BIND_CONTEXT_RX.search(line):
                    continue
            offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not offenders, (
        "a wildcard bind address is configured in the shipped tree:\n  "
        + "\n  ".join(offenders)
    )


def test_this_rule_catches_the_wildcard() -> None:
    """The rule above must fail on a wildcard bind -- a rule that cannot fail is not a rule."""
    offender = 'BIND="0.0.0.0:5010"'
    caught = bool(WILDCARD_RX.search(offender)) and not _is_comment(offender, ".sh")
    assert caught, "the wildcard rule no longer matches the wildcard it forbids"


def test_loopback_pattern_accepts_the_loopbacks_and_nothing_else() -> None:
    """The loopback pattern must recognise loopback and reject a wildcard."""
    for good in ("127.0.0.1:5010", "127.0.0.53", "[::1]:5010", "127.0.0.1"):
        assert LOOPBACK_RX.match(good), f"{good!r} is loopback and must be accepted"
    for bad in ("0.0.0.0:5010", "192.0.2.41:5010", "example.com:5010", ""):
        assert not LOOPBACK_RX.match(bad), f"{bad!r} is not loopback and must be rejected"
