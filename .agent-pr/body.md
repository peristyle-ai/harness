The operator's screenshot of the group chat (2026-11-01): with the thought stream and the tool-call trace collapsed by default, the bubble showed an EMPTY coloured bar where the answer goes. The one line that says what the agent is doing RIGHT NOW was only visible if you opened the thought stream.

So the bubble now carries a STATUS LINE: the model's latest thought, at the top, live. It is a separate element from the answer, because a thought must never overwrite the answer, and the answer must never wipe the thought - which is exactly what happens when both are the same element (the old code wrote the streamed text straight into .gmsg-body).

Both surfaces get it: the group chat (group.js) and the 1:1 chat (app.js). A feature that exists in one and not the other is the group-chat-lags-behind-the-DM complaint all over again.

The status line disappears when the answer arrives: it said what the agent was doing RIGHT NOW, and the answer is what it did.

The service worker is cache-first for the static shell, so the bundle change bumps CACHE_NAME (harness-v13 to harness-v14). Without that an installed PWA keeps serving the old bundle and the status line never reaches it.

Tests: tests/test_live_status_line.py, 12 tests. Seen failing on the old code first (11 failed, 1 passed - the parse check), passing after the change (12 passed).
