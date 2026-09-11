import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_RULE_RUN_NOW_SCREENSHOT ||
  "/tmp/annotation-rule-run-now-smoke.png";

const IDS = {
  queue: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  rule: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
};

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(harnessDir, "annotation-rule-run-now-smoke.html");
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const requests = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 900, height: 520 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

  let evaluateCalls = 0;
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
    if (
      url.pathname.endsWith(
        `/automation-rules/${IDS.rule}/evaluate/`,
      )
    ) {
      evaluateCalls += 1;
      if (evaluateCalls === 1) {
        return request.respond(
          jsonResponse(202, {
            status: "scheduled",
            workflow_id: "wf-rule-run-now",
            message:
              "We're preparing your data. You'll get an email when it's ready.",
          }),
        );
      }
      return request.respond(
        jsonResponse(409, {
          status: false,
          result: "A run is already in progress for this rule.",
        }),
      );
    }
    return request.continue();
  });

  try {
    await page.goto(`${APP_BASE}/.tmp-smoke/annotation-rule-run-now-smoke.html`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(() => window.__ruleRunDone === true);
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    const evidence = await page.evaluate(() => window.__ruleRunEvidence);
    assert(!evidence?.error, `Run Now smoke failed: ${JSON.stringify(evidence)}`);
    assert(evidence?.scheduled === "scheduled", "Async 202 run was not accepted.");
    assert(
      evidence?.duplicateMessage === "A run is already in progress for this rule.",
      "Duplicate-run warning was not surfaced.",
    );
    assert(
      requests.filter((entry) => entry.method === "POST").length === 2,
      `Expected two evaluate POSTs, got ${JSON.stringify(requests)}`,
    );
    assert(
      requests.every((entry) =>
        entry.pathname.endsWith(`/automation-rules/${IDS.rule}/evaluate/`),
      ),
      `Unexpected request path: ${JSON.stringify(requests)}`,
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
    <title>Annotation Rule Run Now Smoke</title>
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
      button {
        min-width: 108px;
        border: 1px solid #2065d1;
        background: #fff;
        color: #2065d1;
        font-weight: 700;
        padding: 8px 14px;
      }
      button:disabled {
        color: #919eab;
        border-color: #dfe3e8;
      }
      .status {
        color: #637381;
        font-size: 14px;
      }
      .notice {
        margin-top: 12px;
        color: #005249;
        font-size: 14px;
      }
      .warning {
        margin-top: 8px;
        color: #7a4100;
        font-size: 14px;
      }
    </style>
    <script>
      window.__ruleRunDone = false;
      window.__ruleRunEvidence = null;
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";

      const ids = ${JSON.stringify(IDS)};

      function Harness() {
        const [running, setRunning] = React.useState(false);
        const [status, setStatus] = React.useState("Ready");
        const [notice, setNotice] = React.useState("");
        const [warning, setWarning] = React.useState("");

        React.useEffect(() => {
          let active = true;
          async function run() {
            try {
              setRunning(true);
              setStatus("Running...");
              const scheduled = await postEvaluate();
              if (!active) return;
              setNotice(scheduled.message);
              setStatus("Scheduled");
              setRunning(false);

              const duplicate = await postEvaluate({ allowError: true });
              if (!active) return;
              setWarning(duplicate.result);
              window.__ruleRunEvidence = {
                scheduled: scheduled.status,
                duplicateMessage: duplicate.result,
              };
              window.__ruleRunDone = true;
            } catch (error) {
              window.__ruleRunEvidence = { error: error.message };
              window.__ruleRunDone = true;
              setStatus("Failed");
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
          React.createElement("h1", null, "Automation Rule"),
          React.createElement("p", { className: "status" }, status),
          React.createElement(
            "button",
            { disabled: running, "aria-label": running ? "Running" : "Run Now" },
            running ? "Running..." : "Run Now",
          ),
          notice ? React.createElement("div", { className: "notice" }, notice) : null,
          warning
            ? React.createElement("div", { className: "warning" }, warning)
            : null,
        );
      }

      async function postEvaluate(options = {}) {
        const response = await fetch(
          "/model-hub/annotation-queues/" +
            ids.queue +
            "/automation-rules/" +
            ids.rule +
            "/evaluate/",
          { method: "POST" },
        );
        const body = await response.json();
        if (!response.ok && !options.allowError) {
          throw new Error("HTTP " + response.status);
        }
        return body;
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
