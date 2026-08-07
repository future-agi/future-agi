import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_FALCON_CONTEXT_SCREENSHOT ||
  "/tmp/annotation-falcon-context-smoke.png";

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "annotation-falcon-context-smoke.html",
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
      `${APP_BASE}/.tmp-smoke/annotation-falcon-context-smoke.html`,
      { waitUntil: "domcontentloaded" },
    );
    await page.waitForFunction(
      () => window.__falconContext?.page === "annotation_queues",
    );
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    const context = await page.evaluate(() => window.__falconContext);
    assert(
      context?.entity_type === "annotation_queue",
      `Unexpected entity_type: ${JSON.stringify(context)}`,
    );
    assert(
      context?.entity_id === "queue-123",
      `Unexpected entity_id: ${JSON.stringify(context)}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          screenshot: SCREENSHOT_PATH,
          evidence: { context },
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
    <title>Annotation Falcon Context Smoke</title>
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
        width: 560px;
        padding: 24px;
        background: #fff;
        border: 1px solid #dfe3e8;
      }
      pre {
        margin: 0;
        padding: 16px;
        background: #f4f6f8;
        color: #1c252e;
        white-space: pre-wrap;
        font-size: 13px;
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
      import { MemoryRouter } from "react-router-dom";
      import { useFalconContext } from "/src/sections/falcon-ai/hooks/useFalconContext.js";

      function ContextDump() {
        const context = useFalconContext();
        window.__falconContext = context;
        return React.createElement(
          "div",
          { className: "surface" },
          React.createElement("pre", null, JSON.stringify(context, null, 2)),
        );
      }

      createRoot(document.getElementById("root")).render(
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/dashboard/annotations/queues/queue-123/annotate"] },
          React.createElement(ContextDump),
        ),
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
