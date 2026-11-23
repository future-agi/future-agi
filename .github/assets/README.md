# README assets

Every image the top-level `README.md` uses, and how to remake it. Keep the total under 12 MB and use synthetic data only.

## Visual direction

The palette comes from the grey star logo: charcoal and aluminium greys, with one silver line as the only highlight. Green and red appear only to mean pass and fail.

Type is IBM Plex Sans in sentence case. IBM Plex Mono is used only where the content really is code or trace data.

Avoid violet glows, gradient chips, all-caps eyebrow labels and a coloured word in a headline. They make the images look generated.

## Files

| File | Where it appears | Source |
|---|---|---|
| `logo-light.svg`, `logo-dark.svg` | Header | The website's grey star logo. The dark version is the original; the light version recolours the text and star darker so it reads on white. |
| `readme/system-{dark,light}.png` | How it works | `src/system.html`, rendered at 2x. A systems diagram of one round: real traffic into Trace, then Evaluate, Simulate and Improve (with the winning fix continuing and rejected fixes fanning off), then Ship and protect, and a dashed way back to the next round. Colours and type follow futureagi.com's design refresh (landing-page PR #327, r16 tokens, Inter). |
| `readme/gateway-{dark,light}.png` | Gateway and guardrails | `src/gateway.html`. An illustration of what the gateway does on every request, with a fallback example. |
| `readme/integrations-{dark,light}.png` | Works with your stack | `src/integrations.html`. Logos are drawn as single-colour masks from `src/logos/`, so they stay uniform in both themes. Only logos with a clean vector source are shown. Others go in the text table. |
| `readme/trace.gif` | Trace and monitor | Scripted animation, `src/scenes/trace.json`. Opening a trace, then zooming into the step tree and the model call. |
| `readme/evals.gif` | Evaluate and label | `src/scenes/evals.json`. Writing a rule and testing it, then the LLM judge's Fail with its reason, then the same check with Jev. |
| `readme/simulate.gif` | Simulate and test, Improve | `src/scenes/simulate.json`. Debug failures, then Run Epoch, then an Epoch ready for review. Both frames are docs captures of the Northwind Pharmacy sample voice agent, joined into one sequence. Its numbers (0% to 75%) come from that sample, not from a measured benchmark. |
| `../../frontend/public/assets/readme/demo-6keOTAOUUWI-v4.png` | Header video | YouTube still. Use a new filename for a new video, because GitHub caches images by path. |

## How the GIFs are made

1. **Capture screenshots at 2x** from a fresh `./bin/install` on a temporary cloud VM, using synthetic data: a LangGraph ride-booking agent traced with `fi-instrument`. Put them in `src/shots/` (not committed, because they are large).
   - The Debug failures and Epoch frames come from the docs' Northwind Pharmacy sample run, with the docs callouts painted out. Replace them with live captures when an environment run is set up.
2. **Edit the camera path** in `src/scenes/<name>.json`. Each entry gives a time, the screenshot to show, and the region to frame; changing the screenshot crossfades between them. Cursor clicks and captions are timed the same way.
3. **Render the frames** with `src/capture.js` (Playwright, 1200 × 750, 12 fps), with the folder served on `127.0.0.1:8766`.
4. **Encode** each GIF with:

   ```sh
   ffmpeg -framerate 12 -i frames/<name>/%04d.png -vf "fps=10,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" <name>.gif
   ```

   Keep each GIF under about 4.5 MB.

## Wanted next

- Live captures of an environment run and an Epoch, to replace the docs-based frames.
- GIFs for Datasets and Experiments, and Dashboards, and a live Gateway capture.
- A 1280 × 640 social preview, uploaded in repository settings rather than committed.
