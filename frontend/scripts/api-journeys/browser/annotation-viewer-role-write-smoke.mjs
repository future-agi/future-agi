import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_VIEWER_ROLE_WRITE_SCREENSHOT ||
  "/tmp/annotation-viewer-role-write-smoke.png";

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "annotation-viewer-role-write-smoke.html",
  );
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const consoleMessages = [];
  const requests = [];
  const responses = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 900, height: 520 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => consoleMessages.push(message.text()));
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.includes("/model-hub/annotation-queues/")) {
      responses.push({
        status: response.status(),
        pathname: url.pathname,
      });
    }
  });

  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.includes("/model-hub/annotation-queues/")) {
      requests.push({
        method: request.method(),
        pathname: url.pathname,
        postData: request.postData() || "",
      });
    }
    if (
      request.method() === "OPTIONS" &&
      url.pathname.includes("/model-hub/annotation-queues/")
    ) {
      return request.respond(corsResponse(204));
    }
    if (url.pathname.endsWith("/annotations/submit/")) {
      return request.respond(
        jsonResponse(200, { status: true, result: { submitted: 1 } }),
      );
    }
    if (url.pathname.endsWith("/complete/")) {
      return request.respond(
        jsonResponse(200, {
          status: true,
          result: {
            completed_item_id: "22222222-2222-4222-8222-222222222222",
            next_item: null,
          },
        }),
      );
    }
    return request.continue();
  });

  try {
    await page.goto(
      `${APP_BASE}/.tmp-smoke/annotation-viewer-role-write-smoke.html`,
      { waitUntil: "domcontentloaded" },
    );
    await page.waitForFunction(() => window.__viewerRoleWriteDone === true);
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    const evidence = await page.evaluate(() => window.__viewerRoleWriteEvidence);
    const queueWrites = requests.filter(
      (entry) =>
        entry.method === "POST" &&
        entry.pathname.includes(
          "/model-hub/annotation-queues/11111111-1111-4111-8111-111111111111/",
        ),
    );
    assert(
      !evidence?.error,
      `Mutation failed: ${JSON.stringify({ evidence, requests, responses, consoleMessages })}`,
    );
    assert(
      queueWrites.length === 2,
      `Expected submit and complete requests, got ${JSON.stringify({ queueWrites, requests, evidence, consoleMessages })}`,
    );
    assert(
      queueWrites.every((entry) => !entry.postData.includes("workspace")),
      `Frontend sent workspace workaround data: ${JSON.stringify(queueWrites)}`,
    );
    assert(
      evidence?.submitted === 1 &&
        evidence?.completedItemId ===
          "22222222-2222-4222-8222-222222222222",
      `Unexpected viewer write evidence: ${JSON.stringify(evidence)}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          screenshot: SCREENSHOT_PATH,
          requests: queueWrites,
          console: consoleMessages,
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
    <title>Annotation Viewer Role Write Smoke</title>
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
        width: 560px;
        background: #fff;
        border: 1px solid #dfe3e8;
        padding: 24px;
      }
      .status {
        color: #637381;
        font-size: 14px;
      }
    </style>
    <script>
      window.__FUTURE_AGI_CONFIG__ = { VITE_HOST_API: window.location.origin };
      window.__viewerRoleWriteDone = false;
      window.__viewerRoleWriteEvidence = null;
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";

      function Harness() {
        const queueId = "11111111-1111-4111-8111-111111111111";
        const itemId = "22222222-2222-4222-8222-222222222222";
        const labelId = "44444444-4444-4444-8444-444444444444";
        const [status, setStatus] = React.useState("Starting viewer queue-role writes");

        React.useEffect(() => {
          let active = true;
          async function run() {
            try {
              const submitResponse = await postJson(
                "/model-hub/annotation-queues/" +
                  queueId +
                  "/items/" +
                  itemId +
                  "/annotations/submit/",
                {
                  annotations: [
                    { label_id: labelId, value: { value: "positive" } },
                  ],
                },
              );
              if (!active) return;
              setStatus("Annotation submitted");
              const completeResponse = await postJson(
                "/model-hub/annotation-queues/" +
                  queueId +
                  "/items/" +
                  itemId +
                  "/complete/",
                {},
              );
              if (!active) return;
              window.__viewerRoleWriteEvidence = {
                submitted: submitResponse?.result?.submitted,
                completedItemId:
                  completeResponse?.result?.completed_item_id,
              };
              window.__viewerRoleWriteDone = true;
              setStatus("Viewer queue-role writes completed");
            } catch (error) {
              window.__viewerRoleWriteEvidence = {
                error: error?.message || String(error),
                response: error?.response?.data || null,
                stack: error?.stack || null,
                statusCode: error?.statusCode || null,
                keys: error && typeof error === "object" ? Object.keys(error) : [],
              };
              window.__viewerRoleWriteDone = true;
              setStatus("Viewer queue-role writes failed");
            }
          }
          run();
          return () => {
            active = false;
          };
        }, []);

        return React.createElement(
          "div",
          { className: "surface" },
          React.createElement("h1", null, "Viewer Queue Role"),
          React.createElement("p", { className: "status" }, status),
        );
      }

      async function postJson(path, payload) {
        const response = await fetch(path, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.json();
      }

      createRoot(document.getElementById("root")).render(
        React.createElement(Harness),
      );
    </script>
  </body>
</html>`;
}

function jsonResponse(status, body) {
  return {
    status,
    headers: {
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "GET,POST,PATCH,DELETE,OPTIONS",
      "access-control-allow-headers": "*",
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  };
}

function corsResponse(status) {
  return {
    status,
    headers: {
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "GET,POST,PATCH,DELETE,OPTIONS",
      "access-control-allow-headers": "*",
    },
    body: "",
  };
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
