An agent has THREE names, and they are not the same thing:

| form | example | what it is |
|---|---|---|
| key | `example` | the identifier — the schema, `app.agents`, the API path, the `agent_name` column, the group's roster row |
| display name | `EXAMPLE` | the label — the chat header, the entity list, the group bubble |
| schema form | `example` | the key, folded |

The harness conflated the first two, so the label could only ever be the
key with different capitals. A rename to a lower-case key therefore took
the label with it: the chat header showed the key, the entity list showed
the key, the group chips showed the key.

This separates them. The display name is read from the agent's own
`IDENTITY.md` (`**Name:**`), so the agent declares what it is called and
the operator does not have to configure it. The key stays the identifier
everywhere it has to be one.

## It also fixes the empty chat window

A bookmark — or a link, or a person typing the name the agent answers to
— carrying the label used to reach an API that only knew the key, and got
a 400. The chat window then rendered **empty**: the data was intact, the
service healthy, and a count query proved the conversations present.

Now a name in a URL resolves to the agent:

- the web route redirects the label to the canonical key URL;
- the API rewrites the path segment before the route sees it, so a stale
  bookmark keeps working;
- the name the caller sent is kept, so an icon uploaded under the old
  name is still found.

The HMI renders the label and links with the key — in the entity list and
in the group roster and bubbles alike.

## Notes for the reviewer

- The examples are the neutral placeholders the rest of the repo uses.
- The display name is a **label**: it never reaches SQL, is never an
  identifier, and does not have to be unique (an ambiguous label resolves
  to nothing rather than guessing).
- An install whose identity file declares no name still works: the label
  falls back to the key, and the old capitalised form still finds the
  agent through the folded-key fallback.
- 323 tests pass, including 28 new ones across the helpers and the
  routes. The route tests are the ones that matter: a count query proves
  storage, never a screen.

Proposed by: Grove
