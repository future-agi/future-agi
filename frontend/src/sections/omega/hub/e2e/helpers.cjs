/* eslint-env node */
// Shared helpers for the Hub e2e: sign-in through the real backend, and
// screenshots at 1280 and 390 wide, light and dark.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const PORT = Number(process.env.HUB_HARNESS_PORT || 3041);
const APP = `http://127.0.0.1:${PORT}`;
const SCREENS =
  process.env.HUB_SCREENS_DIR ||
  path.join(os.homedir(), ".futureagi-local/release-tabs/screens/hub-frontend");
const SIZES = [
  { width: 1280, height: 900 },
  { width: 390, height: 844 },
];

const registry = () => import("./lib/registry.mjs");

// A real session: POST /accounts/token/ as the user, then put the tokens
// where the app's own login puts them, before any page script runs.
async function signIn(context, who) {
  const { login } = await registry();
  const session = await login(`omega-hub-${who}@futureagi.local`);
  await context.addInitScript((t) => {
    localStorage.setItem("accessToken", t.access);
    localStorage.setItem("refreshToken", t.refresh);
    localStorage.setItem("rememberMe", "true");
  }, session);
  return session;
}

async function settle(page) {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.evaluate(() => document.fonts?.ready);
  await page.waitForTimeout(350);
}

// Every variant of the current page: 1280 and 390 wide, light and dark. The
// app follows the system colour scheme, so emulating it switches the theme.
async function shootAll(page, name, { fullPage = true, sizes = SIZES, modes = ["light", "dark"] } = {}) {
  fs.mkdirSync(SCREENS, { recursive: true });
  const original = page.viewportSize();
  const written = [];
  // No hover or focus state in the picture.
  await page.mouse.move(0, 0);
  await page.evaluate(() => document.activeElement?.blur?.());
  for (const mode of modes) {
    await page.emulateMedia({ colorScheme: mode });
    for (const size of sizes) {
      await page.setViewportSize(size);
      await settle(page);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      const file = path.join(SCREENS, `${name}-real-${size.width}-${mode}.png`);
      await page.screenshot({ path: file, fullPage });
      written.push({ file, overflow });
    }
  }
  await page.emulateMedia({ colorScheme: "light" });
  if (original) await page.setViewportSize(original);
  return written;
}

module.exports = { APP, SCREENS, SIZES, registry, shootAll, settle, signIn };
