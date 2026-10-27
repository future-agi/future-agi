import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_STALE_VALUE_RESET_SCREENSHOT ||
  "/tmp/annotation-stale-value-reset-smoke.png";

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "annotation-stale-value-reset-smoke.html",
  );
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 900, height: 620 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.endsWith("/annotations/")) {
      return request.respond(jsonResponse(200, { status: true, result: [] }));
    }
    return request.continue();
  });

  try {
    await page.goto(
      `${APP_BASE}/.tmp-smoke/annotation-stale-value-reset-smoke.html`,
      { waitUntil: "domcontentloaded" },
    );
    await page.waitForFunction(() =>
      document.body.textContent.includes("Harness item item-1"),
    );
    await page.waitForSelector('textarea[placeholder="Add notes for this label..."]');
    await page.waitForFunction(
      () => window.__panelState?.labelNotes === "old label note",
    );
    const before = await panelState(page);
    assert(
      before.labelNotes === "old label note" &&
        before.itemNotes === "old item note" &&
        before.submitDisabled === false,
      `Expected initial item to show old annotation state, got ${JSON.stringify(before)}`,
    );

    await clickByText(page, "Move to item 2");
    await page.waitForFunction(() =>
      document.body.textContent.includes("Harness item item-2"),
    );
    await page.waitForFunction(() => window.__panelState?.labelNotes === "");
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    const after = await panelState(page);
    assert(
      after.labelNotes === "" &&
        after.itemNotes === "" &&
        after.submitDisabled === true &&
        after.submittedCount === 0,
      `Expected stale annotation state to clear on item change, got ${JSON.stringify(after)}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          screenshot: SCREENSHOT_PATH,
          evidence: { before, after },
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
    <title>Annotation Stale Value Reset Smoke</title>
    <style>
      body {
        margin: 0;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #f7f8fa;
      }
      #root {
        padding: 24px;
      }
      .surface {
        width: 640px;
        height: 520px;
        background: #fff;
        border: 1px solid #dfe3e8;
        display: flex;
        flex-direction: column;
      }
      .toolbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 12px 16px;
        border-bottom: 1px solid #dfe3e8;
      }
      .panel {
        flex: 1;
        min-height: 0;
      }
    </style>
    <script>
      window.__FUTURE_AGI_CONFIG__ = { VITE_HOST_API: window.location.origin };
      window.__panelState = null;
      window.__submitted = [];
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";
      import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
      import { SnackbarProvider } from "notistack";
      import LabelPanel from "/src/sections/annotations/queues/annotate/label-panel.jsx";

      const queryClient = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      });

      const label = {
        id: "queue-label-1",
        label_id: "label-1",
        name: "Passes checks",
        type: "thumbs_up_down",
        settings: {},
        allow_notes: true,
        required: false,
      };
      const previousAnnotations = [
        {
          label_id: "label-1",
          value: { value: "up" },
          notes: "old label note",
        },
      ];

      function Harness() {
        const [itemId, setItemId] = React.useState("item-1");
        const detailItemId = itemId === "item-1" ? "item-1" : "item-1";
        React.useEffect(() => {
          const syncState = () => {
            const labelNotes = document.querySelector(
              'textarea[placeholder="Add notes for this label..."]',
            )?.value;
            const itemNotes = document.querySelector(
              'textarea[placeholder="Add notes for this item..."]',
            )?.value;
            const submitButton = Array.from(
              document.querySelectorAll("button"),
            ).find((button) => button.textContent?.includes("Submit & Next"));
            const submitDisabled = submitButton?.disabled;
            window.__panelState = {
              itemId,
              labelNotes,
              itemNotes,
              submitDisabled,
              submittedCount: window.__submitted.length,
            };
          };
          syncState();
          const timer = window.setInterval(syncState, 50);
          return () => window.clearInterval(timer);
        }, [itemId]);

        return React.createElement(
          "div",
          { className: "surface" },
          React.createElement(
            "div",
            { className: "toolbar" },
            React.createElement("strong", null, "Harness item " + itemId),
            React.createElement(
              "button",
              { type: "button", onClick: () => setItemId("item-2") },
              "Move to item 2",
            ),
          ),
          React.createElement(
            "div",
            { className: "panel" },
            React.createElement(LabelPanel, {
              labels: [label],
              annotations: previousAnnotations,
              initialItemNotes: "old item note",
              onSubmit: (payload) => {
                window.__submitted.push(payload);
              },
              queueId: "queue-1",
              itemId,
              detailItemId,
            }),
          ),
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

async function panelState(page) {
  return page.evaluate(() => window.__panelState);
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
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  return "google-chrome";
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
