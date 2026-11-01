"""One adapter (and so one HTTP client) per agent (2026-11-01).

The bug these cover: the adapter registry was populated only for the
agents listed in llm.per_agent, so on most installs EVERY AGENT SHARED
ONE httpx.Client -- one connection pool, one connection.

A shared client is a shared failure domain. The adapter holds one
client; when the upstream resets that connection, every stream in
flight on it dies at the same moment. On 2026-11-01 two agents on one
install, sharing one client, both lost their turn on the same upstream
reset -- three milliseconds apart.

So: every agent gets its own adapter, and llm.per_agent becomes a
PARTIAL override (the fields it names replace the global ones; the rest
are inherited). Before this, an entry was a COMPLETE llm block -- eight
fields, api_key included -- so getting an isolated client meant copying
the api_key once per agent, and most installs just shared instead.

No DB, no app stack.
"""
import sys
import unittest

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from app.config import _merge_llm, load_config


def _write_config(tmp: Path, llm_extra: str = "", agents=("first", "second")) -> Path:
    cfg = f"""
app:
  name: harness
  bind: 127.0.0.1:0
  tls: {{ cert: /dev/null, key: /dev/null }}
  default_agent: {agents[0]}
  agents: [{", ".join(agents)}]
  secret_key: test-secret
db:
  url: postgresql://app:***@localhost:5432/test
  operator_role: app
  runtime_role: app
  database: test
llm:
  adapter: minimax_m31_flash
  api_key: sk-test
  base_url: https://api.example.invalid/anthropic
  model: Test-Model
  max_tokens: 1024
  timeout_s: 30
  temperature: 0.7
{llm_extra}
embedding:
  base_url: http://127.0.0.1:8080/v1
  model: Test-Embed
  dimensions: 1024
  api_key: ''
  timeout_s: 5
memory:
  top_k: 10
  vector_limit: 30
  trigram_limit: 30
  recency_decay_days: 30
  chunk: {{ max_tokens: 800, min_tokens: 20, overlap_tokens: 50 }}
  permanent: {{ max_tokens: 10000, warn_overflow: true, pin_audit: true }}
agent:
  max_tool_iterations: 5
  max_searches_per_turn: 5
  max_pins_per_turn: 3
  max_unpins_per_turn: 5
bootstrap:
  files: {{}}
  behavior_on_overflow: fail
security:
  headers:
    x_content_type_options: nosniff
    x_frame_options: DENY
    strict_transport_security: "max-age=15552000; includeSubDomains"
    referrer_policy: strict-origin-when-cross-origin
    csp: "default-src 'self'"
  rate_limit:
    default: 100/minute
    chat: 30/minute
    memory: 60/minute
  internal_token: test-internal-token
  allow_sudo: false
"""
    path = tmp / "config.yaml"
    path.write_text(cfg)
    return path


class TestPerAgentIsAPartialOverride(unittest.TestCase):
    """A per-agent entry names what changes; the rest is inherited."""

    GLOBAL = {
        "adapter": "minimax_m31_flash",
        "api_key": "sk-global",
        "base_url": "https://api.example.invalid/anthropic",
        "model": "Test-Model",
        "max_tokens": 1024,
        "timeout_s": 30,
        "temperature": 0.7,
    }

    def test_an_override_changes_only_what_it_names(self):
        merged = _merge_llm(self.GLOBAL, {"model": "Other-Model"})
        self.assertEqual(merged["model"], "Other-Model")
        self.assertEqual(merged["api_key"], "sk-global")
        self.assertEqual(merged["base_url"], self.GLOBAL["base_url"])
        self.assertEqual(merged["max_tokens"], 1024)

    def test_an_override_can_change_the_key(self):
        merged = _merge_llm(self.GLOBAL, {"api_key": "sk-other"})
        self.assertEqual(merged["api_key"], "sk-other")

    def test_an_empty_override_inherits_everything(self):
        merged = _merge_llm(self.GLOBAL, {})
        for key, value in self.GLOBAL.items():
            self.assertEqual(merged[key], value)

    def test_the_nested_per_agent_map_is_not_inherited(self):
        # 'per_agent' is a container, not an LLM setting.
        global_llm = dict(self.GLOBAL, per_agent={"x": {"model": "Y"}})
        merged = _merge_llm(global_llm, {"model": "Other-Model"})
        self.assertNotIn("per_agent", merged)

    def test_the_global_block_is_not_mutated(self):
        global_llm = dict(self.GLOBAL)
        _merge_llm(global_llm, {"model": "Other-Model"})
        self.assertEqual(global_llm["model"], "Test-Model")


