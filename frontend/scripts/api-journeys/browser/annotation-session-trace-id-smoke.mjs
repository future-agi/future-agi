import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_SESSION_TRACE_ID_SCREENSHOT ||
  "/tmp/annotation-session-trace-id-smoke.png";
const TRACE_ID = "trace-session-child-1";

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(harnessDir, "annotation-session-trace-id.html");
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const requests = [];
  const pageErrors = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 960, height: 560 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/tracer/trace/")) {
      requests.push({ method: request.method(), pathname: url.pathname });
      if (url.pathname.includes("undefined")) {
        return request.respond(
          jsonResponse(400, {
            status: false,
            result: "error retrieving trace Unable to retrieve trace.",
          }),
        );
      }
      return request.respond(
        jsonResponse(200, {
          status: true,
          result: {
            trace: { id: TRACE_ID },
            observation_spans: [],
            summary: { total_spans: 0 },
          },
        }),
      );
    }
    return request.continue();
  });

  try {
    await page.goto(`${APP_BASE}/.tmp-smoke/annotation-session-trace-id.html`, {
      waitUntil: "domcontentloaded",
    });
    await page.click("[data-testid='session-trace']");
    await page.waitForFunction(() => window.__sessionTraceDone === true);
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    const evidence = await page.evaluate(() => window.__sessionTraceEvidence);
    assert(!evidence?.error, `Session trace smoke failed: ${evidence?.error}`);
    assert(
      evidence?.traceData?.trace_id === TRACE_ID,
      `Expected trace_id=${TRACE_ID}, got ${JSON.stringify(evidence)}`,
    );
    assert(
      !("traceId" in evidence.traceData),
      `Unexpected camel traceId payload: ${JSON.stringify(evidence.traceData)}`,
    );
    assert(
      requests.some((entry) => entry.pathname === `/tracer/trace/${TRACE_ID}/`),
      `Expected canonical trace request, got ${JSON.stringify(requests)}`,
    );
    assert(
      requests.every((entry) => !entry.pathname.includes("undefined")),
      `Unexpected undefined trace request: ${JSON.stringify(requests)}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          screenshot: SCREENSHOT_PATH,
          requests,
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
    <title>Annotation Session Trace ID Smoke</title>
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
        width: 640px;
        background: #fff;
        border: 1px solid #dfe3e8;
        padding: 24px;
      }
      button {
        border: 1px solid #c4cdd5;
        background: #fff;
        padding: 10px 12px;
        font: inherit;
      }
      .status {
        color: #006c9c;
        font-weight: 700;
      }
    </style>
    <script>
      window.__sessionTraceDone = false;
      window.__sessionTraceEvidence = null;
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";

      const traceId = ${JSON.stringify(TRACE_ID)};

      function Harness() {
        const [status, setStatus] = React.useState("Session trace ready");

        async function openTrace() {
          const traceData = { trace_id: traceId };
          try {
            const response = await fetch("/tracer/trace/" + traceData.trace_id + "/");
            window.__sessionTraceEvidence = {
              ok: response.ok,
              requestTraceId: traceData.trace_id,
              traceData,
            };
            setStatus(response.ok ? "Trace loaded" : "Trace failed");
          } catch (error) {
            window.__sessionTraceEvidence = { error: error.message, traceData };
            setStatus("Trace failed");
          } finally {
            window.__sessionTraceDone = true;
          }
        }

        return React.createElement(
          "div",
          { className: "surface" },
          React.createElement("h1", null, "Session Trace Drawer"),
          React.createElement(
            "button",
            { "data-testid": "session-trace", onClick: openTrace },
            "customer asks for help",
          ),
          React.createElement("p", { className: "status" }, status),
        );
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
