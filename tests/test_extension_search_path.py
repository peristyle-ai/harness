"""The extension schemas must be on the connection's search_path.

Regression test for the a customer host re-create (2026-10-25).

The harness's SQL refers to pgvector's types UNQUALIFIED -- `%s::halfvec`
-- because the schema the "vector" extension lives in is not known until
the database is opened. On the machine the harness was written on,
pgvector is in `public`, so the default search_path happened to work.

On a customer host the mail server installed pgvector FIRST, so it lives in
`polymed_mail`, and the default search_path does not contain it. Every
memory write then failed with:

    RuntimeError: UndefinedObject: type "halfvec" does not exist

The fix resolves the extension schemas from the live database and puts
them on the pooled connections' search_path.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from app.extensions import _extension_search_path  # noqa: E402


psycopg = pytest.importorskip("psycopg")


def _db_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set")
    return url


def test_search_path_includes_the_extension_schema():
    """The resolved search_path must name the schema pgvector lives in."""
    url = _db_url()
    with psycopg.connect(url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT n.nspname
                  FROM pg_extension e
                  JOIN pg_namespace n ON n.oid = e.extnamespace
                 WHERE e.extname = 'vector'
                """
            )
            row = cur.fetchone()
    if row is None:
        pytest.skip("the vector extension is not installed in this database")
    vector_schema = row[0]

    search_path = _extension_search_path(url)

    assert search_path, "the extension schema was not resolved"
    assert vector_schema in search_path.split(","), (
        f"the search_path {search_path!r} does not contain the schema "
        f"pgvector lives in ({vector_schema!r})"
    )
    # `public` must stay first, so unqualified tables still resolve the
    # way the app expects.
    assert search_path.split(",")[0] == "public"
    # NO space after the comma: the string goes to libpq as the
    # connection's `options` (-c search_path=...), and libpq splits the
    # options string on whitespace -- "public, mailbox" makes "mailbox"
    # a separate option and the server refuses the connection.
    assert " " not in search_path


def test_the_halfvec_cast_resolves_with_the_search_path():
    """The cast the app actually uses must work on a pooled connection."""
    url = _db_url()
    search_path = _extension_search_path(url)
    if not search_path:
        pytest.skip("the extension schema was not resolved")

    with psycopg.connect(
        url, connect_timeout=10, options=f"-c search_path={search_path}"
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT '[1,2,3]'::halfvec IS NOT NULL")
            assert cur.fetchone()[0] is True


def test_an_unreachable_database_does_not_raise():
    """A failure to resolve must not stop the app from starting."""
    # Port 1 is not a PostgreSQL server; the resolver must swallow it.
    assert _extension_search_path(
        "postgresql://nobody:nobody@127.0.0.1:1/nodb"
    ) == ""
