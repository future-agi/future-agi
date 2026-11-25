import { createRequire } from "node:module";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
import { createServer } from "vite";
import react from "@vitejs/plugin-react-swc";
const require = createRequire(resolve("../e2e/package.json"));
const { chromium, expect } = require("@playwright/test");
const mock = resolve(
  "src/sections/derived-metrics/__tests__/fixtures/builder-visual-api.js",
);
const sources = new Set([
  "src/auth/hooks",
  "src/contexts/OrganizationContext",
  "src/contexts/WorkspaceContext",
  "src/utils/axios",
  "src/hooks/useDashboards",
]);
const html =
  '<!doctype html><html><head><meta charset="utf-8"><title>Metric builder component test</title><style>html,body,#root{height:100%;margin:0}</style></head><body><div id="root"></div><script type="module" src="/src/sections/derived-metrics/__tests__/fixtures/builder-visual.jsx"></script></body></html>';
const server = await createServer({
  configFile: false,
  plugins: [
    {
      name: "f05-visual-fixtures",
      enforce: "pre",
      resolveId(source, importer) {
        if (
          sources.has(source) ||
          (importer?.includes("/sections/derived-metrics/") &&
            ["./api", "./useMetricsEnabled"].includes(source))
        )
          return mock;
        return null;
      },
      configureServer(vite) {
        vite.middlewares.use(async (req, res, next) => {
          if (!req.url?.startsWith("/dashboard/metrics/new")) return next();
          res.setHeader("Content-Type", "text/html");
          res.end(await vite.transformIndexHtml(req.url, html));
        });
      },
    },
    react(),
  ],
  resolve: {
    alias: [...sources]
      .map((find) => ({ find, replacement: mock }))
      .concat({ find: "src", replacement: resolve("src") }),
  },
  server: { host: "127.0.0.1", port: 4175, strictPort: true },
});
await server.listen();
const browser = await chromium.launch({
  headless: true,
  args: ["--no-sandbox"],
});
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
page.on("dialog", (dialog) => dialog.accept());
const root =
  "http://127.0.0.1:4175/dashboard/metrics/new?project=11111111-1111-4111-8111-111111111111";
try {
  await page.goto(root);
  await expect(page.getByRole("heading", { name: "New metric" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Project" })).toHaveValue(
    "Support assistant",
  );
  await page.screenshot({
    path: "/opt/regen/logs/f05-desktop-empty.png",
    fullPage: true,
  });
  await page.getByLabel("Metric name").fill("Resolution rate");
  await page.getByRole("combobox", { name: "Choose a source" }).click();
  await page.getByRole("option", { name: "Resolved", exact: true }).click();
  await expect(page.getByText("50%", { exact: true })).toBeVisible();
  await expect(page.getByText("1 of 2 conversations match")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Save metric", exact: true }),
  ).toBeEnabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "/opt/regen/logs/f05-desktop-preview.png",
    fullPage: true,
  });
  await page.getByRole("checkbox", { name: "Customize match labels" }).check();
  await page.getByLabel("Show matches as").fill("Resolved");
  await page.getByLabel("And the rest as").fill("Not resolved");
  await expect(
    page.getByRole("cell", { name: "Not resolved", exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.screenshot({
    path: "/opt/regen/logs/f05-desktop-labels.png",
    fullPage: true,
  });
  await page.goto(`${root}&unavailable=true`);
  await expect(
    page.getByText(
      "Preview unavailable. Your definition has not been changed.",
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Retry", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "/opt/regen/logs/f05-desktop-unavailable.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
  await writeFile(
    "/opt/regen/logs/f05-browser.json",
    JSON.stringify(
      {
        result: "passed",
        fixture:
          "synthetic component visual test, not backend runtime qualification",
        cases: 4,
        desktopWidths: [1440, 1280],
        pageErrors: errors,
      },
      null,
      2,
    ),
  );
} catch (error) {
  await page.screenshot({
    path: "/opt/regen/logs/f05-browser-failure.png",
    fullPage: true,
  });
  await writeFile(
    "/opt/regen/logs/f05-browser-failure.json",
    JSON.stringify(
      {
        message: error.message,
        pageErrors: errors,
        body: await page.locator("body").innerText(),
      },
      null,
      2,
    ),
  );
  throw error;
} finally {
  await browser.close();
  await server.close();
}
