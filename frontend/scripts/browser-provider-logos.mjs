// CLOUD ONLY. Provider-logo proof in Chromium and Firefox on the real AI
// Providers and gateway Models pages of a production build, with loopback API
// fixtures. The provider-status fixture is the backend's own response, saved by
// futureagi/model_hub/tests/test_provider_logo_urls.py (PROVIDER_STATUS_EXPORT).
//
// Every browser request is recorded per event. Requests to any other host are
// aborted in the browser before they reach the network, and the proof fails if
// any request to the provider-logo bucket was attempted, if a page drew a
// provider with artwork other than the path the backend names, or if a logo
// did not visibly render.
//
// Usage: node browser-provider-logos.mjs <dist> <e2e dir> <provider-status.json> <out>
import assert from "node:assert/strict";
import process from "node:process";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { createRequire } from "node:module";
import {
  DEMO_CATALOG,
  DEMO_PROVIDERS,
} from "../src/sections/gateway/models/test-fixtures/demoCatalog.js";

const LOGO_BUCKET = "fi-image-assets.s3.ap-south-1.amazonaws.com";
const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);
const PORT = 5188;
const ORIGIN = `http://127.0.0.1:${PORT}`;

const [dist, browserPackage, statusFile, out] = process.argv.slice(2);
const providers = JSON.parse(fs.readFileSync(statusFile, "utf8"));
assert(Array.isArray(providers) && providers.length > 0, "provider-status");
const { chromium, firefox } = createRequire(
  path.join(browserPackage, "package.json"),
)("playwright");
fs.mkdirSync(out, { recursive: true });

const org = { id: "logo-org", name: "logo-org", display_name: "Logo fixture" };
const ws = {
  id: "logo-workspace",
  name: "logo-workspace",
  display_name: "Logo fixture",
};
function api(url) {
  const p = url.pathname;
  if (p.includes("/accounts/user-info"))
    return {
      id: "logo-user",
      email: "logo@example.invalid",
      name: "Logo fixture",
      first_name: "Logo",
      last_name: "Fixture",
      organization: org,
      organization_role: "Owner",
      org_level: 100,
      default_workspace_id: ws.id,
      default_workspace_name: ws.name,
      default_workspace_display_name: ws.display_name,
      default_workspace_role: "workspace_admin",
      ws_level: 100,
      is_onboarded: true,
      onboarding_completed: true,
    };
  let result = [];
  if (p.includes("provider-status")) result = { providers };
  else if (p.includes("/managed-models/catalog/")) result = DEMO_CATALOG;
  else if (p.includes("/managed-models/"))
    result = {
      available: true,
      enabled: true,
      balance_usd: 10,
      markup_percent: 5,
    };
  else if (p.endsWith("/agentcc/gateways/"))
    result = [
      { id: "local", name: "Local", base_url: "http://127.0.0.1:8999" },
    ];
  else if (p.includes("/gateways/local/providers/")) result = DEMO_PROVIDERS;
  else if (p.includes("organization")) result = { organizations: [org] };
  else if (p.includes("workspace")) result = { workspaces: [ws] };
  else if (p.includes("permissions"))
    result = { permissions: [], role: "owner" };
  else if (p.includes("pricing") || p.includes("subscription"))
    result = { plan: "enterprise" };
  return { status: true, result, results: [], count: 0 };
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
  ".jpeg": "image/jpeg",
  ".ico": "image/x-icon",
};
// A static server for the build, the way nginx serves frontend/dist.
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, ORIGIN).pathname;
  if (pathname === "/config.js") {
    res.setHeader("Content-Type", "text/javascript");
    return res.end(
      'window.__FUTURE_AGI_CONFIG__={VITE_HOST_API:"http://127.0.0.1:8999"};',
    );
  }
  let file = path.resolve(dist, `.${decodeURIComponent(pathname)}`);
  if (!file.startsWith(path.resolve(dist) + path.sep)) {
    res.writeHead(403);
    return res.end();
  }
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    if (path.extname(pathname)) {
      res.writeHead(404);
      return res.end();
    }
    file = path.join(dist, "index.html");
  }
  res.setHeader(
    "Content-Type",
    mime[path.extname(file).toLowerCase()] || "application/octet-stream",
  );
  res.end(fs.readFileSync(file));
});
await new Promise((resolve) => server.listen(PORT, "127.0.0.1", resolve));

// The Image component lazy-loads: a logo's <img> exists only once its card has
// been in view. Scroll every scrollable element through, a viewport at a time.
async function materialize(page) {
  for (let step = 0; step < 60; step++) {
    const moved = await page.evaluate(() => {
      let any = false;
      const els = [
        document.scrollingElement,
        ...document.querySelectorAll("*"),
      ];
      for (const el of els) {
        if (!el || el.scrollHeight <= el.clientHeight + 4) continue;
        const style =
          el === document.scrollingElement ? null : getComputedStyle(el);
        if (style && !/(auto|scroll)/.test(style.overflowY)) continue;
        const before = el.scrollTop;
        el.scrollTop = before + Math.max(200, el.clientHeight * 0.8);
        if (el.scrollTop !== before) any = true;
      }
      return any;
    });
    await page.waitForTimeout(150);
    if (!moved) break;
  }
}

