import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_QUEUE_LIMIT_SCREENSHOT ||
  "/tmp/annotation-queue-limit-message-smoke.png";

const backendMessage =
  "You've reached the 3 annotation queues limit across this organization (5 existing queues; 2 in the current workspace and 3 in other workspaces). Archive unused queues in another workspace or upgrade your plan.";
const projectId = "11111111-1111-4111-8111-111111111111";

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "annotation-queue-limit-message-smoke.html",
  );
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  let capturedPayload = null;
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 980, height: 640 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (
      url.pathname === "/model-hub/annotation-queues/get-or-create-default/" &&
      request.method() === "POST"
    ) {
      capturedPayload = JSON.parse(request.postData() || "{}");
      return request.respond(
        jsonResponse(402, {
          status: false,
          type: "entitlement_error",
          code: "ENTITLEMENT_LIMIT",
          detail: backendMessage,
          message: backendMessage,
          result: backendMessage,
          error: {
            code: "ENTITLEMENT_LIMIT",
            message: backendMessage,
            detail: {
              feature: "annotation_queues",
              current_usage: 5,
              limit: 3,
              workspace_usage: 2,
              other_workspace_usage: 3,
            },
          },
          upgrade_required: true,
        }),
      );
    }
    return request.continue();
  });

  try {
    await page.goto(
      `${APP_BASE}/.tmp-smoke/annotation-queue-limit-message-smoke.html`,
      { waitUntil: "domcontentloaded" },
    );
    await page.waitForSelector("#load-default-queue");
    await page.click("#load-default-queue");
    await page.waitForFunction(
      (message) => document.body.textContent.includes(message),
      {},
      backendMessage,
    );
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    const bodyText = await page.evaluate(() => document.body.textContent);
    assert(
      bodyText.includes(backendMessage),
      "Backend queue limit message was not rendered in the snackbar.",
    );
    assert(
      capturedPayload?.project_id === projectId,
      `Default queue payload was not canonical: ${JSON.stringify(capturedPayload)}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          screenshot: SCREENSHOT_PATH,
          evidence: {
            rendered_message: backendMessage,
            request_payload: capturedPayload,
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
    <title>Annotation Queue Limit Message Smoke</title>
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
      button {
        border: 0;
        background: #1c252e;
        color: #fff;
        padding: 10px 14px;
        cursor: pointer;
      }
      p {
        margin: 0 0 16px;
        color: #637381;
        font-size: 14px;
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
      import { useGetOrCreateDefaultQueue } from "/src/api/annotation-queues/annotation-queues.js";

      const queryClient = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      });

      function Harness() {
        const getDefaultQueue = useGetOrCreateDefaultQueue();
        return React.createElement(
          "div",
          { className: "surface" },
          React.createElement("p", null, "Observe default queue creation uses the backend limit message."),
          React.createElement(
            "button",
            {
              id: "load-default-queue",
              onClick: () => getDefaultQueue.mutate({ projectId: "${projectId}" }),
            },
            "Open annotation labels",
          ),
        );
      }

      createRoot(document.getElementById("root")).render(
        React.createElement(
          QueryClientProvider,
          { client: queryClient },
          React.createElement(
            SnackbarProvider,
            { maxSnack: 1 },
            React.createElement(Harness),
          ),
        ),
      );
    </script>
  </body>
</html>
`;
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
