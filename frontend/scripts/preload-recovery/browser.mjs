/* eslint-env node */
// Frontend-only deployment regression. Run against an unmodified production
// Vite output: node scripts/preload-recovery/browser.mjs <dist> <out> <pw-dir> [pdf-dist].
// Only the deployment-mode read is stubbed; routes and lazy chunks are real.
// Optional pdf-dist adds an isolated production-built real PdfPreview fixture.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import { createRequire } from "node:module";
import { pdfScenarios } from "./pdf-browser.mjs";
const require = createRequire(
  path.resolve(process.argv[4] || "../e2e", "package.json"),
);
const { chromium, firefox } = require("playwright");
const root = path.resolve(process.argv[2]);
const out = path.resolve(process.argv[3]);
const pdfRoot = process.argv[5] ? path.resolve(process.argv[5]) : null;
fs.mkdirSync(out, { recursive: true });
const manifest = JSON.parse(
  fs.readFileSync(path.join(root, ".vite/manifest.json"), "utf8"),
);
const login = manifest["src/pages/auth/jwt/login.jsx"]?.file;
assert(login, "production manifest must identify the actual lazy login page");
const mime = {
  ".js": "text/javascript",
  ".css": "text/css",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".woff2": "font/woff2",
  ".ico": "image/x-icon",
  ".html": "text/html",
};
const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");
  if (url.pathname === "/config.js") {
    res.setHeader("Content-Type", "text/javascript");
    res.end("window.__FUTURE_AGI_CONFIG__={VITE_HOST_API:location.origin};");
    return;
  }
  if (url.pathname === "/api/deployment-info/") {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ result: { mode: "cloud" } }));
    return;
  }
  const isPdf = pdfRoot && url.pathname.startsWith("/preload-pdf/");
  const servingRoot = isPdf ? pdfRoot : root;
  const pathname = isPdf
    ? url.pathname.slice("/preload-pdf".length)
    : url.pathname;
  const file = path.resolve(servingRoot, `.${pathname}`);
  if (!file.startsWith(`${servingRoot}/`)) {
    res.writeHead(403).end();
    return;
  }
  const actual =
    fs.existsSync(file) && fs.statSync(file).isFile()
      ? file
      : path.extname(file)
        ? null
        : path.join(
            servingRoot,
            isPdf ? "scripts/preload-recovery/pdf-fixture.html" : "index.html",
          );
  if (!actual) {
    res.writeHead(404).end();
    return;
  }
  res.setHeader("Cache-Control", "no-store");
  res.setHeader(
    "Content-Type",
    mime[path.extname(actual)] || "application/octet-stream",
  );
  fs.createReadStream(actual).pipe(res);
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const results = [];
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

async function scenario(browserType, name, run) {
  const browser = await browserType.launch({ headless: true });
  const context = await browser.newContext({ serviceWorkers: "block" });
  const page = await context.newPage();
  const record = {
    browser: browserType.name(),
    scenario: name,
    documents: [],
    console: [],
    failures: [],
    blocked: [],
    status: "FAIL",
  };
  page.setDefaultTimeout(20000);
  page.setDefaultNavigationTimeout(20000);
  await context.route("**/*", (route) => {
    if (new URL(route.request().url()).origin !== origin) {
      record.blocked.push(route.request().url());
      return route.abort("blockedbyclient");
    }
    return route.continue();
  });
  page.on("request", (request) => {
    if (request.isNavigationRequest() && request.frame() === page.mainFrame())
      record.documents.push(request.url());
  });
  page.on("requestfailed", (request) =>
    record.failures.push({
      url: request.url(),
      error: request.failure()?.errorText,
    }),
  );
  page.on("console", (message) => {
    if (message.type() === "error") record.console.push(message.text());
  });
  page.on("pageerror", (error) => record.console.push(error.message));
  try {
    await run(page, record);
    record.status = "PASS";
  } catch (error) {
    record.error = error.stack;
  } finally {
    record.finalUrl = page.url();
    await page
      .screenshot({ path: path.join(out, `${record.browser}-${name}.png`) })
      .catch(() => {});
    await context.close();
    await browser.close();
    results.push(record);
    fs.writeFileSync(
      path.join(out, "results.json"),
      JSON.stringify(results, null, 2),
    );
    console.log(JSON.stringify(record));
  }
}

for (const browserType of [chromium, firefox]) {
  await scenario(browserType, "navigate-away", async (page, record) => {
    const held = deferred();
    const abort = deferred();
    await page.route(`${origin}/${login}`, async (route) => {
      held.resolve();
      await abort.promise;
      await route.abort("aborted").catch(() => {});
    });
    await page.goto(`${origin}/auth/jwt/login`, {
      waitUntil: "domcontentloaded",
    });
    await Promise.race([
      held.promise,
      delay(20000).then(() => {
        throw new Error("lazy login request was not held");
      }),
    ]);
    // Hold the new document before commit: the old page still has route A's URL.
    // Aborting its import here recreates the Firefox NS_BINDING_ABORTED race.
    await page.route(`${origin}/404`, async (route) => {
      abort.resolve();
      await delay(300);
      await route.continue().catch(() => {});
    });
    await page.goto(`${origin}/404`, { waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Page not found", exact: true }).waitFor();
    await delay(7000); // greater than the default 1s + 2s + 3s import retry delays
    assert.equal(page.url(), `${origin}/404`);
    assert.deepEqual(record.documents, [
      `${origin}/auth/jwt/login`,
      `${origin}/404`,
    ]);
    assert(
      record.failures.some((item) => item.url === `${origin}/${login}`),
      "the original lazy request must actually fail",
    );
  });

  await scenario(
    browserType,
    "current-route-recovers",
    async (page, record) => {
      await page.route(`${origin}/${login}`, (route) =>
        record.documents.length === 1
          ? route.fulfill({
              status: 404,
              contentType: "text/javascript",
              body: "",
            })
          : route.continue(),
      );
      await page.goto(`${origin}/auth/jwt/login`, {
        waitUntil: "domcontentloaded",
      });
      await page
        .getByRole("button", { name: "Continue", exact: true })
        .waitFor({ timeout: 30000 });
      await delay(4500);
      assert.equal(page.url(), `${origin}/auth/jwt/login`);
      assert.equal(
        record.documents.length,
        2,
        "one recovery reload and then the real login page",
      );
    },
  );

  await scenario(
    browserType,
    "persistent-404-bounded",
    async (page, record) => {
      await page.route(`${origin}/${login}`, (route) =>
        route.fulfill({
          status: 404,
          contentType: "text/javascript",
          body: "",
        }),
      );
      await page.goto(`${origin}/auth/jwt/login`, {
        waitUntil: "domcontentloaded",
      });
      await page.waitForFunction(
        () => document.title === "Something went wrong",
        null,
        { timeout: 30000 },
      );
      await delay(4500);
      assert.equal(
        record.documents.length,
        2,
        "persistent missing chunk must not cause a reload loop",
      );
      assert.equal(page.url(), `${origin}/auth/jwt/login`);
      assert.equal(
        await page.evaluate(() =>
          Boolean(window.__RELOADING_FOR_VITE_PRELOAD_ERROR),
        ),
        false,
      );
    },
  );
  if (pdfRoot) await pdfScenarios(browserType, scenario, origin, pdfRoot);
}
server.close();
if (results.some((result) => result.status !== "PASS")) process.exitCode = 1;
