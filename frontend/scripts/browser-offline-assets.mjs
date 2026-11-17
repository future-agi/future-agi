// CLOUD ONLY. Synthetic API asset probe, not real-backend acceptance.
import process from "node:process";
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import { createRequire } from "node:module";
import { probeSurfaces, surfaceResponse } from "./browser-asset-surfaces.mjs";
const [dist, browserPackage, out, mode = "candidate"] = process.argv.slice(2);
let supplemental = false;
if (!["candidate", "baseline"].includes(mode))
  throw new Error("Unknown capture mode");
// Baseline alone may fetch its original public font/icon assets. Product/API
// hosts remain blocked. Never apply this allowance to the candidate proof.
const baselineAssetHosts = new Set([
  "fonts.googleapis.com",
  "fonts.gstatic.com",
  "api.iconify.design",
  "api.simplesvg.com",
  "api.unisvg.com",
]);
const { chromium, firefox } = createRequire(
  path.join(browserPackage, "package.json"),
)("playwright");
fs.mkdirSync(out, { recursive: true });
const datasetId = "11111111-1111-4111-8111-111111111111";
const name = "Offline asset fixture";
const dataset = {
  id: datasetId,
  dataset_id: datasetId,
  datasetId,
  name,
  kind: "rows",
  dataset_type: "custom",
  model_type: "llm",
  number_of_datapoints: 2,
  number_of_experiments: 0,
  created_at: "2026-10-01T00:00:00Z",
  created_by: "Asset fixture",
};
const column = {
  id: "input",
  column_id: "input",
  name: "Input",
  field: "input",
  column_name: "Input",
  column_type: "text",
  data_type: "text",
  type: "text",
  width: 240,
  hidden: false,
  is_visible: true,
  origin_type: "OTHERS",
  output_type: "text",
  status: "Completed",
  metadata: {},
};
const org = {
  id: "asset-org",
  name: "asset-org",
  display_name: "Asset fixture",
};
const workspace = {
  id: "asset-workspace",
  name: "asset-workspace",
  display_name: "Asset fixture",
};
function responseFor(url) {
  const extra = supplemental ? surfaceResponse(url) : null;
  if (extra) return extra;
  const p = url.pathname;
  if (p.includes("/accounts/user-info"))
    return {
      id: "asset-user",
      email: "fixture@example.invalid",
      name: "Asset fixture",
      first_name: "Asset",
      last_name: "Fixture",
      organization: org,
      organization_role: supplemental ? "Owner" : "owner",
      org_level: 100,
      default_workspace_id: workspace.id,
      default_workspace_name: workspace.name,
      default_workspace_display_name: workspace.display_name,
      default_workspace_role: supplemental ? "workspace_admin" : "owner",
      ws_level: 100,
      is_onboarded: true,
      onboarding_completed: true,
    };
  if (p.includes("get-datasets") || p.includes("datasets/query"))
    return {
      status: true,
      result: { datasets: [dataset], total_count: 1, total_pages: 1 },
    };
  if (p.includes("get-dataset-table"))
    return {
      status: true,
      result: {
        table: [
          {
            row_id: "row-1",
            order: 0,
            input: {
              cell_value: "Local font and icon check",
              status: "Completed",
            },
          },
          {
            row_id: "row-2",
            order: 1,
            input: {
              cell_value: "Second populated asset row",
              status: "Completed",
            },
          },
        ],
        metadata: { total_rows: 2 },
        column_config: [column],
        columnConfig: [column],
        total_count: 2,
        total_rows: 2,
        total_pages: 1,
        dataset_config: { name, is_sdk: false },
        dataset_name: name,
      },
    };
  if (p.includes("/dataset/columns/"))
    return { status: true, result: { columns: [column] } };
  if (p.includes("organization"))
    return { status: true, result: { organizations: [org] } };
  if (p.includes("workspace"))
    return { status: true, result: { workspaces: [workspace] } };
  if (p.includes("permissions"))
    return { status: true, result: { permissions: [], role: "owner" } };
  if (p.includes("json-schema")) return { status: true, result: {} };
  if (p.includes("pricing") || p.includes("subscription"))
    return { status: true, result: { plan: "enterprise" } };
  return { status: true, result: [], results: [], count: 0 };
}
const mime = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".woff2": "font/woff2",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".ico": "image/x-icon",
};
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, "http://127.0.0.1").pathname;
  if (pathname === "/config.js") {
    res.setHeader("Content-Type", "text/javascript");
    res.end(
      'window.__FUTURE_AGI_CONFIG__={VITE_HOST_API:"http://127.0.0.1:8999"};',
    );
    return;
  }
  let file = path.resolve(dist, `.${decodeURIComponent(pathname)}`);
  if (!file.startsWith(path.resolve(dist) + path.sep))
    file = path.join(dist, "index.html");
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory())
    file = path.join(dist, "index.html");
  res.setHeader(
    "Content-Type",
    mime[path.extname(file)] || "application/octet-stream",
  );
  res.end(fs.readFileSync(file));
});
await new Promise((resolve) => server.listen(5188, "127.0.0.1", resolve));
const routes = [
  "/dashboard/develop",
  `/dashboard/develop/${datasetId}?tab=data`,
  "/dashboard/develop/r4-lab",
  "/dashboard/huggingface",
  "/dashboard/develop/leaderboards",
  `/dashboard/develop/${datasetId}/compare`,
  `/dashboard/develop/${datasetId}?tab=summary`,
  `/dashboard/develop/experiment/${datasetId}/data`,
  `/dashboard/develop/individual-experiment/${datasetId}/data`,
  ...["matrix", "paths", "summary", "traces"].map(
    (tab) => `/dashboard/develop/experiment/${datasetId}/${tab}`,
  ),
];
const results = [];
try {
  for (const [browserName, engine] of Object.entries({ chromium, firefox })) {
    const browser = await engine.launch();
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      serviceWorkers: "block",
    });
    const requests = [];
    const external = [];
    const pageErrors = [];
    let currentRoute;
    await context.route("**/*", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      const entry = {
        route: currentRoute,
        host: url.host,
        url: req.url(),
        type: req.resourceType(),
      };
      requests.push(entry);
      if (!["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)) {
        external.push(entry);
        if (mode === "baseline" && baselineAssetHosts.has(url.hostname)) {
          await route.continue();
          return;
        }
        await route.abort("blockedbyclient");
        return;
      }
      if (url.port === "8999") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          headers: {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
          },
          body: JSON.stringify(responseFor(url)),
        });
        return;
      }
      await route.continue();
    });
    await context.addInitScript(() => {
      if (
        location.hostname !== "127.0.0.1" ||
        location.pathname.startsWith("/auth/")
      )
        return;
      localStorage.setItem("accessToken", "asset-fixture-not-a-credential");
      localStorage.setItem("initial-render", "done");
      localStorage.setItem("settings", JSON.stringify({ themeMode: "light" }));
      for (const [key, value] of Object.entries({
        organizationId: "asset-org",
        organizationName: "asset-org",
        organizationDisplayName: "Asset fixture",
        organizationRole: "owner",
        orgLevel: "100",
        workspaceId: "asset-workspace",
        workspaceName: "asset-workspace",
        workspaceDisplayName: "Asset fixture",
        workspaceRole: "owner",
        wsLevel: "100",
        workspaceOrgId: "asset-org",
      }))
        sessionStorage.setItem(key, value);
    });
    const page = await context.newPage();
    page.on("pageerror", (error) =>
      pageErrors.push({ route: currentRoute, message: error.message }),
    );
    for (const route of routes) {
      currentRoute = route;
      const row = { browser: browserName, route };
      try {
        await page.goto(`http://127.0.0.1:5188${route}`, {
          waitUntil: "networkidle",
          timeout: 45000,
        });
        await page.evaluate(() => document.fonts.ready);
        await page.addStyleTag({
          content:
            "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}",
        });
        if (route === `/dashboard/develop/${datasetId}?tab=data`) {
          await page.waitForFunction(
            () =>
              [...document.querySelectorAll('.ag-cell[col-id="input"]')].some(
                (cell) =>
                  cell.textContent.includes("Second populated asset row"),
              ),
            null,
            { timeout: 15000 },
          );
        }
        await page.waitForTimeout(1200);
        row.render = await page.evaluate(() => ({
          text: document.body.innerText.slice(0, 1800),
          font: getComputedStyle(document.body).fontFamily,
          interLoaded: document.fonts.check("400 16px Inter"),
          svgCount: document.querySelectorAll(".iconify, .component-iconify")
            .length,
          missing: [...document.querySelectorAll("[data-missing-icon]")].map(
            (n) => n.getAttribute("data-missing-icon"),
          ),
          grids: document.querySelectorAll("[role=grid]").length,
          gridValues: [
            ...document.querySelectorAll(
              '.ag-center-cols-container .ag-cell[col-id="input"]',
            ),
          ].map((cell) => cell.textContent.trim()),
          iconGeometry: [...document.querySelectorAll("svg.iconify")].map(
            (svg) => ({
              viewBox: svg.getAttribute("viewBox"),
              paths: [...svg.querySelectorAll("path")].map((p) =>
                p.getAttribute("d"),
              ),
            }),
          ),
          cellTypography: [
            ...document.querySelectorAll(
              '.ag-center-cols-container .ag-cell[col-id="input"]',
            ),
          ].map((cell) => {
            const s = getComputedStyle(cell);
            const r = cell.getBoundingClientRect();
            return {
              text: cell.textContent.trim(),
              font: s.fontFamily,
              weight: s.fontWeight,
              size: s.fontSize,
              width: r.width,
              height: r.height,
            };
          }),
          fontFaces: [...document.fonts]
            .filter((f) => f.status === "loaded")
            .map((f) => ({
              family: f.family,
              weight: f.weight,
              style: f.style,
            })),
        }));
        row.finalUrl = page.url();
        row.pass =
          row.render.text.includes("Offline asset fixture") &&
          row.render.interLoaded &&
          row.render.svgCount > 0 &&
          row.render.missing.length === 0;
        if (route === `/dashboard/develop/${datasetId}?tab=data`)
          row.pass &&=
            row.render.grids > 0 &&
            ["Local font and icon check", "Second populated asset row"].every(
              (text) => row.render.gridValues.includes(text),
            );
        row.screenshot = `${browserName}-${results.length}.png`;
        await page.screenshot({
          path: path.join(out, row.screenshot),
          fullPage: false,
          animations: "disabled",
        });
      } catch (error) {
        row.pass = false;
        row.error = error.message;
        row.finalUrl = page.url();
        row.diagnosticText = await page
          .locator("body")
          .innerText()
          .catch(() => "");
        row.screenshot = `${browserName}-${results.length}-failed.png`;
        await page
          .screenshot({ path: path.join(out, row.screenshot) })
          .catch(() => {});
      }
      results.push(row);
    }
    fs.writeFileSync(
      path.join(out, `${browserName}-requests.json`),
      JSON.stringify(
        {
          hosts: [...new Set(requests.map((r) => r.host))].sort(),
          external,
          requests,
          pageErrors,
        },
        null,
        2,
      ),
    );
    results.push({
      browser: browserName,
      check: "no external font/icon requests",
      pass: external.length === 0 && pageErrors.length === 0,
      external,
      pageErrors,
    });
    if (mode === "candidate") {
      supplemental = true;
      results.push(await probeSurfaces(page, browserName, out, dist));
      supplemental = false;
    }
    await browser.close();
  }
} finally {
  await new Promise((resolve) => server.close(resolve));
}
fs.writeFileSync(
  path.join(out, "summary.json"),
  JSON.stringify(
    {
      purpose:
        "Asset-only production-bundle walk with synthetic localhost API responses, NOT real-backend acceptance",
      results,
    },
    null,
    2,
  ),
);
console.log(
  JSON.stringify(
    results.map(({ render, ...r }) => r),
    null,
    2,
  ),
);
if (results.some((r) => !r.pass)) process.exitCode = 1;
