"""Where the harness is installed.

The install paths are CONFIGURATION, not constants: an operator installs
the harness wherever they like. They are read from the environment, with
defaults derived from where this file actually is -- so the code carries
no absolute path of anyone's.

  HARNESS_ROOT        the install root (the repo). Default: the parent of
                      the app package.
  HARNESS_HOME        the operator's home -- the exec_bash working
                      directory and the attach_file allowlist.
                      Default: HARNESS_ROOT's parent.
  HARNESS_LOG_DIR     the audit logs. Default: HARNESS_ROOT/logs.
  HARNESS_UPLOAD_DIR  the uploads. Default: HARNESS_ROOT/uploads.
  HARNESS_ENV_FILE    the .env to read. Default: HARNESS_ROOT/.env.
"""
import os
from pathlib import Path

# the repo root: app/paths.py -> app/ -> the repo
REPO_ROOT = Path(os.environ.get(
    "HARNESS_ROOT", Path(__file__).resolve().parent.parent)).resolve()

HOME_DIR = Path(os.environ.get("HARNESS_HOME", REPO_ROOT.parent)).resolve()

LOG_DIR = Path(os.environ.get("HARNESS_LOG_DIR", REPO_ROOT / "logs")).resolve()

UPLOAD_DIR = Path(os.environ.get(
    "HARNESS_UPLOAD_DIR", REPO_ROOT / "uploads")).resolve()

ENV_FILE = Path(os.environ.get(
    "HARNESS_ENV_FILE", REPO_ROOT / ".env")).resolve()

# the systemd unit directory the exec_bash tool may touch (the safety
# allowlist names the units it is allowed to manage)
SYSTEMD_DIR = Path(os.environ.get(
    "HARNESS_SYSTEMD_DIR", HOME_DIR / "systemd")).resolve()
