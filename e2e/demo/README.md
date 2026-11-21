# Skills + Agent contexts walkthrough (recording)

Records the product walkthrough video against a running Standalone E2E stack. It is not a test
flow and is not collected by `playwright.config.ts`.

```bash
# on the stack host, from the repo root: Standalone stack with a public HTTPS origin
VITE_HOST_API=https://api.acme-support.example E2E_STACK=standalone bin/e2e up
docker compose --env-file e2e/stack/standalone-e2e.env -f docker-compose.yml \
  -f e2e/stack/docker-compose.standalone-e2e.yml -f e2e/demo/docker-compose.demo.yml \
  -p futureagi-e2e-standalone up -d --wait --no-deps app      # runtime display name for the story
echo "127.0.0.1 app.acme-support.example api.acme-support.example" | sudo tee -a /etc/hosts
sudo node e2e/demo/public-origin.mjs leaf.pem leaf.key        # TLS cert for both names, signed by a local CA

# from e2e/
demo/run.sh all <take> <email-domain> [fast=0|1] [chapters]   # fresh synthetic org + recording
demo/verify-install.sh <take>                                  # optional: ZIP install of the skill, compare locks
python3 demo/postprocess.py raw.webm events.json out/          # edit, chapters, frames (ffmpeg)
```

- `setup.mjs` creates the synthetic "Acme Support" workspace through public APIs only: signup (the
  E2E clean-signup route), an invited admin author, five published skills, two contexts
  (`support-agent` follows production except a deliberate `escalation-tone` pin; `returns-desk`
  pins `refund-policy` v1), reviewed application cases, spend allowance and the fixture provider key.
- `walkthrough.demo.ts` drives the real UI for all eight chapters with a cursor highlight
  (`overlay.ts`). Captions are events, not page overlays: `postprocess.py` renders them in a band below
  the 1440x810 app frame, one line each, held at least 4 s. Model behaviour comes from the deterministic
  skills-fakes fixture (the title card says model responses are simulated); the only difference between
  arms is the bytes each context delivers. Chapter 8 runs the copied API command during the take and
  shows its real output in a terminal frame.
- `public-origin.mjs` (recording only) serves the stack as `https://app.acme-support.example` and
  `https://api.acme-support.example`, so the install commands and API host on camera are not loopback
  addresses. The browser context accepts the local CA; `verify-install.sh` passes it to curl.
- `events.json` marks chapters, captions, page loads, long runs, hidden steps and every stretch in which
  the page showed a loading placeholder (skeleton, busy or "Loading" region, reported by the page); `audit` events list
  any loopback address or test-infrastructure word visible when a caption appears; `note` events
  record the values read from the UI (ship/hold, repair outcome, digests). `postprocess.py` removes
  placeholders, page loads, hidden steps and most of each run wait, then writes the mp4, webm,
  chapters.md, frames and per-second contact sheets for the frame audit.
