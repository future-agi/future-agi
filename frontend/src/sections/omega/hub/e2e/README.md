# Omega Hub e2e (real backend)

A Playwright proof that the Hub pages work against the real Hub backend and
the real sandboxed checker, with a human decision before anything is listed.

What runs:

- **Backend.** `scripts/omega-hub/dev-up.sh` from this repo: Django with the
  lane settings on `127.0.0.1:8741`, Postgres, Redis and MinIO from the local
  test stack, and the checker driving `omega-hub-runner:<commit>` under
  Colima with the network off. Start it with
  `OMEGA_HUB_LANE_STATE=/tmp/hubfe-lane OMEGA_HUB_DEV_DB=omega_hub_fe_dev OMEGA_HUB_BUCKET=omega-hub-fe OMEGA_HUB_PORT=8741 OMEGA_HUB_RUNNER_IMAGE=omega-hub-runner:881b05b DOCKER_CONTEXT=colima sh scripts/omega-hub/dev-up.sh`.
- **Seed.** `node seed-official.mjs` uploads the backend's three Official
  fixtures with maintainer A's Hub token. They reach `in_review` through the
  real checker; nothing is approved by the seed.
- **App.** `serve-harness.cjs` builds this frontend with only
  `src/routes/sections/index.jsx` swapped for `harness/HarnessRoutes.jsx`,
  which mounts `HUB_ROUTES` under `/dashboard` the way the app's router does.
  `index.html`, `main.jsx`, `app.jsx` (auth, organisation and workspace
  providers, theme, React Query) and the app's axios are the real ones. It
  serves on `127.0.0.1:3041` and proxies every API path to the backend, so
  the app talks to it same-origin.
- **Users.** Maintainers A and B come from the seed; user C, an Owner of an
  organisation of their own and not a maintainer, is created by
  `lib/django.mjs`. Every page is opened with a real session from
  `POST /accounts/token/`.

`community.e2e.cjs`, in order:

1. B approves the Official seeds in the review page: acknowledges each flag,
   ticks the checklist from `GET /rules`, writes the shell part the checker
   observed, approves.
2. C sees no Review tab, and `/dashboard/hub/review` shows "No permission".
3. C creates the publisher `c-test` in Settings.
4. C uploads a broken bundle; the checks stop it (`checks_failed`) and the
   page lists each finding with its fix.
5. C uploads `csv-answers` 1.0.0 (a remix of `future-agi/first-agent` that
   imports `node:child_process` while its draft disclosure says "None." for
   shell) and follows it live to `in_review`. It is not in `/entries`.
6. B sees it in the queue, reads the flags, and requests changes with a
   reason and a note on the `child_process` line of `main.mjs`.
7. C sees the reason and the note beside `main.mjs`, uploads again with the
   shell use disclosed, and follows it to `in_review`.
8. B approves with a tightened disclosure.
9. C's submission says "Published as c-test/csv-answers@1.0.0"; the Hub home
   lists it with the Community reviewed badge; its page says "Derived from
   future-agi/first-agent" and shows B's wording under "Confirmed in
   review"; `first-agent` shows "Remixes 1" (the count beside the Remixes
   heading); C likes `first-agent`.

`moderation.e2e.cjs`: C reports an entry in the private dialog and requests
verification; B unranks it from the reports page, picks `c-test/csv-answers`
for the week, verifies `c-test`, blocks a digest, removes an entry from the
Maintainer group of its More menu (the page becomes a tombstone) and finds
each decision in the audit log under B's account.

Run (builds the harness, starts and stops it; the backend must be up):

```sh
PLAYWRIGHT_NODE_MODULES="path/to/node_modules" \
HUB_E2E_WORK=/tmp/hubfe-e2e ./run-e2e.sh
```

Replace `path/to/node_modules` with the directory containing `playwright`, whose version matches the browsers in `PLAYWRIGHT_BROWSERS_PATH`.

Screenshots go to `$HUB_SCREENS_DIR` (default set in `run-e2e.sh`)
as `<page>-real-<width>-<light|dark>.png`: real data from the local backend,
at 1280 and 390 wide. Stop the backend with `scripts/omega-hub/dev-down.sh`
and the same environment; it drops the dev database and bucket.
