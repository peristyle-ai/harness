The shipped template's llm block still carried the old MiniMax-M3 defaults while the production install runs minimax_m31_flash / MiniMax-M3.1-Flash. A fresh install therefore started on a different adapter, model, output budget and timeout from the one that is actually in daily use.

This changes the template's global llm block to the production values:

  adapter:    minimax_m3   -> minimax_m31_flash
  model:      MiniMax-M3   -> MiniMax-M3.1-Flash
  max_tokens: 2048         -> 524288
  timeout_s:  60           -> 180

temperature (0.7) and streaming (1) already matched and are unchanged. The commented per-agent example further down the same file already carried these values, so the global block now agrees with it.

One file, one concern: config.yaml.example. No secret is touched -- api_key stays the YOUR_LLM_API_KEY_HERE placeholder, and the placeholder check in tests/test_security.py still passes.
