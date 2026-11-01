"""Route tests for the agent's three names (2026-11-01).

The bug these cover: an install whose agent was renamed from a
capitalised key to a lower-case one left its bookmarks pointing at the
old form. The URL carried 'EXAMPLE', the API only knew 'example', and
the chat window rendered EMPTY -- with the data intact, the service
healthy, and a count query proving four conversations present.

So the tests here are about the ROUTES, not the helpers: a name that
arrived in a URL has to reach the right agent, and the page has to show
the label.

No DB -- the routes are exercised with a fake registry, the same way
the HMI would reach them.
"""
import sys
import tempfile
import unittest

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from app.agent.bootstrap import BootstrapLoader, BootstrapRegistry
from app.config import load_config


def _make_app(identity_text: str = "- **Name:** EXAMPLE\n"):
    """A minimal Flask app with one agent whose key is 'example'."""
    from flask import Flask

    cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml.example").read_text())
    cfg["app"]["secret_key"] = "test-secret"
    cfg["app"]["bind"] = "127.0.0.1:0"
    cfg["bootstrap"]["files"] = {
        "IDENTITY.md": {"path": "IDENTITY.md", "max_bytes": 8000},
    }
    tmp = tempfile.mkdtemp()
    (Path(tmp) / "IDENTITY.md").write_text(identity_text)
    cfg["app"]["pie_root"] = tmp
    cfg["app"]["default_agent"] = "example"
    cfg["app"]["agents"] = ["example"]
    cfg_path = Path(tmp) / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))

    full = load_config(str(cfg_path))
    registry = BootstrapRegistry(
        {"example": BootstrapLoader(full, workspace_root=Path(tmp))}
    )

    app = Flask(__name__)
    app.config["HARNESS_CONFIG_OBJ"] = full
    app.config["SECRET_KEY"] = "test-secret"
    app.extensions["harness"] = {"bootstrap_registry": registry}
    app.jinja_loader.searchpath.insert(
        0, str(Path(__file__).resolve().parent.parent / "app" / "templates")
    )
    return app


class TestTheWebRoute(unittest.TestCase):
    """GET /c/<entity> -- the chat page."""

    def setUp(self):
        from app.routes.web import bp as web_bp

        self.app = _make_app()
        self.app.register_blueprint(web_bp)
        self.client = self.app.test_client()

    def test_the_display_name_url_redirects_to_the_key(self):
        # The old bookmark. It has to land on the agent, not 404.
        r = self.client.get("/c/EXAMPLE")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/c/example")

    def test_the_key_url_renders(self):
        r = self.client.get("/c/example")
        self.assertEqual(r.status_code, 200)

    def test_the_page_shows_the_display_name(self):
        # The label in the header AND in the title.
        body = self.client.get("/c/example").get_data(as_text=True)
        self.assertIn('id="entityName">EXAMPLE<', body)
        self.assertIn("<title>EXAMPLE — harness</title>", body)

    def test_the_page_carries_the_key_to_the_javascript(self):
        # The JS calls /api/v1/entities/<name>/... -- it has to be the
        # KEY, or every API call 400s and the window is blank.
        body = self.client.get("/c/example").get_data(as_text=True)
        self.assertIn('data-entity="example"', body)
        self.assertNotIn('data-entity="EXAMPLE"', body)

    def test_an_unknown_agent_is_a_404(self):
        self.assertEqual(self.client.get("/c/nobody").status_code, 404)


class TestTheApiCanonicalisation(unittest.TestCase):
    """The entity routes have to accept the display name too."""

    def setUp(self):
        from flask import g

        from app.routes.agent_lookup import canonicalise_agent_name

        self.app = _make_app()
        self.client = self.app.test_client()
        self._g = g
        self._canonicalise = canonicalise_agent_name

        # The entity routes need a DB, so the hook is exercised
        # directly: it is the thing that makes a stale bookmark work.
        @self.app.route("/api/v1/_probe/<name>")
        def probe(name):
            from flask import request

            self._canonicalise()
            return {
                "got": request.view_args["name"],
                "original": getattr(self._g, "original_entity_name", None),
            }

    def test_the_hook_rewrites_the_display_form_to_the_key(self):
        r = self.client.get("/api/v1/_probe/EXAMPLE")
        self.assertEqual(r.get_json()["got"], "example")
        self.assertEqual(r.get_json()["original"], "EXAMPLE")

    def test_the_key_passes_through_unchanged(self):
        r = self.client.get("/api/v1/_probe/example")
        self.assertEqual(r.get_json()["got"], "example")
        self.assertIsNone(r.get_json()["original"])


class TestTheGroupRoster(unittest.TestCase):
    """The roster stores the KEY; a client may send the display name."""

    def setUp(self):
        from app.routes.groups import bp as groups_bp

        self.app = _make_app()
        self.app.register_blueprint(groups_bp)
        self.client = self.app.test_client()

    def test_the_roster_endpoint_carries_the_display_names(self):
        from app.routes.groups import _agent_display_names

        with self.app.app_context():
            self.assertEqual(_agent_display_names(), {"example": "EXAMPLE"})


class TestAnInstallWithNoDeclaredName(unittest.TestCase):
    """An identity file with no '**Name:**' line still works."""

    def setUp(self):
        from app.routes.web import bp as web_bp

        self.app = _make_app(identity_text="# IDENTITY.md\n\nNo name field here.\n")
        self.app.register_blueprint(web_bp)
        self.client = self.app.test_client()

    def test_the_key_url_renders(self):
        self.assertEqual(self.client.get("/c/example").status_code, 200)

    def test_the_header_falls_back_to_the_key(self):
        body = self.client.get("/c/example").get_data(as_text=True)
        self.assertIn('id="entityName">example<', body)

    def test_the_old_capitalised_url_still_finds_the_agent(self):
        # The folded-key fallback: 'EXAMPLE' resolves to 'example' even
        # with no display name declared.
        r = self.client.get("/c/EXAMPLE")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/c/example")


if __name__ == "__main__":
    unittest.main()
