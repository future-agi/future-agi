import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_DATASET_SEARCH_SCREENSHOT ||
  "/tmp/annotation-dataset-search-smoke.png";

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "annotation-dataset-search-smoke.html",
  );
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const datasetSearchRequests = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1120, height: 720 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.setRequestInterception(true);
  page.on("request", async (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/model-hub/develops/get-datasets-names/") {
      return request.respond(
        jsonResponse(200, {
          result: {
            datasets: [
              {
                datasetId: "dataset-1",
                id: "dataset-1",
                name: "Support Prompts",
              },
            ],
          },
        }),
      );
    }

    if (url.pathname === "/model-hub/develops/dataset-1/get-dataset-table/") {
      const searchPayload = url.searchParams.get("search");
      const searchKey = parseSearchKey(searchPayload);
      datasetSearchRequests.push(searchKey);
      await page
        .evaluate((key) => {
          window.__datasetSearchRequests = window.__datasetSearchRequests || [];
          window.__datasetSearchRequests.push(key);
        }, searchKey)
        .catch(() => null);
      return request.respond(
        jsonResponse(200, datasetTableResponse(searchKey)),
      );
    }

    return request.continue();
  });

  try {
    await page.goto(
      `${APP_BASE}/.tmp-smoke/annotation-dataset-search-smoke.html`,
      { waitUntil: "domcontentloaded" },
    );
    await page.waitForSelector('[role="combobox"]');
    await page.click('[role="combobox"]');
    await clickByText(page, "Support Prompts");
    await page.waitForSelector('input[placeholder="Search in dataset"]');
    await page.type('input[placeholder="Search in dataset"]', "refund");

    await page.waitForFunction(
      () => window.__datasetSearchRequests?.includes("refund"),
      { timeout: 15000 },
    );
    await page.waitForFunction(() =>
      document.body.textContent.includes("refund policy"),
    );
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    const evidence = await page.evaluate(() => window.__datasetSearchRequests);
    assert(
      evidence.includes("refund"),
      `Search request was not issued without Enter: ${JSON.stringify(evidence)}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          screenshot: SCREENSHOT_PATH,
          evidence: {
            dataset_search_requests: evidence,
          },
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

  function datasetTableResponse(searchKey) {
    const rows = searchKey
      ? [
          {
            row_id: "row-refund",
            rowId: "row-refund",
            prompt: { cellValue: "refund policy" },
          },
        ]
      : [
          {
            row_id: "row-welcome",
            rowId: "row-welcome",
            prompt: { cellValue: "welcome message" },
          },
        ];

    return {
      result: {
        columnConfig: [
          {
            id: "prompt",
            name: "Prompt",
            dataType: "text",
            isVisible: true,
          },
        ],
        table: rows,
        metadata: { total_rows: rows.length },
      },
    };
  }

  function parseSearchKey(searchPayload) {
    if (!searchPayload) return "";
    try {
      return JSON.parse(searchPayload)?.key || "";
    } catch {
      return "";
    }
  }
}

function smokeHarnessHtml() {
  return `<!doctype html>
<html>
  <head>
    <meta charset="UTF-8" />
    <title>Annotation Dataset Search Smoke</title>
    <style>
      body {
        margin: 0;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #f7f8fa;
      }
      #root {
        padding: 32px;
      }
      .surface {
        display: flex;
        flex-direction: column;
        height: 560px;
        background: #fff;
        border: 1px solid #dfe3e8;
      }
    </style>
    <script>
      window.__FUTURE_AGI_CONFIG__ = { VITE_HOST_API: window.location.origin };
      window.__datasetSearchRequests = [];
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";
      import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
      import { AllCommunityModule, ModuleRegistry } from "ag-grid-community";
      import {
        ServerSideRowModelApiModule,
        ServerSideRowModelModule,
      } from "ag-grid-enterprise";
      import { DatasetRowSelector } from "/src/sections/annotations/queues/items/add-items-dialog.jsx";

      ModuleRegistry.registerModules([
        AllCommunityModule,
        ServerSideRowModelModule,
        ServerSideRowModelApiModule,
      ]);

      const queryClient = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      });

      createRoot(document.getElementById("root")).render(
        React.createElement(
          QueryClientProvider,
          { client: queryClient },
          React.createElement(
            "div",
            { className: "surface" },
            React.createElement(DatasetRowSelector, {
              onSetSelection: () => {},
              onSelectAll: () => {},
            }),
          ),
        ),
      );
    </script>
  </body>
</html>
`;
}

async function clickByText(page, text) {
  const clicked = await page.evaluate((needle) => {
    const elements = Array.from(
      document.querySelectorAll("button, [role='button'], li, span, p"),
    );
    const target = elements.find(
      (element) => element.textContent?.trim() === needle,
    );
    if (!target) return false;
    target.click();
    return true;
  }, text);
  if (!clicked) throw new Error(`Could not find text: ${text}`);
}

function jsonResponse(status, body) {
  return {
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  };
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
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
  return "google-chrome";
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
