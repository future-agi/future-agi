import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_AUTO_ASSIGN_SCREENSHOT ||
  "/tmp/annotation-auto-assign-edit-smoke.png";

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "annotation-auto-assign-edit-smoke.html",
  );
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1100, height: 620 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await page.goto(
      `${APP_BASE}/.tmp-smoke/annotation-auto-assign-edit-smoke.html`,
      { waitUntil: "domcontentloaded" },
    );
    await page.waitForFunction(() =>
      document.body.textContent.includes("All annotators"),
    );
    await clickByText(page, "All annotators");
    await page.waitForFunction(() => document.body.textContent.includes("Bob"));
    await clickByText(page, "Bob");
    await sleep(300);
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });
    await clickByText(page, "Apply");
    await page.waitForFunction(() => window.__assignmentPayload !== null);

    const evidence = await page.evaluate(() => window.__assignmentPayload);
    assert(
      evidence?.itemIds?.[0] === "item-1",
      `Wrong item assignment payload: ${JSON.stringify(evidence)}`,
    );
    assert(
      JSON.stringify(evidence.userIds) === JSON.stringify(["user-1"]),
      `Expected Bob to be removed from assignment: ${JSON.stringify(evidence)}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          screenshot: SCREENSHOT_PATH,
          evidence,
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
    <title>Annotation Auto Assign Edit Smoke</title>
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
        height: 420px;
        background: #fff;
        border: 1px solid #e5e7eb;
      }
    </style>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";
      import { AllCommunityModule, ModuleRegistry } from "ag-grid-community";
      import QueueItemsTable from "/src/sections/annotations/queues/items/queue-items-table.jsx";

      ModuleRegistry.registerModules([AllCommunityModule]);
      window.__assignmentPayload = null;

      createRoot(document.getElementById("root")).render(
        React.createElement(
          "div",
          { className: "surface" },
          React.createElement(QueueItemsTable, {
            data: [
              {
                id: "item-1",
                source_type: "dataset_row",
                source_preview: {
                  type: "dataset_row",
                  dataset_name: "Awesome Chatgpt Prompts",
                  row_order: 44,
                },
                status: "pending",
                assigned_users: [],
                review_status: null,
                created_at: new Date().toISOString(),
              },
            ],
            loading: false,
            page: 0,
            rowsPerPage: 10,
            totalCount: 1,
            selectedIds: new Set(),
            onSelectToggle: () => {},
            onSelectAll: () => {},
            onRemove: () => {},
            autoAssign: true,
            annotators: [
              {
                user_id: "user-1",
                name: "Alice",
                email: "alice@example.com",
                role: "annotator",
              },
              {
                user_id: "user-2",
                name: "Bob",
                email: "bob@example.com",
                role: "annotator",
              },
              {
                user_id: "user-3",
                name: "Reviewer",
                email: "reviewer@example.com",
                role: "reviewer",
              },
            ],
            onAssign: (payload) => {
              window.__assignmentPayload = payload;
            },
          }),
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
      document.querySelectorAll(
        "button, [role='button'], .MuiChip-root, li, span, p",
      ),
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

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  }
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  return "/usr/bin/google-chrome";
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
