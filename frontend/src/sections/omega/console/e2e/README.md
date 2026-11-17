# console-frame e2e

Playwright proof that `ConsoleFramePage` frames the Omega Console in real
browsers (Chromium, Firefox, WebKit): the key is a freshly minted Omega session
assertion handed over omega-embed/1 and never put in a URL or storage, the
parent URL and the Console's route stay in step (Back, reload, deep links), the
theme follows Future AGI, and the Console's "Future AGI" entry brings the user
back. It also covers the calm states: connecting, a role without Omega, Omega
not set up, and the Console not answering.

What runs (`console-host.cjs`):

- A production build of this frontend (`build-harness.cjs`) with only
  `src/routes/sections/index.jsx` swapped for `harness/HarnessRoutes.jsx`,
  which mounts the real `ConsoleFramePage` inside the real
  `OmegaConsoleLayout` at `/dashboard/console/*`, and the real product
  switcher on every other page. Sentry is on and points at the harness's own
  ingest sink (`sentry-sink.cjs`).
- **Fake host** `http://localhost:3061`: `/omega/console/` is
  `fake-console.cjs`, a stand-in that speaks omega-embed/1 the way the EE
  Console's child bridge does, including `hello.want`, `embed.telemetry` and
  `embed.badge`.
- **EE host** `http://localhost:3062` (only with `CONSOLE_E2E_EE_DIST`): the EE
  Console's embed build under `/omega/console/` and a customer-only fixture
  gateway at `/omega/api/*` built from the EE lane's `e2e/embed/fixtures.ts`
  (`CONSOLE_E2E_EE_FIXTURES`). It accepts only keys this host minted.
- **Two origins** (only with `CONSOLE_E2E_XO_ORIGIN_DIR` and
  `CONSOLE_E2E_EE_FIXTURES`, `xo-hosts.cjs`): the host on
  `http://127.0.0.1:43310`, the EE Console packaged for its own origin on
  `:43311`, served by Future AGI's own Console-origin listener code
  (`services/omega-gateway/src/console-origin.mjs`: its CSP with
  `frame-ancestors <host>`, its security headers, its CSP report reader), and
  a foreign site on `:43312` that tries to frame the Console and says hello.
- Every host answers `POST /accounts/omega/session-exchange/` like Future
  AGI's backend (`count` keys per call, at most 8; a 403 viewer refusal or a
  503 when a test says so) and serves `/config.js` the way the docker
  entrypoint does.

The specs:

| File | What it proves |
|---|---|
| `console.e2e.cjs` | the frame's key, URL sync, theme, way back, calm states (fake Console) |
| `ee-console.e2e.cjs` | the same against the EE Console's embed build |
| `host.e2e.cjs` | CP F-HOST: P7 batched keys, P8 Sentry forwarding, P10 viewer panel, P15 Inbox count, Hub and remix landings |
| `xo.e2e.cjs` | CP P6 and SEC5: sandboxed frame on its own origin, foreign hello ignored, foreign framing refused, a malformed answer as one Sentry event, CSP reports |
| `notify.e2e.cjs` | P15 opt-in notification (full Chromium only) |
| `axe.e2e.cjs` | A11Y1 on the host's own parts (needs `AXE_CORE_FILE`) |
| `screens.e2e.cjs` | screenshots at 1280 and 390, light and dark (Chromium) |

Run (builds once, starts and stops the hosts):

```sh
PLAYWRIGHT_NODE_MODULES=/path/to/node_modules-with-playwright \
CONSOLE_E2E_WORK=/path/to/scratch \
CONSOLE_E2E_EE_DIST=/path/to/omega-ee/apps/console/dist-embed \
CONSOLE_E2E_EE_FIXTURES=/path/to/omega-ee/apps/console/e2e/embed/fixtures.ts \
CONSOLE_E2E_XO_ORIGIN_DIR=/path/to/the-console-origin-build \
AXE_CORE_FILE=/path/to/axe-core/axe.min.js \
CONSOLE_HOST_EVIDENCE=/path/to/evidence \
CONSOLE_SCREENS_DIR=/path/to/screens \
./run-e2e.sh            # add --project=chromium, or a file filter
```

`CONSOLE_HOST_EVIDENCE` collects one JSON receipt per check and browser
(`evidence.cjs`). `CONSOLE_E2E_REUSE_BUILD=1` skips the build when one exists.

`CONSOLE_E2E_XO_ORIGIN_DIR` is the EE Console built for its own origin and
told to trust this harness's host as its parent. The EE lane's own
`dist-embed-xo` trusts its own test host (`127.0.0.1:43210`), so build a copy
into scratch from the EE checkout, then lay out only the Console (the
build's other apps, `index.html` and `app.html`, stay off the Console origin,
as in EE's `e2e/embed/originServer.ts`):

```sh
cd <omega-ee>/apps/console && \
OMEGA_CONSOLE_BASE=/omega/console/ OMEGA_CONSOLE_OUT_DIR=<scratch>/build \
OMEGA_CONSOLE_BUILD=host-xo-<ee sha> \
VITE_OMEGA_EMBED_PARENT_ORIGINS=http://127.0.0.1:43310 npx vite build
O=<scratch>/origin/omega/console && mkdir -p "$O" && \
cp -R <scratch>/build/. "$O" && rm "$O/index.html" "$O/app.html" && \
cp "$O/console.html" <scratch>/origin/index.html
```

## On the rig

`rig/host-live.mjs` runs the host's checks against a real stack (the cloud
rig or the Mac's stack): the real login page, the real gateway, Django
exchange and EE, and a real Future AGI viewer it seeds in tenant A. From the
served checkout, one browser a run:

```sh
node frontend/src/sections/omega/console/e2e/rig/host-live.mjs chromium
```

It writes `host-live-<browser>.json` (no key, password or token) and
screenshots under `<evidence>/console-host/`.
