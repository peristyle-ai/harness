"""Security hardening for harness.

- Security headers (X-Content-Type-Options, X-Frame-Options, HSTS,
  Referrer-Policy, CSP) applied to every response.
- Log redaction: any `LLM_API_KEY= ***` or `Authorization: Bearer ***`
  pattern in a log record is masked before emission.
- Per-request `X-Request-Id` for log correlation.
- Sanity check at startup: refuse to start if config.yaml is readable by
  others, is missing a required value, or still holds a placeholder where
  a real secret belongs.
- Rate limiting via Flask-Limiter (in-memory, process-wide).
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from uuid import uuid4

from flask import Flask, current_app, g, request

from .config import AppConfig

# Regex for redaction. Order matters: bearer first (more specific).
_REDACT_BEARER = re.compile(r"(Authorization:\s*Bearer\s+)([A-Za-z0-9_\-\.]+)", re.IGNORECASE)
_REDACT_KEY = re.compile(r"(LLM_API_KEY\s*=\s*)([^\s\"']+)", re.IGNORECASE)
_MASK = "***REDACTED***"

# The values that mean "the operator has not filled this in yet". A
# config that still holds one of them is not a working config, and the
# app says so at startup rather than failing at the first API call.
PLACEHOLDERS = (
    "YOUR_API_KEY_HERE",
    "YOUR_SECRET_KEY_HERE",
    "YOUR_PASSWORD_HERE",
    "YOUR_TOKEN_HERE",
    "YOUR_INTERNAL_TOKEN_HERE",
    "REPLACE_ME",
    "replace_me",
    "changeme",
    "sk-...",
)

# The config values that MUST be present and non-empty. A missing secret
# is a startup failure, not a runtime surprise.
REQUIRED_CONFIG_VALUES = (
    ("app", "secret_key"),
    ("db", "url"),
    ("llm", "api_key"),
)


class KeyRedactionFilter(logging.Filter):
    """Mask any `LLM_API_KEY= ***` line or `Authorization: Bearer ***` that
    slips into a log record (msg + args). One filter instance is shared
    across all loggers."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _REDACT_BEARER.sub(rf"\1{_MASK}", record.msg)
            record.msg = _REDACT_KEY.sub(rf"\1{_MASK}", record.msg)
        if record.args:
            new_args = []
            for a in record.args:
                if isinstance(a, str):
                    a = _REDACT_BEARER.sub(rf"\1{_MASK}", a)
                    a = _REDACT_KEY.sub(rf"\1{_MASK}", a)
                new_args.append(a)
            record.args = tuple(new_args)
        return True


_REDACTION_FILTER: KeyRedactionFilter | None = None


def install_log_redaction() -> None:
    """Install the redaction filter on the root logger (and key child loggers)."""
    global _REDACTION_FILTER
    if _REDACTION_FILTER is None:
        _REDACTION_FILTER = KeyRedactionFilter()
    root = logging.getLogger()
    if _REDACTION_FILTER not in root.filters:
        root.addFilter(_REDACTION_FILTER)
    for name in ("gunicorn.error", "werkzeug"):
        lg = logging.getLogger(name)
        if _REDACTION_FILTER not in lg.filters:
            lg.addFilter(_REDACTION_FILTER)


def apply_security_headers(app: Flask, config: AppConfig) -> None:
    h = config.security.headers

    @app.after_request
    def _headers(response):
        response.headers["X-Content-Type-Options"] = h.x_content_type_options
        response.headers["X-Frame-Options"] = h.x_frame_options
        response.headers["Strict-Transport-Security"] = h.strict_transport_security
        response.headers["Referrer-Policy"] = h.referrer_policy
        response.headers["Content-Security-Policy"] = h.csp
        return response


def install_request_id(app: Flask) -> None:
    """Add a per-request UUID for log correlation."""

    @app.before_request
    def _start():
        g.request_id = request.headers.get("X-Request-Id") or uuid4().hex[:12]

    @app.after_request
    def _end(response):
        rid = getattr(g, "request_id", None)
        if rid:
            response.headers["X-Request-Id"] = rid
            current_app.logger.info(
                "request_id=%s method=%s path=%s status=%s",
                rid, request.method, request.path, response.status_code,
            )
        return response


def sanity_check_config(config_path: str | os.PathLike[str] = "config.yaml") -> None:
    """Refuse to start on a config that is not a working config.

    Three ways to fail, all of them "the operator has not finished":

      1. the file is group- or world-readable -- it holds the API key and
         the database password, so it must be mode 0600;
      2. a required value is missing or empty;
      3. a required value still holds a placeholder.

    Idempotent: safe to call more than once. Raises RuntimeError, which
    stops the service before it serves a single request.
    """
    p = Path(config_path)
    if not p.exists():
        raise RuntimeError(
            f"security: {p} not found. Copy config.yaml.example to "
            "config.yaml and fill in the values."
        )
    mode = p.stat().st_mode & 0o777
    if mode & 0o044:
        raise RuntimeError(
            f"security: {p} is readable by others (mode={oct(mode)}). "
            f"It holds the API key and the database password. "
            f"Run: chmod 600 {p}"
        )

    import yaml

    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except Exception as e:
        raise RuntimeError(f"security: {p} is not valid YAML: {e}") from e

    for section, key in REQUIRED_CONFIG_VALUES:
        val = (raw.get(section) or {}).get(key)
        if val is None or not str(val).strip():
            raise RuntimeError(
                f"security: {section}.{key} is empty in {p}. "
                f"Edit {p} and set the real value."
            )
        val = str(val).strip()
        if val in PLACEHOLDERS or val.startswith("YOUR_"):
            raise RuntimeError(
                f"security: {section}.{key} still holds a placeholder "
                f"({val[:24]}). Edit {p} and set the real value."
            )


def install_rate_limiter(app: Flask, config: AppConfig) -> None:
    """Apply Flask-Limiter with the config's rate_limit values. In-memory
    storage; process-wide. For multi-worker, switch to Redis.
    """
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    lim = Limiter(
        get_remote_address,
        app=app,
        default_limits=[config.security.rate_limit.default],
        storage_uri="memory://",
        strategy="fixed-window",
    )
    app.extensions["limiter"] = lim
