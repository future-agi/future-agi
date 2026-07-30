import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_LABEL_PICKER_SCREENSHOT ||
  "/tmp/annotation-label-picker-smoke.png";

const createdLabel = {
  id: "22222222-2222-4222-8222-222222222222",
  name: "Browser Auto Label",
  type: "text",
  settings: {},
  allow_notes: false,
};

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "annotation-label-picker-smoke.html",
  );
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 980, height: 720 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/model-hub/annotations-labels/") {
      if (request.method() === "GET") {
        return request.respond(jsonResponse(200, []));
      }
      if (request.method() === "POST") {
        return request.respond(jsonResponse(201, createdLabel));
      }
    }
    return request.continue();
  });

  try {
    await page.goto(
      `${APP_BASE}/.tmp-smoke/annotation-label-picker-smoke.html`,
      { waitUntil: "domcontentloaded" },
    );
    await page.waitForFunction(() =>
      document.body.textContent.includes("Create new label"),
    );
    await clickByText(page, "Create new label");
    await page.waitForSelector('input[name="name"]');
    await page.type('input[name="name"]', createdLabel.name);
    await clickByText(page, "Text");
    await clickByText(page, "Create");
    await page.waitForFunction(
      (labelId) => window.__selectedIds?.includes(labelId),
      {},
      createdLabel.id,
    );
    await page.waitForFunction(
      (labelName) => document.body.textContent.includes(labelName),
      {},
      createdLabel.name,
    );
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    const selectedIds = await page.evaluate(() => window.__selectedIds);
    assert(
      selectedIds.includes(createdLabel.id),
      `Created label was not auto-selected: ${JSON.stringify(selectedIds)}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          screenshot: SCREENSHOT_PATH,
          evidence: {
            created_label_id: createdLabel.id,
            selected_ids: selectedIds,
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
    <title>Annotation Label Picker Smoke</title>
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
        width: 520px;
        padding: 24px;
        background: #fff;
        border: 1px solid #dfe3e8;
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
      import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
      import { SnackbarProvider } from "notistack";
      import LabelPicker from "/src/sections/annotations/queues/components/label-picker.jsx";

      const queryClient = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      });

      function Harness() {
        const [selectedIds, setSelectedIds] = React.useState([]);
        window.__selectedIds = selectedIds;
        return React.createElement(
          "div",
          { className: "surface" },
          React.createElement(LabelPicker, {
            selectedIds,
            onChange: (ids) => {
              window.__selectedIds = ids;
              setSelectedIds(ids);
            },
          }),
        );
      }

      createRoot(document.getElementById("root")).render(
        React.createElement(
          QueryClientProvider,
          { client: queryClient },
          React.createElement(
            SnackbarProvider,
            null,
            React.createElement(Harness),
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
