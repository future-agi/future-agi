/* eslint-env node */
// Screenshots of every EmbeddedSurface state at 1280 and 390 wide, light and
// dark, for the owner's review. Chromium only. Set EMBED_SCREENS_DIR.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test, expect } = require("playwright/test");

const { CHILD, PARENT } = require("./ports.cjs");
const { attachSink } = require("./sink.cjs");

const DIR =
  process.env.EMBED_SCREENS_DIR ||
  path.join(process.env.EMBED_E2E_WORK || os.tmpdir(), "screens");

const STATES = [
  { name: "connecting", connect: `${CHILD}/silent`, state: "connecting" },
  { name: "connected", connect: CHILD, state: "connected", surface: true },
  { name: "needs-key", connect: `${CHILD}/needs-key`, state: "needs-key" },
  { name: "refused", connect: `${CHILD}/refused`, state: "refused" },
  {
    name: "framing-blocked",
    connect: `${CHILD}/framing-blocked`,
    state: "timeout",
  },
  { name: "not-running", connect: "http://127.0.0.1:4199", state: "timeout" },
];
const SIZES = [
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
];

test.describe("EmbeddedSurface screenshots", () => {
  test.skip(
    ({ browserName }) => browserName !== "chromium",
    "screenshots use Chromium",
  );
  test.beforeAll(() => fs.mkdirSync(DIR, { recursive: true }));

  for (const mode of ["light", "dark"]) {
    for (const size of SIZES) {
      for (const shot of STATES) {
        test(`${shot.name} ${size.width} ${mode}`, async ({
          page,
          context,
        }) => {
          await attachSink(context, { mainPage: () => page });
          await page.setViewportSize(size);
          await page.emulateMedia({ colorScheme: mode });
          await page.goto(
            `${PARENT}/dashboard/studio#connect=${shot.connect}&t=screens`,
          );
          const target = shot.surface
            ? page.getByTestId("omega-embedded-surface")
            : page.getByTestId("omega-embed-gate");
          await expect(target).toHaveAttribute("data-state", shot.state, {
            timeout: 15_000,
          });
          if (shot.surface) {
            const child = page
              .frames()
              .find((f) => f.url().startsWith(`${CHILD}/child.html`));
            await expect
              .poll(() => child.evaluate(() => window.__embed.theme))
              .toBe(mode);
          }
          await page.waitForTimeout(400); // let fonts and the fade settle
          await page.screenshot({
            path: path.join(DIR, `${shot.name}-${size.width}-${mode}.png`),
          });
        });
      }
    }
  }
});
