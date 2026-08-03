import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_SESSION_SELECT_ALL_SCREENSHOT ||
  "/tmp/annotation-session-select-all-smoke.png";
const IDS = {
  queue: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  project: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
};

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "annotation-session-select-all.html",
  );
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
    if (url.pathname.includes("/model-hub/annotation-queues/")) {
      const entry = {
        method: request.method(),
        pathname: url.pathname,
        postData: request.postData() || "",
      };
      requests.push(entry);
      return request.respond(
        jsonResponse(200, {
          status: true,
          result: {
            added: 3,
            duplicates: 0,
            errors: [],
            total_matching: 4,
          },
        }),
      );
    }
    return request.continue();
  });

  try {
    await page.goto(
      `${APP_BASE}/.tmp-smoke/annotation-session-select-all.html`,
      {
        waitUntil: "domcontentloaded",
      },
    );
    await page.click("[data-testid='page-select-all']");
    await page.click("[data-testid='select-all-matching']");
    await page.click("[data-testid='add-to-queue']");
    await page.waitForFunction(() => window.__sessionSelectAllDone === true);
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    const evidence = await page.evaluate(
      () => window.__sessionSelectAllEvidence,
    );
    const payload = JSON.parse(requests[0]?.postData || "{}");
    assert(
      !evidence?.error,
      `Session select-all smoke failed: ${evidence?.error}`,
    );
    assert(
      payload.selection?.mode === "filter",
      "Expected filter-mode payload.",
    );
    assert(
      payload.selection?.source_type === "trace_session",
      `Unexpected source_type: ${JSON.stringify(payload)}`,
    );
    assert(
      payload.selection?.project_id === IDS.project,
      `Unexpected project_id: ${JSON.stringify(payload)}`,
    );
    assert(
      payload.selection?.exclude_ids?.[0] === "session-2",
      `Expected excluded session-2, got ${JSON.stringify(payload)}`,
    );
    assert(
      payload.selection?.filter?.[0]?.column_id === "created_at",
      `Expected backend filter payload, got ${JSON.stringify(payload)}`,
    );
    assert(
      evidence?.added === 3,
      `Unexpected add count: ${JSON.stringify(evidence)}`,
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
    <title>Annotation Session Select All Smoke</title>
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
        width: 720px;
        background: #fff;
        border: 1px solid #dfe3e8;
        padding: 24px;
      }
      .toolbar,
      .footer,
      .banner {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 16px;
      }
      .row {
        display: grid;
        grid-template-columns: 36px 1fr;
        align-items: center;
        border-bottom: 1px solid #edf0f2;
        padding: 10px 0;
      }
      button {
        border: 1px solid #c4cdd5;
        background: #fff;
        padding: 8px 12px;
        font: inherit;
      }
      .primary {
        background: #212b36;
        color: #fff;
      }
      .banner {
        color: #006c9c;
        font-weight: 700;
      }
    </style>
    <script>
      window.__sessionSelectAllDone = false;
      window.__sessionSelectAllEvidence = null;
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";

      const ids = ${JSON.stringify(IDS)};
      const filters = [{
        column_id: "created_at",
        filter_config: {
          filter_type: "datetime",
          filter_op: "between",
          filter_value: ["2026-01-01T00:00:00.000Z", "2026-05-23T00:00:00.000Z"],
        },
      }];

      function Harness() {
        const [pageSelected, setPageSelected] = React.useState(false);
        const [filterMode, setFilterMode] = React.useState(false);
        const [status, setStatus] = React.useState("Ready");
        const visibleRows = ["session-1", "session-2", "session-3"];
        const excludedIds = ["session-2"];

        async function addToQueue() {
          const payload = filterMode
            ? {
                selection: {
                  mode: "filter",
                  source_type: "trace_session",
                  project_id: ids.project,
                  filter: filters,
                  exclude_ids: excludedIds,
                },
              }
            : {
                items: visibleRows
                  .filter((id) => !excludedIds.includes(id))
                  .map((id) => ({ source_type: "trace_session", source_id: id })),
              };
          try {
            const response = await fetch(
              "/model-hub/annotation-queues/" + ids.queue + "/add-items/",
              {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify(payload),
              },
            );
            const body = await response.json();
            window.__sessionSelectAllEvidence = {
              added: body?.result?.added,
              filterMode,
              payload,
            };
            setStatus("3 sessions added");
          } catch (error) {
            window.__sessionSelectAllEvidence = { error: error.message, payload };
            setStatus("Add failed");
          } finally {
            window.__sessionSelectAllDone = true;
          }
        }

        return React.createElement(
          "div",
          { className: "surface" },
          React.createElement("h1", null, "Choose from sessions"),
          React.createElement(
            "div",
            { className: "toolbar" },
            React.createElement(
              "button",
              {
                "data-testid": "page-select-all",
                onClick: () => setPageSelected(true),
              },
              "Select visible sessions",
            ),
          ),
          pageSelected &&
            React.createElement(
              "div",
              { className: "banner" },
              React.createElement("span", null, "3 visible sessions selected."),
              React.createElement(
                "button",
                {
                  "data-testid": "select-all-matching",
                  onClick: () => setFilterMode(true),
                },
                "Select all 4 sessions matching filter",
              ),
            ),
          visibleRows.map((row) =>
            React.createElement(
              "div",
              { className: "row", key: row },
              React.createElement("input", {
                type: "checkbox",
                checked: pageSelected && !excludedIds.includes(row),
                readOnly: true,
              }),
              React.createElement("span", null, row),
            ),
          ),
          React.createElement(
            "div",
            { className: "footer" },
            React.createElement(
              "button",
              {
                className: "primary",
                "data-testid": "add-to-queue",
                onClick: addToQueue,
              },
              filterMode ? "(4) Add to queue" : "(2) Add to queue",
            ),
            React.createElement("span", null, status),
          ),
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
