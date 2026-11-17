# Studio tab e2e

A Playwright proof that the Studio tab works in real browsers (Chromium,
Firefox, WebKit) against the real `omega dev`, and that the connect key never
reaches Future AGI's URL, storage, network log or analytics.

What runs:

- **Future AGI** (`http://localhost:3051`, `STUDIO_E2E_PARENT_PORT`): a
  production build of this frontend (`serve-harness.cjs`). `index.html` with
  its connect intake, `main.jsx` (Sentry, PostHog, GTM), `app.jsx` (New Relic,
  Mixpanel, providers, theme) and every analytics SDK are the real ones, with
  keys set so each SDK starts. Only `src/routes/sections/index.jsx` is swapped
  for `harness/HarnessRoutes.jsx`, which registers the real `StudioPage` on the
  shell's two routes (`/dashboard/studio` and `/dashboard/studio/*`). There is
  no `DashboardLayout` (no sidebar, no sign-in).
- **omega dev** (`omega-dev.cjs`): the BUILT CLI from the accepted
  studio-embed head, started per test file on a copy of Studio's fixture
  project with its own HOME/XDG/git config and no provider or tracing keys,
  `--port 0`, `--reload off`. One instance runs with
  `--embed-origin http://localhost:3051`, a second without it (Studio's
  default `frame-ancestors 'none'`). The fixture's `router` entry uses a
  deterministic function model; that is the only fake.
- **Sink**: embed-host's `../../embed/e2e/sink.cjs` records every request
  (URL, headers, decoded body, frame) and answers every non-loopback host
  locally, so nothing leaves the machine.

`studio.e2e.cjs` (all three engines):

1. omega dev prints the Future AGI link only for the origin it was given.
2. Stage 2: the printed link frames Studio; a scripted run inside the frame
   succeeds; its id is in omega dev's own `GET /studio/v1/runs`; the address
   bar mirrors the run's thread without reloading the frame; the key is in no
   URL, history entry, storage, cookie or analytics payload and only ever an
   `Authorization` header on the frame's own API calls; Future AGI's page makes
   zero requests to Studio; a reload reconnects through Studio's kept key; a
   saved connection reconnects after Disconnect.
3. Pasting the local link connects.
4. Stage 1: omega dev without `--embed-origin` is named "running but won't
   open inside Future AGI" within 6 s of the frame mounting (Studio's 4.5 s
   hello bound, `STUDIO_HELLO_TIMEOUT_MS`, and one probe), with the exact
   command; "Open local Studio directly" opens the loopback page.
5. A bare address in a fresh tab (no key to send, none kept by Studio): the
   frame shows "Reconnecting" until Studio answers `needs-key` itself, then the
   tab asks for the link again, sooner than EmbeddedSurface's 8 s fallback.
6. Not running, and a non-loopback link, are refused with a reason.

`screens.e2e.cjs` (Chromium only) writes every state at 1280 and 390 wide,
light and dark, each reached against the real omega dev.

## Build omega dev (read-only copy of the accepted head)

The studio-embed worktree belongs to another lane, so it is never built in
place. An APFS clone costs almost no disk:

```sh
B=<this worktree>/.omega/build            # ignored by the repo's build/ rule
cp -cR <core>/oss-wave2/studio-embed "$B/omega-studio-embed"
rm -f "$B/omega-studio-embed/.git"        # the copy must not act as that worktree
rm -rf "$B/omega-studio-embed/.omega"
mkdir -p "$B/home" "$B/xdg"
export HOME="$B/home" XDG_CONFIG_HOME="$B/xdg" XDG_CACHE_HOME="$B/xdg" \
  GIT_CONFIG_GLOBAL=/dev/null GIT_CEILING_DIRECTORIES="$B"
for p in studio-protocol studio studio-ui cli; do
  (cd "$B/omega-studio-embed/packages/$p" && npm run -s build)
done
```

The clone was taken when the studio-embed worktree was clean at
`9dc972aaa2fc8f9299579ec90959f8034533e3d2`; `.accepted-head` in the clone
records it. `.omega/build` is kept so a verifier can re-run without
rebuilding.

## Run

```sh
B=<this worktree>/.omega/build
PLAYWRIGHT_NODE_MODULES="$B/omega-studio-embed/node_modules/.pnpm/playwright@1.60.0/node_modules" \
PLAYWRIGHT_BROWSERS_PATH="$HOME/Library/Caches/ms-playwright" \
STUDIO_E2E_WORK="$B/e2e-work" \
STUDIO_SCREENS_DIR="$B/screens/studio-tab" \
./run-e2e.sh            # add --project=chromium, or a file filter
```

| Variable                   | Meaning                                                                                                                                   |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `PLAYWRIGHT_NODE_MODULES`  | a `node_modules` holding `playwright` 1.60 (matching the browsers in `PLAYWRIGHT_BROWSERS_PATH`); Playwright is not a frontend dependency |
| `STUDIO_OMEGA_REPO`        | the Omega checkout whose `packages/cli/dist` runs (default `<worktree>/.omega/build/omega-studio-embed`)                                  |
| `STUDIO_E2E_WORK`          | scratch dir: the Future AGI build, results, evidence JSON, omega dev workspaces                                                           |
| `STUDIO_SCREENS_DIR`       | where screenshots go                                                                                                                      |
| `STUDIO_E2E_PARENT_PORT`   | Future AGI's port (default 3051)                                                                                                          |
| `STUDIO_E2E_REUSE_BUILD=1` | serve the last build instead of rebuilding                                                                                                |

Playwright reuses a server already listening on the parent port, so make sure
nothing else is on 3051 before a run, or it will test that server's build.

What local runs cannot prove: both pages are loopback over http, so Chrome's
local-network prompt and Safari's block on http inside https never fire. Those
causes are unit-tested in `../__tests__/StudioPage.diagnosis.test.jsx` with
stubbed browser signals on an https origin.
