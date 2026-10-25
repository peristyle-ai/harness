# app/static/entity-icons/

**This directory holds runtime content and is not tracked in git (except
this README).**

An agent's icon (avatar) is uploaded through the API:

    PUT /api/v1/entities/<name>/icon      (multipart, field "file")

The server writes it here as `<name>-<random-hex>.<ext>` and records the
filename, mime type and size in `public.entity_icons`. The row in that
table is the source of truth for *which* file belongs to *which* agent —
the filename on disk is meaningless on its own.

    GET /api/v1/entities/<name>/icon      (public — the <img> tag needs no credentials)

If the metadata row exists but the file does not, the GET endpoint logs
`entity_icon_get: file missing for <name>` and returns 404. The chat UI
falls back to a generated placeholder avatar.

Why it is not tracked:

- it is **user content**, uploaded at runtime — the same category as
  `uploads/`;
- it is **per-install**: the filename carries the agent name of whoever
  runs that install, which is a fingerprint, not source;
- it is **not reproducible**: a fresh clone cannot know which avatars an
  operator wants.

The default PWA icons (`icon.svg`, `icon-180.png`, `icon-512.png`) live one
directory up and *are* tracked — those are the app's own icons.