class TestTheConfigLoadsAPartialOverride(unittest.TestCase):
    """The end-to-end path: a one-line override in config.yaml."""

    def test_a_one_line_override_loads(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = _write_config(
                Path(tmp),
                llm_extra="  per_agent:\n    second:\n      model: Other-Model\n",
            )
            cfg = load_config(str(path))
            self.assertIn("second", cfg.llm_per_agent)
            override = cfg.llm_per_agent["second"]
            self.assertEqual(override.model, "Other-Model")
            # everything else came from the global block
            self.assertEqual(override.adapter, "minimax_m31_flash")
            self.assertEqual(override.base_url, "https://api.example.invalid/anthropic")
            self.assertEqual(override.max_tokens, 1024)
            self.assertEqual(override.timeout_s, 30)

    def test_an_agent_with_no_override_is_absent_from_the_map(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = _write_config(Path(tmp))
            cfg = load_config(str(path))
            # no per_agent at all -> the map is empty, and every agent
            # falls back to the global block (and gets its own adapter).
            self.assertFalse(cfg.llm_per_agent)


class TestEveryAgentGetsItsOwnAdapter(unittest.TestCase):
    """The registry holds one adapter per agent, each with its own client.

    This is the whole point of the change, so it is tested directly: two
    agents on one install must not share an httpx.Client.
    """

    def _build_registry(self, agents, per_agent=None):
        """Build the registry the way app.extensions.init_app does."""
        import tempfile

        from dataclasses import replace

        from app.agent.llm import get_adapter

        with tempfile.TemporaryDirectory() as tmp:
            path = _write_config(Path(tmp), agents=agents)
            cfg = load_config(str(path))
            if per_agent:
                from app.config import _make_llm

                llm = yaml.safe_load(path.read_text())["llm"]
                overrides = {
                    name: _make_llm(_merge_llm(llm, override))
                    for name, override in per_agent.items()
                }
                cfg = replace(cfg, llm_per_agent=overrides)
            registry = {}
            for agent_name in cfg.app.agents:
                override_llm_cfg = (
                    cfg.llm_per_agent.get(agent_name) if cfg.llm_per_agent else None
                )
                agent_config = (
                    cfg if override_llm_cfg is None
                    else replace(cfg, llm=override_llm_cfg)
                )
                registry[agent_name] = get_adapter(agent_config)
            return cfg, registry

    def test_two_agents_get_two_different_clients(self):
        cfg, registry = self._build_registry(("first", "second"))
        self.assertEqual(set(registry), {"first", "second"})
        first_client = registry["first"]._client
        second_client = registry["second"]._client
        self.assertIsNot(
            first_client, second_client,
            "two agents shared one HTTP client -- a shared failure domain",
        )

    def test_each_client_has_its_own_pool(self):
        cfg, registry = self._build_registry(("first", "second"))
        self.assertIsNot(
            registry["first"]._client._transport,
            registry["second"]._client._transport,
        )

    def test_an_agent_without_an_override_still_gets_its_own_client(self):
        # The whole point: isolation does NOT require a config entry.
        cfg, registry = self._build_registry(("first", "second"))
        self.assertIn("first", registry)
        self.assertIn("second", registry)
        self.assertIsNot(registry["first"]._client, registry["second"]._client)

    def test_an_override_replaces_the_adapter_for_that_agent_only(self):
        cfg, registry = self._build_registry(
            ("first", "second"),
            per_agent={"second": {"model": "Other-Model"}},
        )
        self.assertEqual(registry["first"].model, "Test-Model")
        self.assertEqual(registry["second"].model, "Other-Model")
        self.assertIsNot(registry["first"]._client, registry["second"]._client)


if __name__ == "__main__":
    unittest.main()
