import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_SELF_REVIEW_BLOCK_SCREENSHOT ||
  "/tmp/annotation-self-review-block-smoke.png";

const IDS = {
  queue: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  item: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  ownItem: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
};

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "annotation-self-review-block.html",
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
      url.pathname.endsWith(`/${IDS.item}/review/`) ||
      url.pathname.endsWith(`/${IDS.ownItem}/review/`)
    ) {
      return request.respond(
        jsonResponse(403, {
          status: false,
          message: "You cannot review your own annotation.",
        }),
      );
    }
    return request.continue();
  });

  try {
    await page.goto(
      `${APP_BASE}/.tmp-smoke/annotation-self-review-block.html`,
      {
        waitUntil: "domcontentloaded",
      },
    );
    await page.waitForFunction(() => window.__selfReviewDone === true);
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    const evidence = await page.evaluate(() => window.__selfReviewEvidence);
    assert(
      !evidence?.error,
      `Self-review smoke failed: ${JSON.stringify(evidence)}`,
    );
    assert(evidence?.blocked === true, "Self-review request was not blocked.");
    assert(
      evidence?.message === "You cannot review your own annotation.",
      `Unexpected block message: ${JSON.stringify(evidence)}`,
    );
    assert(
      requests.some((entry) => entry.method === "POST"),
      `Expected a review POST, got ${JSON.stringify(requests)}`,
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
    <title>Annotation Self Review Block Smoke</title>
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
      .blocked {
        color: #b42318;
        font-weight: 700;
      }
    </style>
    <script>
      window.__selfReviewDone = false;
      window.__selfReviewEvidence = null;
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";

      const ids = ${JSON.stringify(IDS)};

      function Harness() {
        const [status, setStatus] = React.useState("Checking self-review block");

        React.useEffect(() => {
          let active = true;
          async function run() {
            try {
              const result = await postReview(
                "/model-hub/annotation-queues/" +
                  ids.queue +
                  "/items/" +
                  ids.ownItem +
                  "/review/",
              );
              if (!active) return;
              window.__selfReviewEvidence = result;
              window.__selfReviewDone = true;
              setStatus(result.message);
            } catch (error) {
              window.__selfReviewEvidence = { error: error.message };
              window.__selfReviewDone = true;
              setStatus("Self-review smoke failed");
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
          React.createElement("h1", null, "Self Review Guard"),
          React.createElement("p", { className: "status" }, "Review action result"),
          React.createElement("p", { className: "blocked" }, status),
        );
      }

      async function postReview(path) {
        const response = await fetch(path, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ action: "approve" }),
        });
        const body = await response.json();
        return {
          blocked: response.status === 403,
          message: body?.message,
        };
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
