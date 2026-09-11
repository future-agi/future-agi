import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_QUEUE_DISABLED_SCREENSHOT ||
  "/tmp/annotation-queue-disabled-smoke.png";

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "annotation-queue-disabled-smoke.html",
  );
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 760, height: 420 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await page.goto(
      `${APP_BASE}/.tmp-smoke/annotation-queue-disabled-smoke.html`,
      { waitUntil: "domcontentloaded" },
    );
    await page.waitForSelector("button");
    await page.click("button");
    await page.waitForSelector('[role="menuitem"][aria-disabled="true"]');

    const evidence = await page.evaluate(() => {
      const item = document.querySelector(
        '[role="menuitem"][aria-disabled="true"]',
      );
      return {
        disabledText: item?.textContent?.trim() || "",
        ariaDisabled: item?.getAttribute("aria-disabled"),
        actions: window.__bulkActions || [],
      };
    });

    assert(
      evidence.disabledText.includes("Add to annotation queue"),
      `Disabled action was not rendered: ${JSON.stringify(evidence)}`,
    );
    assert(
      evidence.ariaDisabled === "true",
      `Annotation queue action was not disabled: ${JSON.stringify(evidence)}`,
    );

    await page
      .click('[role="menuitem"][aria-disabled="true"]')
      .catch(() => null);
    const actionCount = await page.evaluate(
      () => (window.__bulkActions || []).length,
    );
    assert(actionCount === 0, "Disabled action fired an onAction callback.");
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

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
    <title>Annotation Queue Disabled Smoke</title>
    <style>
      body {
        margin: 0;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #f7f8fa;
      }
      #root {
        padding: 48px;
      }
    </style>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";
      import BulkActionsBar from "/src/sections/projects/LLMTracing/BulkActionsBar.jsx";

      window.__bulkActions = [];

      createRoot(document.getElementById("root")).render(
        React.createElement(BulkActionsBar, {
          selectedCount: 3,
          onClearSelection: () => {},
          onAction: (id) => window.__bulkActions.push(id),
          actions: [
            {
              id: "annotation-queue",
              label: "Add to annotation queue",
              icon: "mdi:clipboard-list-outline",
              disabled: true,
              disabledReason: "Selected calls are still in progress.",
            },
          ],
        }),
      );
    </script>
  </body>
</html>
`;
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
  return "/usr/bin/google-chrome";
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
