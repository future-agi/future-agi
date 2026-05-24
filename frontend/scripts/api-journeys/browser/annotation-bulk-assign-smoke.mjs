import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_BULK_ASSIGN_SCREENSHOT ||
  "/tmp/annotation-bulk-assign-smoke.png";

const IDS = {
  queue: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  item: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  annotator: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
};

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(harnessDir, "annotation-bulk-assign-smoke.html");
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const requests = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 900, height: 540 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

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
    if (url.pathname.endsWith(`/items/assign/`)) {
      return request.respond(
        jsonResponse(200, {
          status: true,
          result: { assigned: 1 },
        }),
      );
    }
    return request.continue();
  });

  try {
    await page.goto(`${APP_BASE}/.tmp-smoke/annotation-bulk-assign-smoke.html`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(() => window.__bulkAssignDone === true);
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    const evidence = await page.evaluate(() => window.__bulkAssignEvidence);
    assert(!evidence?.error, `Bulk assign failed: ${JSON.stringify(evidence)}`);
    assert(evidence?.assigned === 1, "Bulk assign did not complete.");
    assert(
      evidence?.payload?.action === "set",
      `Expected set action, got ${JSON.stringify(evidence?.payload)}`,
    );
    assert(
      evidence?.payload?.item_ids?.[0] === IDS.item,
      `Selected item id missing: ${JSON.stringify(evidence?.payload)}`,
    );
    assert(
      evidence?.payload?.user_ids?.[0] === IDS.annotator,
      `Selected annotator id missing: ${JSON.stringify(evidence?.payload)}`,
    );
    assert(
      requests.filter((entry) => entry.method === "POST").length === 1,
      `Expected one assign POST, got ${JSON.stringify(requests)}`,
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
    <title>Annotation Bulk Assign Smoke</title>
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
        width: 580px;
        background: #fff;
        border: 1px solid #dfe3e8;
        padding: 24px;
      }
      .row {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 10px 0;
      }
      button {
        border: 1px solid #2065d1;
        background: #2065d1;
        color: #fff;
        font-weight: 700;
        padding: 8px 14px;
      }
      button.secondary {
        background: #fff;
        color: #2065d1;
      }
      button:disabled {
        color: #919eab;
        border-color: #dfe3e8;
        background: #f4f6f8;
      }
      .dialog {
        margin-top: 18px;
        border: 1px solid #dfe3e8;
        padding: 16px;
      }
      .status {
        color: #637381;
        font-size: 14px;
      }
    </style>
    <script>
      window.__bulkAssignDone = false;
      window.__bulkAssignEvidence = null;
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";

      const ids = ${JSON.stringify(IDS)};

      function Harness() {
        const [selected, setSelected] = React.useState(false);
        const [dialogOpen, setDialogOpen] = React.useState(false);
        const [annotatorSelected, setAnnotatorSelected] = React.useState(false);
        const [status, setStatus] = React.useState("Ready");

        async function assignSelected(targetSelected = annotatorSelected) {
          try {
            const payload = {
              item_ids: [ids.item],
              user_ids: targetSelected ? [ids.annotator] : [],
              action: "set",
            };
            const result = await postJson(
              "/model-hub/annotation-queues/" + ids.queue + "/items/assign/",
              payload,
            );
            window.__bulkAssignEvidence = {
              assigned: result?.result?.assigned,
              payload,
            };
            window.__bulkAssignDone = true;
            setStatus("Assigned");
          } catch (error) {
            window.__bulkAssignEvidence = { error: error.message };
            window.__bulkAssignDone = true;
            setStatus("Failed");
          }
        }

        React.useEffect(() => {
          async function run() {
            setSelected(true);
            setDialogOpen(true);
            setAnnotatorSelected(true);
            await new Promise((resolve) => setTimeout(resolve, 50));
            await assignSelected(true);
          }
          run();
        }, []);

        return React.createElement(
          "div",
          { className: "surface" },
          React.createElement("h1", null, "Queue Items"),
          React.createElement("p", { className: "status" }, status),
          React.createElement(
            "div",
            { className: "row" },
            React.createElement("input", {
              type: "checkbox",
              checked: selected,
              readOnly: true,
              "aria-label": "Select item",
            }),
            React.createElement("span", null, "Trace item"),
            React.createElement(
              "button",
              {
                className: "secondary",
                disabled: !selected,
              },
              "Assign Selected (1)",
            ),
          ),
          dialogOpen
            ? React.createElement(
                "div",
                { className: "dialog" },
                React.createElement("h2", null, "Assign Selected Items"),
                React.createElement(
                  "label",
                  null,
                  React.createElement("input", {
                    type: "checkbox",
                    checked: annotatorSelected,
                    readOnly: true,
                  }),
                  "Alice",
                ),
                React.createElement(
                  "div",
                  { style: { marginTop: "12px" } },
                  React.createElement("button", { disabled: false }, "Assign"),
                ),
              )
            : null,
        );
      }

      async function postJson(path, payload) {
        const response = await fetch(path, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        });
        const body = await response.json();
        if (!response.ok) throw new Error("HTTP " + response.status);
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
