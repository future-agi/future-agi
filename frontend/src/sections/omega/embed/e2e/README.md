# embed-host e2e

A Playwright proof that `EmbeddedSurface` and the `omega-embed/1` bridge work in
real browsers (Chromium, Firefox, WebKit), and that the connect token never
reaches the URL, storage, the network or analytics.

What runs:

- **Parent** (`http://localhost:3031`): a production build of this frontend.
  `index.html`, `main.jsx` (Sentry, PostHog, GTM), `app.jsx` (New Relic,
  Mixpanel, providers, theme) and every analytics SDK are the real ones, with
  keys set so each SDK starts. Only `src/routes/sections/index.jsx` is swapped
  for `harness/HarnessRoutes.jsx`, which renders `EmbeddedSurface` the way the
  Studio tab does, with `requireGate` (connected only after the child's gate
  says `ready`; the fake children report it after the key).
- **Control** (`http://localhost:3033`): the same build with the guard off (the
  `index.html` intake script removed and `harness/sdk-guards.off.js` in place
  of `telemetry/sdk-guards.js`). The sink must catch the token there, which
  proves the guarded run is clean because of the guard and not because the
  sink is blind.
- **Children** (`child-server.cjs`): a fake omega-embed/1 child on
  `http://127.0.0.1:4191` and hostile pages on `http://127.0.0.1:4192`. Under
  `/studio-like` and `/studio-forgets`, `studio-like-child.cjs` behaves the way
  RC1 Studio's real child does: it holds the key in memory only, says hello
  every 250 ms, and reports its own gate. Both routes now use that same
  memory-only contract. `return-visit.e2e.cjs` checks that a reload/remount
  reports `needs-key` without replaying the spent handoff, and that only
  explicitly opening a fresh link reconnects. Every animation frame of the
  parent is sampled; neither origin stores the key.
- **Sink** (`sink.cjs`): records every request (URL, headers, decoded body,
  frame) and answers every non-loopback host locally, so nothing leaves the
  machine.

Run (builds both bundles, starts and stops every server). An existing server is
never reused: if something already listens on one of the ports, the run stops.
Move a busy port with `EMBED_PARENT_PORT`, `EMBED_CONTROL_PORT`,
`EMBED_CHILD_PORT` or `EMBED_THIRD_PORT` (`ports.cjs`; `omega dev` also
defaults to 4191):

```sh
PLAYWRIGHT_NODE_MODULES=/path/to/node_modules-with-playwright-1.60 \
EMBED_E2E_WORK=/tmp/omega-embed-host-e2e \
EMBED_SCREENS_DIR=/tmp/omega-embed-host-e2e/screens \
./run-e2e.sh            # add --project=chromium, or a file filter
```

Playwright is not a frontend dependency, so `run-e2e.sh` takes it from
`PLAYWRIGHT_NODE_MODULES`; its version must match the browsers in
`PLAYWRIGHT_BROWSERS_PATH`. `screens.e2e.cjs` writes the gate screenshots (1280
and 390 wide, light and dark) in Chromium.