// Wait until an <img> has finished loading or failed, then describe it.
async function describeImage(locator) {
  await locator.scrollIntoViewIfNeeded({ timeout: 10000 });
  await locator
    .evaluate(
      (img) =>
        new Promise((resolve) => {
          const done = () => resolve();
          if (img.complete && img.getAttribute("src")) return done();
          img.addEventListener("load", done, { once: true });
          img.addEventListener("error", done, { once: true });
          setTimeout(done, 10000);
        }),
    )
    .catch(() => {});
  return locator.evaluate((img) => {
    const r = img.getBoundingClientRect();
    return {
      src: img.getAttribute("src"),
      alt: img.getAttribute("alt"),
      loaded: img.complete && img.naturalWidth > 0,
      visible:
        r.width > 0 &&
        r.height > 0 &&
        r.bottom > 0 &&
        r.top < window.innerHeight &&
        getComputedStyle(img).visibility !== "hidden",
    };
  });
}

const results = [];
const summary = { bucketAttempts: 0, externalAttempts: 0 };
try {
  for (const [engineName, engine] of Object.entries({ chromium, firefox })) {
    const browser = await engine.launch();
    const ledger = path.join(out, `${engineName}-requests.jsonl`);
    try {
      const context = await browser.newContext({
        viewport: { width: 1440, height: 1000 },
        serviceWorkers: "block",
      });
      const requests = [];
      const errors = [];
      let phase = "guard-control";
      let seq = 0;
      context.on("request", (request) => {
        const u = new URL(request.url());
        const row = {
          seq: ++seq,
          time: new Date().toISOString(),
          browser: engineName,
          phase,
          method: request.method(),
          host: u.hostname,
          port: u.port || (u.protocol === "https:" ? "443" : "80"),
          path: u.pathname,
          type: request.resourceType(),
          external: !LOCAL_HOSTS.has(u.hostname),
          logoBucket: u.hostname === LOGO_BUCKET,
        };
        requests.push(row);
        fs.appendFileSync(ledger, JSON.stringify(row) + "\n");
      });
      await context.route("**/*", async (route) => {
        const url = new URL(route.request().url());
        if (!LOCAL_HOSTS.has(url.hostname))
          return route.abort("blockedbyclient");
        if (url.port === "8999")
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            headers: {
              "Access-Control-Allow-Origin": "*",
              "Access-Control-Allow-Headers": "*",
            },
            body: JSON.stringify(api(url)),
          });
        return route.continue();
      });
      await context.addInitScript(() => {
        if (
          location.hostname !== "127.0.0.1" ||
          location.pathname.startsWith("/auth/")
        )
          return;
        localStorage.setItem("accessToken", "logo-fixture-not-a-credential");
        localStorage.setItem("initial-render", "done");
        localStorage.setItem(
          "settings",
          JSON.stringify({ themeMode: "light" }),
        );
        for (const [key, value] of Object.entries({
          organizationId: "logo-org",
          organizationName: "logo-org",
          organizationDisplayName: "Logo fixture",
          organizationRole: "Owner",
          orgLevel: "100",
          workspaceId: "logo-workspace",
          workspaceName: "logo-workspace",
          workspaceDisplayName: "Logo fixture",
          workspaceRole: "workspace_admin",
          wsLevel: "100",
          workspaceOrgId: "logo-org",
        }))
          sessionStorage.setItem(key, value);
      });

      // Negative control: the recorder sees, and the route refuses, a request
      // to a non-local host.
      const controlPage = await context.newPage();
      await controlPage.goto("https://guard-control.invalid/logo.png").then(
        () => {
          throw new Error("Guard control escaped");
        },
        () => {},
      );
      assert.equal(
        requests.filter((r) => r.phase === "guard-control" && r.external)
          .length,
        1,
        "guard control must be recorded once",
      );
      await controlPage.close();

      const page = await context.newPage();
      page.on("pageerror", (e) => errors.push({ phase, message: e.message }));
      const finish = async (row) => {
        const own = requests.filter((r) => r.phase === row.phase);
        row.requests = own.length;
        row.external = own.filter((r) => r.external);
        row.bucketAttempts = own
          .filter((r) => r.logoBucket)
          .map((r) => ({ seq: r.seq, port: r.port, path: r.path }));
        row.localLogoRequests = own.filter(
          (r) =>
            !r.external &&
            (r.path.startsWith("/assets/provider-logos/") ||
              r.path.startsWith("/icons/settings/")),
        ).length;
        row.errors = errors.filter((e) => e.phase === row.phase);
        summary.bucketAttempts += row.bucketAttempts.length;
        summary.externalAttempts += row.external.length;
        row.finalUrl = page.url();
        await page.screenshot({
          path: path.join(out, `${engineName}-${row.phase}.png`),
          fullPage: true,
        });
        results.push(row);
      };
      const assertQuiet = (row) => {
        const own = requests.filter((r) => r.phase === row.phase);
        assert.deepEqual(
          own.filter((r) => r.logoBucket).map((r) => r.path),
          [],
          `requests to ${LOGO_BUCKET}`,
        );
        assert.deepEqual(
          own.filter((r) => r.external).map((r) => `${r.host}${r.path}`),
          [],
          "requests to non-local hosts",
        );
        assert.deepEqual(
          errors.filter((e) => e.phase === row.phase).map((e) => e.message),
          [],
          "page errors",
        );
      };

      // 1. AI Providers: every provider the backend names a logo for is drawn
      // with exactly that artwork, from this origin, visibly.
      phase = "providers";
      {
        const row = { browser: engineName, phase, pass: false };
        try {
          await page.goto(`${ORIGIN}/dashboard/settings/ai-providers`, {
            waitUntil: "networkidle",
            timeout: 45000,
          });
          await materialize(page);
          row.providers = [];
          for (const p of providers.filter((x) => x.logo_url)) {
            const img = page.locator(`img[alt="${p.display_name}"]`).first();
            const seen = await describeImage(img).catch((e) => ({
              error: e.message.split("\n")[0],
            }));
            row.providers.push({
              provider: p.provider,
              backend: p.logo_url,
              ...seen,
            });
          }
          const wrong = row.providers.filter(
            (x) => x.src !== x.backend || !x.loaded || !x.visible,
          );
          assert.deepEqual(
            wrong.map(
              (x) => `${x.provider}: drew ${x.src}, backend ${x.backend}`,
            ),
            [],
            "each provider must visibly render the backend's same-origin artwork",
          );
          assertQuiet(row);
          row.pass = true;
        } catch (e) {
          row.error = e.message;
        }
        await finish(row);
      }

      // 2. Gateway models: the catalog's vendor logos come from the same
      // provider-status response.
      phase = "models";
      {
        const row = { browser: engineName, phase, pass: false };
        try {
          await page.goto(`${ORIGIN}/dashboard/gateway/models`, {
            waitUntil: "networkidle",
            timeout: 45000,
          });
          const openai = providers.find((p) => p.provider === "openai");
          const img = page.locator(`img[src="${openai.logo_url}"]`).first();
          row.openai = await img
            .waitFor({ state: "attached", timeout: 15000 })
            .then(() => describeImage(img))
            .catch(() => null);
          row.images = await page.locator("img").evaluateAll((nodes) =>
            nodes.map((n) => ({
              src: n.getAttribute("src"),
              alt: n.getAttribute("alt"),
              loaded: n.complete && n.naturalWidth > 0,
            })),
          );
          assert(
            row.openai?.loaded && row.openai?.visible,
            `the OpenAI vendor logo must visibly render from ${openai.logo_url}`,
          );
          assertQuiet(row);
          row.pass = true;
        } catch (e) {
          row.error = e.message;
        }
        await finish(row);
      }

      // 3. A client that draws logo_url verbatim (an older UI build, the
      // SDK's users): every returned value must load from this origin and
      // never from the bucket.
      phase = "returned-logo-display";
      {
        const row = { browser: engineName, phase, pass: false };
        try {
          await page.goto(`${ORIGIN}/dashboard/settings/ai-providers`, {
            waitUntil: "networkidle",
            timeout: 45000,
          });
          await page.evaluate(
            (rows) => {
              const box = document.createElement("div");
              box.id = "returned-logos";
              box.style.cssText =
                "position:fixed;inset:0;z-index:99999;background:#fff;display:flex;flex-wrap:wrap;gap:8px;padding:8px;overflow:auto";
              for (const r of rows) {
                const img = document.createElement("img");
                img.src = r.logo_url;
                img.alt = `returned:${r.provider}`;
                img.width = 40;
                img.height = 40;
                box.append(img);
              }
              document.body.append(box);
            },
            providers.filter((p) => p.logo_url),
          );
          row.providers = [];
          for (const p of providers.filter((x) => x.logo_url)) {
            const img = page.locator(`img[alt="returned:${p.provider}"]`);
            row.providers.push({
              provider: p.provider,
              ...(await describeImage(img)),
            });
          }
          assertQuiet(row);
          const broken = row.providers.filter((x) => !x.loaded || !x.visible);
          assert.deepEqual(
            broken.map((x) => `${x.provider}: ${x.src}`),
            [],
            "every returned logo must decode and show",
          );
          row.pass = true;
        } catch (e) {
          row.error = e.message;
        }
        await finish(row);
      }
      await context.close();
    } finally {
      await browser.close();
    }
  }
} finally {
  await new Promise((resolve) => server.close(resolve));
}
fs.writeFileSync(
  path.join(out, "results.json"),
  JSON.stringify(
    {
      kind: "real-pages-backend-provider-status",
      logoBucket: LOGO_BUCKET,
      summary,
      results,
    },
    null,
    2,
  ) + "\n",
);
console.log(
  `PROVIDER-LOGOS ${results.map((r) => `${r.browser}/${r.phase}=${r.pass ? "PASS" : "FAIL"}`).join(" ")} bucketAttempts=${summary.bucketAttempts} externalAttempts=${summary.externalAttempts}`,
);
process.exitCode = results.every((r) => r.pass) ? 0 : 1;
