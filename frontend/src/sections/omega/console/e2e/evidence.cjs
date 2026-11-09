/* eslint-env node */
// Receipts and screenshots for lane CONSOLE-HOST's browser checks, written
// under CONSOLE_HOST_EVIDENCE (one JSON per check and browser, plus PNGs at
// 1280 and 390 wide in light and dark). Nothing is written when it is unset.
const fs = require("node:fs");
const path = require("node:path");

const ROOT = process.env.CONSOLE_HOST_EVIDENCE || "";
let browserVersion = null;

/** Call from a beforeEach with the `browser` fixture: receipts name the build. */
function noteBrowser(browser) {
  browserVersion = browser?.version?.() ?? null;
}

const slug = (text) =>
  text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 80);

function writeReceipt(testInfo, name, data) {
  if (!ROOT) return;
  const dir = path.join(ROOT, "browser", testInfo.project.name);
  fs.mkdirSync(dir, { recursive: true });
  const receipt = {
    check: name,
    browser: testInfo.project.name,
    browserVersion,
    at: new Date().toISOString(),
    ...data,
  };
  fs.writeFileSync(
    path.join(dir, `${slug(name)}.json`),
    `${JSON.stringify(receipt, null, 2)}\n`,
  );
}

const VIEWPORTS = [
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
];

/**
 * Screenshots of the page at both widths in both themes. `prepare(mode)` puts
 * the page in the state to shoot after each theme change.
 */
async function shoot(page, testInfo, name, setTheme, prepare = async () => {}) {
  if (!ROOT) return [];
  const dir = path.join(ROOT, "screens", testInfo.project.name);
  fs.mkdirSync(dir, { recursive: true });
  const files = [];
  for (const mode of ["light", "dark"]) {
    await setTheme(mode);
    for (const viewport of VIEWPORTS) {
      await page.setViewportSize(viewport);
      await prepare(mode, viewport);
      const file = path.join(
        dir,
        `${slug(name)}-${viewport.width}-${mode}.png`,
      );
      await page.screenshot({ path: file });
      files.push(path.relative(ROOT, file));
    }
  }
  await page.setViewportSize(VIEWPORTS[0]);
  return files;
}

module.exports = { noteBrowser, shoot, writeReceipt };
