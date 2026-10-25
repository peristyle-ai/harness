# Third-party notices

`app/static/` carries two vendored browser bundles. They are **vendored,
not imported**: this is a Python/Flask app with no JavaScript build step, so
there is no `package.json`, no `node_modules` and no bundler that could
resolve an `import` at runtime. Both are loaded with a plain `<script>` tag
in `app/templates/chat.html` and `app/templates/group.html`, and both are
served from this origin — which is what lets the app ship a strict
`default-src 'self'` Content-Security-Policy with no third-party origin in it.

## marked — v13.0.3

| | |
|---|---|
| File | `app/static/marked.min.js` |
| Project | https://github.com/markedjs/marked |
| Licence | MIT |
| Copyright | Copyright (c) 2011-2024, Christopher Jeffrey |
| Used for | parsing the model's markdown into HTML (`marked.parse`) |

## DOMPurify — 3.1.6

| | |
|---|---|
| File | `app/static/purify.min.js` |
| Project | https://github.com/cure53/DOMPurify |
| Licence | Apache-2.0 OR MPL-2.0 |
| Copyright | Copyright (c) Cure53 and other contributors |
| Used for | sanitising the HTML that `marked` produced (`DOMPurify.sanitize`) |

## Why the sanitiser is not optional

`marked` converts model output into HTML. That HTML is inserted into the
page, so without a sanitiser a model that emits `<img onerror=…>` would
execute in the browser of whoever is reading the chat. `DOMPurify` is what
makes the rendering path safe; `app.js` degrades to escaped plain text if
either library is missing, and refuses to render unsanitised HTML when
`marked` is present but `DOMPurify` is not.

## Updating a vendored bundle

1. Take the release from the upstream project — the `.min.js` from the
   published asset, not a locally re-minified copy.
2. Replace the file in `app/static/` and update the version + licence row
   above.
3. Bump `CACHE_NAME` in `app/static/sw.js`. The service worker is
   cache-first for the static shell, so an installed PWA will otherwise
   keep serving the old bundle.
4. Confirm the licence banner at the top of the file is intact — it is the
   attribution the licence requires.

## A note on the source maps

`purify.min.js` ends with a `//# sourceMappingURL=purify.min.js.map`
comment, but the `.map` file is **not** in this repository. The comment is
upstream's and is harmless — a browser only fetches the map when devtools
are open — but it does produce a 404 in the console for anyone who does.
Regenerating the map needs the upstream toolchain, so it is left alone
rather than fabricated.
