import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_DIRECT_CALL_SOURCE_SCREENSHOT ||
  "/tmp/annotation-direct-call-queue-source-smoke.png";

const IDS = {
  queue: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  trace: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  rootSpan: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  callExecution: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
};

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "annotation-direct-call-queue-source-smoke.html",
  );
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const apiRequests = [];
  let addItemsPosted = false;
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1280, height: 760 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (
      url.pathname.includes("/model-hub/annotation-queues/") ||
      url.pathname.includes("/tracer/saved-views/") ||
      url.pathname.includes(`/tracer/trace/${IDS.trace}/`)
    ) {
      apiRequests.push({
        method: request.method(),
        pathname: url.pathname,
        query: url.search,
        postData: request.postData() || "",
      });
    }
    if (url.pathname.endsWith("/tracer/saved-views/")) {
      return request.respond(
        jsonResponse(200, {
          status: true,
          result: { custom_views: [] },
        }),
      );
    }
    if (url.pathname.endsWith(`/tracer/trace/${IDS.trace}/`)) {
      return request.respond(
        jsonResponse(200, {
          status: true,
          result: { trace: { id: IDS.trace, tags: [] } },
        }),
      );
    }
    if (
      url.pathname.endsWith("/model-hub/annotation-queues/") &&
      request.method() === "GET"
    ) {
      return request.respond(
        jsonResponse(200, {
          count: 1,
          results: [
            {
              id: IDS.queue,
              name: "Manager Queue",
              status: "active",
              viewer_role: "manager",
              viewer_roles: ["manager", "reviewer", "annotator"],
            },
          ],
        }),
      );
    }
    if (
      url.pathname.endsWith(
        `/model-hub/annotation-queues/${IDS.queue}/items/add-items/`,
      )
    ) {
      addItemsPosted = true;
      return request.respond(
        jsonResponse(200, {
          status: true,
          result: {
            added: 1,
            duplicates: 0,
            errors: [],
            queue_status: "active",
          },
        }),
      );
    }
    return request.continue();
  });

  try {
    await page.goto(
      `${APP_BASE}/.tmp-smoke/annotation-direct-call-queue-source-smoke.html`,
      { waitUntil: "domcontentloaded" },
    );
    await page.waitForFunction(() =>
      Array.from(document.querySelectorAll("button")).some(
        (button) => button.textContent.trim() === "Actions",
      ),
    );
    await clickByText(page, "button", "Actions");
    await clickByText(page, '[role="menuitem"]', "Add to annotation queue");
    await page.waitForFunction(() =>
      document.body.textContent.includes("Manager Queue"),
    );
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });
    await clickByText(page, "*", "Manager Queue");
    await waitForCondition(() => addItemsPosted);

    const post = apiRequests.find(
      (entry) =>
        entry.method === "POST" &&
        entry.pathname.endsWith(
          `/model-hub/annotation-queues/${IDS.queue}/items/add-items/`,
        ),
    );
    assert(post, `Add-items request missing: ${JSON.stringify(apiRequests)}`);
    const payload = JSON.parse(post.postData || "{}");
    assert(
      payload.items?.[0]?.source_type === "trace",
      `Direct call was queued as ${JSON.stringify(payload)}`,
    );
    assert(
      payload.items?.[0]?.source_id === IDS.trace,
      `Direct call queued wrong source id: ${JSON.stringify(payload)}`,
    );
    assert(
      !post.postData.includes("observation_span"),
      `Direct call should not queue a span: ${post.postData}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          screenshot: SCREENSHOT_PATH,
          payload,
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
    <title>Annotation Direct Call Queue Source Smoke</title>
    <style>
      body {
        margin: 0;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #f7f8fa;
      }
      #root {
        height: 760px;
      }
    </style>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";
      import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
      import VoiceDetailDrawerV2 from "/src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2.jsx";

      const ids = ${JSON.stringify(IDS)};
      const queryClient = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
        },
      });

      createRoot(document.getElementById("root")).render(
        React.createElement(
          QueryClientProvider,
          { client: queryClient },
          React.createElement(VoiceDetailDrawerV2, {
            data: {
              module: "project",
              id: ids.callExecution,
              call_execution_id: ids.callExecution,
              trace_id: ids.trace,
              project_id: "project-1",
              status: "completed",
              simulation_call_type: "voice",
              call_type: "outbound",
              duration_seconds: 42,
              scenario: "Direct call annotation",
              scenario_columns: {},
              transcript: [
                {
                  speakerRole: "user",
                  message: "I need help with my order.",
                  startTime: 0,
                  endTime: 2,
                },
                {
                  speakerRole: "agent",
                  message: "I can help with that.",
                  startTime: 2,
                  endTime: 4,
                },
              ],
              eval_outputs: {},
              eval_metrics: {},
              observation_span: [
                {
                  id: ids.rootSpan,
                  observation_type: "conversation",
                  parent_span_id: null,
                  span_attributes: {},
                },
              ],
            },
            onClose: () => {},
          }),
        ),
      );
    </script>
  </body>
</html>`;
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
      await handle.click();
      return;
    }
    await handle.dispose();
  }
  throw new Error(`Could not find ${selector} with text ${text}`);
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
