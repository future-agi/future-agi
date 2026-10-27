import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_SIMULATION_CONTENT_SCREENSHOT ||
  "/tmp/annotation-simulation-content-smoke.png";

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "annotation-simulation-content-smoke.html",
  );
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const apiRequests = [];
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
      url.pathname.includes("/simulate/call-executions/") ||
      url.pathname.includes("/tracer/saved-views/")
    ) {
      apiRequests.push({
        method: request.method(),
        pathname: url.pathname,
        query: url.search,
      });
    }
    if (url.pathname.endsWith("/simulate/call-executions/call-1/")) {
      return request.respond(jsonResponse(200, callExecutionResponse()));
    }
    if (url.pathname.endsWith("/tracer/saved-views/")) {
      return request.respond(
        jsonResponse(200, {
          status: true,
          result: { custom_views: [] },
        }),
      );
    }
    return request.continue();
  });

  try {
    await page.goto(
      `${APP_BASE}/.tmp-smoke/annotation-simulation-content-smoke.html`,
      { waitUntil: "domcontentloaded" },
    );
    await page.waitForSelector('[role="tab"]');
    await page.waitForFunction(() =>
      document.body.textContent.includes("Call Analytics"),
    );

    const hasLegacyFallback = await page.evaluate(() =>
      document.body.textContent.includes("Input"),
    );
    const hasAnnotationsTab = await page.evaluate(() =>
      Array.from(document.querySelectorAll('[role="tab"]')).some(
        (tab) => tab.textContent.trim() === "Annotations",
      ),
    );
    assert(!hasLegacyFallback, "Rendered the legacy call_execution fallback UI.");
    assert(!hasAnnotationsTab, "Embedded simulation drawer exposed Annotations tab.");

    const scenarioTab = await findTabByText(page, "Scenario");
    assert(scenarioTab, "Scenario tab was not rendered for simulation content.");
    await scenarioTab.click();
    await page.waitForFunction(() =>
      document.body.textContent.includes("Greet the customer"),
    );
    await page.waitForSelector('input[placeholder="Search scenario"]');

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      apiRequests.some((entry) =>
        entry.pathname.endsWith("/simulate/call-executions/call-1/"),
      ),
      `Simulation detail request was not made: ${JSON.stringify(apiRequests)}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          screenshot: SCREENSHOT_PATH,
          apiRequests,
          evidence: {
            visibleTabs: await visibleTabLabels(page),
            scenario: "Greet the customer",
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
    <title>Annotation Simulation Content Smoke</title>
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
      import ContentPanel from "/src/sections/annotations/queues/annotate/content-panel.jsx";

      const queryClient = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
        },
      });
      const item = {
        source_type: "call_execution",
        source_content: {
          call_id: "call-1",
          project_id: "project-1",
        },
      };

      createRoot(document.getElementById("root")).render(
        React.createElement(
          QueryClientProvider,
          { client: queryClient },
          React.createElement(ContentPanel, { item }),
        ),
      );
    </script>
  </body>
</html>`;
}

function callExecutionResponse() {
  return {
    id: "call-1",
    test_execution_id: "test-execution-1",
    trace_id: "trace-1",
    project_id: "project-1",
    status: "completed",
    simulation_call_type: "voice",
    call_type: "outbound",
    duration_seconds: 42,
    scenario: "Greet the customer",
    scenario_id: "scenario-1",
    scenario_columns: {
      persona: {
        column_name: "persona",
        value: "Impatient customer",
      },
    },
    transcript: [
      {
        speakerRole: "user",
        message: "Hello, I need help with my order.",
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
    customer_latency_metrics: { system_metrics: {} },
    customer_cost_breakdown: {},
    call_summary: "The agent greeted the customer and offered help.",
    provider: "vapi",
    recording_url: "",
    observation_span: [],
    turn_count: 2,
    talk_ratio: 1,
    agent_talk_percentage: 50,
    avg_agent_latency_ms: 250,
    user_wpm: 120,
    bot_wpm: 110,
    user_interruption_count: 0,
    ai_interruption_count: 0,
  };
}

async function findTabByText(page, label) {
  const handles = await page.$$('[role="tab"]');
  for (const handle of handles) {
    const text = await handle.evaluate((node) => node.textContent.trim());
    if (text === label) return handle;
    await handle.dispose();
  }
  return null;
}

async function visibleTabLabels(page) {
  return page.$$eval('[role="tab"]', (tabs) =>
    tabs.map((tab) => tab.textContent.trim()).filter(Boolean),
  );
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
