import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_CONTENT_COPY_SCREENSHOT ||
  "/tmp/annotation-content-copy-smoke.png";

const EXPECTED_OPTIONS = JSON.stringify(
  {
    expected: false,
    alternatives: ["passed", "failed"],
  },
  null,
  2,
);

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(harnessDir, "annotation-content-copy-smoke.html");
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 960, height: 600 },
    args: ["--no-sandbox"],
  });
  await browser
    .defaultBrowserContext()
    .overridePermissions(APP_BASE, ["clipboard-read", "clipboard-write"])
    .catch(() => null);

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => {
    window.__annotationCopyText = "";
    Object.defineProperty(navigator, "clipboard", {
      value: {
        writeText: async (text) => {
          window.__annotationCopyText = text;
        },
        readText: async () => window.__annotationCopyText || "",
      },
      configurable: true,
    });
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await page.goto(`${APP_BASE}/.tmp-smoke/annotation-content-copy-smoke.html`, {
      waitUntil: "networkidle0",
    });
    await page.waitForSelector('button[aria-label="Copy approved"]');

    await page.click('button[aria-label="Copy approved"]');
    const booleanText = await page.evaluate(() => navigator.clipboard.readText());
    assert(booleanText === "False", `Boolean copied as ${booleanText}`);

    await page.click('button[aria-label="Copy options"]');
    const optionsText = await page.evaluate(() => navigator.clipboard.readText());
    assert(
      optionsText === EXPECTED_OPTIONS,
      `JSON copied incorrectly: ${optionsText}`,
    );

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          screenshot: SCREENSHOT_PATH,
          evidence: {
            booleanText,
            optionsText,
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
}

function smokeHarnessHtml() {
  return `<!doctype html>
<html>
  <head>
    <meta charset="UTF-8" />
    <title>Annotation Content Copy Smoke</title>
    <style>
      body {
        margin: 0;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #f7f8fa;
      }
      #root {
        height: 600px;
      }
    </style>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";
      import ContentPanel from "/src/sections/annotations/queues/annotate/content-panel.jsx";

      const item = {
        source_type: "dataset_row",
        source_content: {
          fields: {
            approved: false,
            options: {
              expected: false,
              alternatives: ["passed", "failed"],
            },
          },
          field_types: {
            approved: "boolean",
            options: "json",
          },
        },
      };

      createRoot(document.getElementById("root")).render(
        React.createElement(ContentPanel, { item }),
      );
    </script>
  </body>
</html>`;
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
