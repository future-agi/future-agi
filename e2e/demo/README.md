# Skills + Agent contexts walkthrough (recording)

Records the product walkthrough video against a running Standalone E2E stack. It is not a test
flow and is not collected by `playwright.config.ts`.

```bash
# on the stack host, from e2e/
demo/run.sh <take> <email-domain> [fast=0|1] [chapters]   # fresh synthetic org + recording
demo/verify-install.sh <take>                               # run the copied install command, compare locks
python3 demo/postprocess.py raw.webm events.json out/ [--skip-webm]   # edit, chapters, frames (ffmpeg)
```

- `setup.mjs` creates the synthetic "Acme Support" workspace through public APIs only: signup (the
  E2E clean-signup route), an invited admin author, five published skills, two contexts
  (`support-agent` follows production except a deliberate `escalation-tone` pin; `returns-desk`
  pins `refund-policy` v1), reviewed application cases, spend allowance and the fixture provider key.
- `walkthrough.demo.ts` drives the real UI for all eight chapters with a caption bar and cursor
  highlight (`overlay.ts`). Model behaviour comes from the deterministic skills-fakes fixture; the
  only difference between arms is the bytes each context delivers.
- `events.json` marks chapters, captions, page loads and long runs; `postprocess.py` removes
  page-load skeletons and most of each run wait, then writes the mp4, chapters.md and frames.
