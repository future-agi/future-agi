import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_ADD_ITEMS_NUDGE_SCREENSHOT ||
  "/tmp/annotation-add-items-checkbox-nudge-smoke.png";
const nudgeText =
  "Use the checkbox column to select rows before adding them to this queue.";

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "annotation-add-items-checkbox-nudge-smoke.html",
  );
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 980, height: 560 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await page.goto(
      `${APP_BASE}/.tmp-smoke/annotation-add-items-checkbox-nudge-smoke.html`,
      { waitUntil: "domcontentloaded" },
    );
    await page.waitForFunction(
      (message) => document.body.textContent.includes(message),
      {},
      nudgeText,
    );
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    const bodyText = await page.evaluate(() => document.body.textContent);
    assert(
      bodyText.includes(nudgeText),
      "Selection checkbox nudge was not visible.",
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          screenshot: SCREENSHOT_PATH,
          evidence: { nudge: nudgeText },
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
    <title>Annotation Add Items Checkbox Nudge Smoke</title>
    <style>
      body {
        margin: 0;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #f7f8fa;
      }
      #root {
        min-height: 100vh;
        display: grid;
        place-items: center;
      }
      .surface {
        width: 680px;
        padding: 24px;
        background: #fff;
        border: 1px solid #dfe3e8;
      }
      .grid-shell {
        margin-top: 16px;
        height: 220px;
        border: 1px solid #dfe3e8;
        background: repeating-linear-gradient(
          to bottom,
          #ffffff,
          #ffffff 43px,
          #f7f8fa 44px,
          #f7f8fa 45px
        );
      }
    </style>
    <script>
      window.__FUTURE_AGI_CONFIG__ = { VITE_HOST_API: window.location.origin };
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";
      import { SelectionCheckboxNudge } from "/src/sections/annotations/queues/items/add-items-dialog.jsx";

      function Harness() {
        return React.createElement(
          "div",
          { className: "surface" },
          React.createElement(SelectionCheckboxNudge, { selectionCount: 0 }),
          React.createElement("div", { className: "grid-shell" }),
        );
      }

      createRoot(document.getElementById("root")).render(
        React.createElement(Harness),
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
  if (process.platform === "linux") {
    return "/usr/bin/google-chrome";
  }
  return "google-chrome";
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
