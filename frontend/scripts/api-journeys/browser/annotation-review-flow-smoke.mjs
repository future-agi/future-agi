import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_REVIEW_FLOW_SCREENSHOT ||
  "/tmp/annotation-review-flow-smoke.png";

const IDS = {
  queue: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  approvedItem: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  reworkItem: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  label: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
  annotator: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
  thread: "ffffffff-ffff-4fff-8fff-ffffffffffff",
};

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "annotation-review-flow-smoke.html",
  );
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const requests = [];
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
      request.method() === "GET" &&
      url.pathname.endsWith(`/${IDS.queue}/export/`) &&
      url.searchParams.get("export_format") === "csv"
    ) {
      return request.respond(
        csvResponse(
          [
            [
              "item_id",
              "requires_review",
              "review_status",
              "reviewer_email",
              "review_notes",
              "label_id",
              "label_name",
              "value",
            ].join(","),
            [
              IDS.approvedItem,
              "True",
              "approved",
              "reviewer@example.com",
              "Looks good.",
              IDS.label,
              "Thumbs",
              '"{""value"":""up""}"',
            ].join(","),
          ].join("\n"),
        ),
      );
    }
    if (url.pathname.endsWith(`/${IDS.approvedItem}/review/`)) {
      return request.respond(
        jsonResponse(200, {
          status: true,
          result: {
            reviewed_item_id: IDS.approvedItem,
            action: "approve",
            next_item: null,
            review_comments: [{ id: "approve-comment", action: "approve" }],
            review_threads: [],
          },
        }),
      );
    }
    if (url.pathname.endsWith(`/${IDS.reworkItem}/review/`)) {
      return request.respond(
        jsonResponse(200, {
          status: true,
          result: {
            reviewed_item_id: IDS.reworkItem,
            action: "request_changes",
            next_item: null,
            review_comments: [
              {
                id: "targeted-comment",
                label_id: IDS.label,
                target_annotator_id: IDS.annotator,
                action: "request_changes",
              },
            ],
            review_threads: [
              {
                id: IDS.thread,
                status: "open",
                blocking: true,
                target_annotator_id: IDS.annotator,
              },
            ],
          },
        }),
      );
    }
    if (url.pathname.endsWith(`/${IDS.thread}/resolve/`)) {
      return request.respond(
        jsonResponse(200, {
          status: true,
          result: {
            thread: { id: IDS.thread, status: "resolved" },
            comment: { id: "resolve-comment", action: "resolve" },
          },
        }),
      );
    }
    return request.continue();
  });

  try {
    await page.goto(
      `${APP_BASE}/.tmp-smoke/annotation-review-flow-smoke.html`,
      {
        waitUntil: "domcontentloaded",
      },
    );
    await page.waitForFunction(() => window.__reviewFlowDone === true);
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    const evidence = await page.evaluate(() => window.__reviewFlowEvidence);
    assert(!evidence?.error, `Review flow failed: ${JSON.stringify(evidence)}`);
    assert(
      evidence?.approved === IDS.approvedItem,
      "Approve did not complete.",
    );
    assert(
      evidence?.targetedAnnotator === IDS.annotator,
      "Request-changes did not preserve target annotator.",
    );
    assert(
      evidence?.resolved === "resolved",
      "Discussion thread did not resolve.",
    );
    assert(
      evidence?.csvHasReviewMetadata === true,
      "Reviewed CSV export metadata was not preserved.",
    );
    assert(
      evidence?.csvHasFinalValue === true,
      "Reviewed CSV export final label value was not preserved.",
    );
    assert(
      requests.filter((entry) => entry.method === "POST").length === 3,
      `Expected three review/discussion POSTs, got ${JSON.stringify(requests)}`,
    );
    assert(
      requests.some((entry) =>
        entry.postData.includes(`"target_annotator_id":"${IDS.annotator}"`),
      ),
      "Targeted review feedback payload was not sent.",
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
    <title>Annotation Review Flow Smoke</title>
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
        width: 620px;
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
      window.__reviewFlowDone = false;
      window.__reviewFlowEvidence = null;
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";

      const ids = ${JSON.stringify(IDS)};

      function Harness() {
        const [status, setStatus] = React.useState("Starting review flow");

        React.useEffect(() => {
          let active = true;
          async function run() {
            try {
              const approved = await postJson(
                "/model-hub/annotation-queues/" +
                  ids.queue +
                  "/items/" +
                  ids.approvedItem +
                  "/review/",
                { action: "approve", notes: "Looks good." },
              );
              if (!active) return;
              setStatus("Approve accepted");

              const rework = await postJson(
                "/model-hub/annotation-queues/" +
                  ids.queue +
                  "/items/" +
                  ids.reworkItem +
                  "/review/",
                {
                  action: "request_changes",
                  label_comments: [
                    {
                      label_id: ids.label,
                      target_annotator_id: ids.annotator,
                      comment: "Please re-check this score.",
                    },
                  ],
                },
              );
              if (!active) return;
              setStatus("Targeted changes requested");

              const resolved = await postJson(
                "/model-hub/annotation-queues/" +
                  ids.queue +
                  "/items/" +
                  ids.reworkItem +
                  "/discussion/" +
                  ids.thread +
                  "/resolve/",
                {},
              );
              if (!active) return;
              setStatus("Checking reviewed export");

              const csvExport = await getText(
                "/model-hub/annotation-queues/" +
                  ids.queue +
                  "/export/?export_format=csv",
              );
              if (!active) return;
              window.__reviewFlowEvidence = {
                approved: approved?.result?.reviewed_item_id,
                targetedAnnotator:
                  rework?.result?.review_threads?.[0]?.target_annotator_id,
                resolved: resolved?.result?.thread?.status,
                csvHasReviewMetadata:
                  csvExport.includes("review_status") &&
                  csvExport.includes("reviewer_email") &&
                  csvExport.includes("review_notes"),
                csvHasFinalValue: csvExport.includes('"{""value"":""up""}"'),
              };
              window.__reviewFlowDone = true;
              setStatus("Review flow accepted");
            } catch (error) {
              window.__reviewFlowEvidence = { error: error.message };
              window.__reviewFlowDone = true;
              setStatus("Review flow failed");
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
          React.createElement("h1", null, "Review Flow"),
          React.createElement("p", { className: "status" }, status),
        );
      }

      async function postJson(path, payload) {
        const response = await fetch(path, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      }

      async function getText(path) {
        const response = await fetch(path);
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.text();
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

function csvResponse(body) {
  return {
    status: 200,
    headers: {
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "GET,POST,PATCH,DELETE,OPTIONS",
      "access-control-allow-headers": "*",
      "content-type": "text/csv",
    },
    body,
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
