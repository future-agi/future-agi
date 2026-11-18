import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_COMPLETED_NAVIGATION_SCREENSHOT ||
  "/tmp/annotation-completed-navigation-smoke.png";

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "annotation-completed-navigation-smoke.html",
  );
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const requests = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 820, height: 420 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/model-hub/annotation-queues/")) {
      requests.push(`${request.method()} ${url.pathname}${url.search}`);
    }
    if (
      url.pathname ===
      "/model-hub/annotation-queues/queue-completed/items/next-item/"
    ) {
      return request.respond(
        jsonResponse(200, {
          status: true,
          result: {
            item: {
              id: "item-1",
              status: "completed",
              review_status: "approved",
            },
          },
        }),
      );
    }
    if (
      url.pathname ===
      "/model-hub/annotation-queues/queue-completed/items/item-1/annotate-detail/"
    ) {
      return request.respond(
        jsonResponse(200, {
          status: true,
          result: {
            item: {
              id: "item-1",
              status: "completed",
              review_status: "approved",
            },
            queue: {
              id: "queue-completed",
              name: "Completed queue",
              status: "completed",
            },
            labels: [],
            annotations: [],
            review_comments: [],
            review_threads: [],
            existing_notes: "",
            span_notes: [],
            progress: {
              total: 2,
              completed: 2,
              current_position: 1,
              user_progress: {
                total: 2,
                completed: 2,
                current_position: 1,
              },
            },
            next_item_id: "item-2",
            prev_item_id: null,
          },
        }),
      );
    }
    return request.continue();
  });

  try {
    await page.goto(
      `${APP_BASE}/.tmp-smoke/annotation-completed-navigation-smoke.html`,
      { waitUntil: "domcontentloaded" },
    );
    await page.waitForFunction(() =>
      document.body.textContent.includes("Completed item item-1"),
    );
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });
    await page.click('button[aria-label="Next"]');
    await page.waitForFunction(() => window.__navigatedTo === "item-2");

    const evidence = await page.evaluate(() => ({
      nextItem: window.__nextItem,
      detail: window.__detailSummary,
      navigatedTo: window.__navigatedTo,
    }));
    const completedNavigationRequests = requests.filter((entry) =>
      entry.includes("/model-hub/annotation-queues/queue-completed/items/"),
    );
    assert(
      completedNavigationRequests.every(
        (entry) => !entry.includes("include_completed"),
      ),
      `Frontend sent include_completed instead of relying on completed-queue backend defaults: ${completedNavigationRequests.join("; ")}`,
    );
    assert(
      evidence.nextItem?.id === "item-1" &&
        evidence.detail?.nextItemId === "item-2" &&
        evidence.navigatedTo === "item-2",
      `Unexpected completed navigation evidence: ${JSON.stringify(evidence)}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          screenshot: SCREENSHOT_PATH,
          requests: completedNavigationRequests,
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
    <title>Annotation Completed Navigation Smoke</title>
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
        width: 520px;
        background: #fff;
        border: 1px solid #dfe3e8;
      }
      .body {
        padding: 20px;
      }
    </style>
    <script>
      window.__FUTURE_AGI_CONFIG__ = { VITE_HOST_API: window.location.origin };
      window.__nextItem = null;
      window.__detailSummary = null;
      window.__navigatedTo = null;
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";
      import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
      import { useAnnotateDetail, useNextItem } from "/src/api/annotation-queues/annotation-queues.js";
      import AnnotateFooter from "/src/sections/annotations/queues/annotate/annotate-footer.jsx";

      const queryClient = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      });

      function Harness() {
        const next = useNextItem("queue-completed");
        const detail = useAnnotateDetail("queue-completed", next.data?.id, {
          enabled: Boolean(next.data?.id),
        });
        React.useEffect(() => {
          window.__nextItem = next.data || null;
        }, [next.data]);
        React.useEffect(() => {
          window.__detailSummary = detail.data
            ? {
                itemId: detail.data.item?.id,
                queueStatus: detail.data.queue?.status,
                nextItemId: detail.data.next_item_id,
              }
            : null;
        }, [detail.data]);

        const loaded = next.data && detail.data;
        return React.createElement(
          "div",
          { className: "surface" },
          React.createElement(
            "div",
            { className: "body" },
            loaded
              ? React.createElement(
                  React.Fragment,
                  null,
                  React.createElement(
                    "strong",
                    null,
                    "Completed item " + detail.data.item.id,
                  ),
                  React.createElement(
                    "p",
                    null,
                    "Next item " + detail.data.next_item_id,
                  ),
                )
              : "Loading",
          ),
          loaded &&
            React.createElement(AnnotateFooter, {
              currentPosition: detail.data.progress.current_position,
              total: detail.data.progress.total,
              hasPrev: Boolean(detail.data.prev_item_id),
              hasNext: Boolean(detail.data.next_item_id),
              onPrev: () => {},
              onNext: () => {
                window.__navigatedTo = detail.data.next_item_id;
              },
            }),
        );
      }

      createRoot(document.getElementById("root")).render(
        React.createElement(
          QueryClientProvider,
          { client: queryClient },
          React.createElement(Harness),
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
