import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import process from "node:process";

// VM-only browser regression for the real app and real disposable E2E backend.
// This checks shell geometry/navigation, not saved gateway policy or model calls.
const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const out = path.resolve(process.argv[2] || path.join(root, "shell-results"));
const require = createRequire(path.join(root, "e2e/package.json"));
const { chromium, request, expect } = require("@playwright/test");
const { build } = createRequire(path.join(root, "frontend/package.json"))(
  "esbuild",
);
await mkdir(out, { recursive: true });
const helper = path.join(out, "provision.mjs");
await build({
  stdin: {
    contents:
      "export { provisionActor } from './lib/provisioning'; export { authInitScript } from './lib/auth';",
    resolveDir: path.join(root, "e2e"),
    loader: "ts",
  },
  bundle: true,
  format: "esm",
  platform: "node",
  outfile: helper,
});
const { provisionActor, authInitScript } = await import(pathToFileURL(helper));
const req = await request.newContext();
const actor = await provisionActor(req, "responsive-shell");
const browser = await chromium.launch({ headless: true });
// Route pins: src/routes/sections/dashboard.jsx and src/routes/paths.js.
const routes = [
  ["datasets", "/dashboard/develop"],
  ["observe", "/dashboard/observe"],
  ["settings", "/dashboard/settings/profile-settings"],
  ["gateway", "/dashboard/gateway"],
  ["providers", "/dashboard/gateway/providers"],
  ["keys", "/dashboard/gateway/keys"],
  ["budgets", "/dashboard/gateway/budgets"],
  ["guardrails", "/dashboard/gateway/guardrails/configuration"],
  ["logs", "/dashboard/gateway/logs"],
  ["analytics", "/dashboard/gateway/analytics"],
  ["sessions", "/dashboard/gateway/sessions"],
];
const results = [];
const blocked = new Set();
try {
  for (const mode of ["light", "dark"])
    for (const width of [390, 768, 1440]) {
      const context = await browser.newContext({
        viewport: { width, height: 960 },
        colorScheme: mode,
        reducedMotion: "reduce",
        serviceWorkers: "block",
      });
      await context.route("**/*", (route) => {
        const url = new URL(route.request().url());
        if (
          ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname) ||
          ["data:", "blob:"].includes(url.protocol)
        )
          return route.continue();
        blocked.add(url.hostname);
        return route.abort();
      });
      await context.addInitScript(authInitScript, {
        ...actor.tokens,
        organizationId: actor.organizationId,
        workspaceId: actor.workspaceId,
      });
      await context.addInitScript(
        (mode) =>
          localStorage.setItem(
            "settings",
            JSON.stringify({
              themeMode: mode,
              themeLayout: "vertical",
              themeDirection: "ltr",
              themeContrast: "default",
              themeColorPresets: "default",
              themeStretch: false,
            }),
          ),
        mode,
      );
      const page = await context.newPage();
      for (const [name, route] of routes) {
        const row = { name, route, mode, width, errors: [] };
        const check = async (label, fn) => {
          try {
            await fn();
          } catch (error) {
            row.errors.push({ label, error: error.message });
          }
        };
        try {
          await page.goto(`http://localhost:3100${route}`, {
            waitUntil: "domcontentloaded",
          });
          await expect(page.getByRole("main")).toBeVisible({ timeout: 60000 });
          await page.evaluate(() => document.fonts.ready);
          await expect(page).toHaveURL(`http://localhost:3100${route}`);
          await check("requested theme is active", () =>
            expect(page.locator("html")).toHaveAttribute("data-theme", mode),
          );
          await page.waitForTimeout(500); // settle existing width transitions, not a readiness substitute
          row.geometry = await page.getByRole("main").evaluate((main) => {
            const rect = main.getBoundingClientRect();
            return {
              viewport: innerWidth,
              document: document.documentElement.scrollWidth,
              left: rect.left,
              right: rect.right,
              width: rect.width,
            };
          });
          await page.screenshot({
            path: path.join(out, `${name}-${mode}-${width}.png`),
            fullPage: true,
          });
          await check("document stays within viewport", () =>
            assert.ok(
              row.geometry.document <= width + 1,
              JSON.stringify(row.geometry),
            ),
          );
          await check("main stays within viewport", () =>
            assert.ok(
              row.geometry.left >= -1 && row.geometry.right <= width + 1,
              JSON.stringify(row.geometry),
            ),
          );
          if (width < 900) {
            await check("main owns mobile content width", () =>
              assert.ok(
                Math.abs(row.geometry.width - width) <= 1,
                JSON.stringify(row.geometry),
              ),
            );
            await check(
              "mobile navigation opens, stays open and restores focus",
              async () => {
                const open = page.getByRole("button", {
                  name: "Open navigation",
                  exact: true,
                });
                await open.click({ timeout: 3000 });
                const navigation = page.getByRole("navigation", {
                  name: "Main navigation",
                  exact: true,
                });
                await expect(navigation).toBeVisible();
                await page.waitForTimeout(350);
                await expect(navigation).toBeVisible();
                // MUI correctly aria-hides the background while the modal is
                // open; inspect the trigger's DOM state without querying the
                // intentionally hidden background through the accessibility tree.
                await expect(
                  page.locator('button[aria-controls="dashboard-navigation"]'),
                ).toHaveAttribute("aria-expanded", "true");
                await page.screenshot({
                  path: path.join(
                    out,
                    `${name}-${mode}-${width}-navigation.png`,
                  ),
                });
                await page.keyboard.press("Escape");
                await expect(navigation).not.toBeVisible();
                await expect(open).toBeFocused();
              },
            );
            if (name === "gateway")
              await check(
                "gateway navigation opens and routes from drawer",
                async () => {
                  await page
                    .getByRole("button", {
                      name: "Open gateway navigation",
                      exact: true,
                    })
                    .click({ timeout: 3000 });
                  const navigation = page.getByRole("navigation", {
                    name: "Gateway navigation",
                    exact: true,
                  });
                  await expect(navigation).toBeVisible();
                  await page.screenshot({
                    path: path.join(
                      out,
                      `${name}-${mode}-${width}-gateway-navigation.png`,
                    ),
                  });
                  await navigation
                    .getByRole("link", { name: "Providers", exact: true })
                    .click();
                  await expect(page).toHaveURL(
                    "http://localhost:3100/dashboard/gateway/providers",
                  );
                  await expect(navigation).not.toBeVisible();
                },
              );
          } else {
            await check("desktop main navigation remains visible", () =>
              expect(
                page.getByRole("navigation", {
                  name: "Main navigation",
                  exact: true,
                }),
              ).toBeVisible({ timeout: 3000 }),
            );
            await check("desktop does not show mobile trigger", () =>
              expect(
                page.getByRole("button", {
                  name: "Open navigation",
                  exact: true,
                }),
              ).toHaveCount(0),
            );
          }
        } catch (error) {
          row.errors.push({ label: "page readiness", error: error.message });
        }
        results.push(row);
        await writeFile(
          path.join(out, "results.json"),
          JSON.stringify({ results, blockedHosts: [...blocked] }, null, 2),
        );
      }
      await context.close();
    }
} finally {
  await browser.close();
  await req.dispose();
}
const failed = results.filter((row) => row.errors.length);
console.log(
  `Responsive shell: ${results.length - failed.length}/${results.length} rows passed`,
);
for (const row of failed) console.log(JSON.stringify(row));
process.exitCode = failed.length ? 1 : 0;
