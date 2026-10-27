import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_LABEL_ARCHIVE_RESTORE_SCREENSHOT ||
  "/tmp/annotation-label-archive-restore-smoke.png";

const IDS = {
  archivedLabel: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
};

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "annotation-label-archive-restore-smoke.html",
  );
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const apiRequests = [];
  let restorePosted = false;
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1180, height: 720 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.includes("/model-hub/annotations-labels/")) {
      apiRequests.push({
        method: request.method(),
        pathname: url.pathname,
        query: url.search,
      });
    }
    if (
      url.pathname.endsWith("/model-hub/annotations-labels/") &&
      request.method() === "GET"
    ) {
      const archived = url.searchParams.get("archived") === "true";
      return request.respond(
        jsonResponse(
          200,
          archived ? archivedLabelsResponse() : activeLabelsResponse(),
        ),
      );
    }
    if (
      url.pathname.endsWith(
        `/model-hub/annotations-labels/${IDS.archivedLabel}/restore/`,
      )
    ) {
      restorePosted = true;
      return request.respond(
        jsonResponse(200, {
          status: true,
          result: {
            ...archivedLabelsResponse().results[0],
            archived: false,
          },
        }),
      );
    }
    return request.continue();
  });

  try {
    await page.goto(
      `${APP_BASE}/.tmp-smoke/annotation-label-archive-restore-smoke.html`,
      { waitUntil: "domcontentloaded" },
    );
    await clickByText(page, "button", "Archived");
    await waitForCondition(() =>
      apiRequests.some((entry) => entry.query.includes("archived=true")),
    );
    await page.waitForFunction(() =>
      document.body.textContent.includes("Archived accuracy"),
    );
    await clickByLabel(page, "Restore label actions");
    await page.waitForFunction(() =>
      Array.from(document.querySelectorAll('[role="menuitem"]')).some(
        (item) => item.textContent.trim() === "Restore",
      ),
    );
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });
    await clickByText(page, '[role="menuitem"]', "Restore");
    await waitForCondition(() => restorePosted);

    assert(
      apiRequests.some((entry) => entry.query.includes("archived=true")),
      `Archived label list request missing: ${JSON.stringify(apiRequests)}`,
    );
    assert(
      apiRequests.some(
        (entry) =>
          entry.method === "POST" &&
          entry.pathname.endsWith(
            `/model-hub/annotations-labels/${IDS.archivedLabel}/restore/`,
          ),
      ),
      `Restore request missing: ${JSON.stringify(apiRequests)}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          screenshot: SCREENSHOT_PATH,
          apiRequests,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await page
      .screenshot({
        path: SCREENSHOT_PATH.replace(/\.png$/, "-failure.png"),
        fullPage: true,
      })
      .catch(() => null);
    throw error;
  } finally {
    await browser.close();
    await rm(harnessPath, { force: true }).catch(() => null);
  }
}

function smokeHarnessHtml() {
  return `<!doctype html>
<html>
  <head>
    <meta charset="UTF-8" />
    <title>Annotation Label Archive Restore Smoke</title>
    <style>
      body {
        margin: 0;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #f7f8fa;
      }
      #root {
        height: 720px;
      }
    </style>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";
      import { BrowserRouter } from "react-router-dom";
      import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
      import {
        CellSelectionModule,
        ClipboardModule,
        MasterDetailModule,
        MenuModule,
        RichSelectModule,
        ServerSideRowModelApiModule,
        ServerSideRowModelModule,
        StatusBarModule,
      } from "ag-grid-enterprise";
      import { AllCommunityModule, ModuleRegistry } from "ag-grid-community";
      import AnnotationLabelsView from "/src/sections/annotations/labels/view/annotation-labels-view.jsx";

      ModuleRegistry.registerModules([
        AllCommunityModule,
        ServerSideRowModelModule,
        ServerSideRowModelApiModule,
        StatusBarModule,
        MasterDetailModule,
        RichSelectModule,
        MenuModule,
        ClipboardModule,
        CellSelectionModule,
      ]);

      const queryClient = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
        },
      });

      createRoot(document.getElementById("root")).render(
        React.createElement(
          QueryClientProvider,
          { client: queryClient },
          React.createElement(
            BrowserRouter,
            null,
            React.createElement(AnnotationLabelsView),
          ),
        ),
      );
    </script>
  </body>
</html>`;
}

function activeLabelsResponse() {
  return {
    count: 0,
    results: [],
  };
}

function archivedLabelsResponse() {
  return {
    count: 1,
    results: [
      {
        id: IDS.archivedLabel,
        name: "Archived accuracy",
        type: "categorical",
        settings: { options: [{ label: "Accurate" }, { label: "Wrong" }] },
        description: "Archived label that can be restored",
        allow_notes: true,
        created_at: "2026-05-14T06:55:33.127Z",
        annotation_count: 1,
        trace_annotations_count: 0,
        archived: true,
      },
    ],
  };
}

function jsonResponse(status, body) {
  return {
    status,
    headers: {
      "content-type": "application/json",
      "access-control-allow-origin": "*",
    },
    body: JSON.stringify(body),
  };
}

async function clickByText(page, selector, text) {
  const handles = await page.$$(selector);
  for (const handle of handles) {
    const matches = await handle.evaluate(
      (node, expected) => node.textContent.trim() === expected,
      text,
    );
    if (matches) {
      await handle.evaluate((node) => node.click());
      return;
    }
    await handle.dispose();
  }
  throw new Error(`Could not find ${selector} with text ${text}`);
}

async function clickByLabel(page, label) {
  const handle = await page.$(`[aria-label="${label}"]`);
  if (!handle) throw new Error(`Could not find control with label ${label}`);
  await handle.evaluate((node) => node.click());
}

async function waitForCondition(condition, timeoutMs = 5000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (condition()) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error("Timed out waiting for condition.");
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  }
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  if (process.platform === "linux") {
    return "/usr/bin/google-chrome";
  }
  return undefined;
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
