import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.VOICE_PATH_ANALYSIS_SCREENSHOT ||
  "/tmp/voice-path-analysis-smoke.png";

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(harnessDir, "voice-path-analysis-smoke.html");
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const branchAnalysisCalls = [];
  const kpiCalls = [];
  const simulateRequests = [];
  const pageErrors = [];

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1120, height: 820 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = request.url();
    if (url.includes("/simulate/")) simulateRequests.push(url);
    if (url.includes("/simulate/call-executions/call-1/branch-analysis/")) {
      if (request.method() === "OPTIONS") {
        request.respond({
          status: 204,
          headers: corsHeaders(),
        });
        return;
      }

      branchAnalysisCalls.push(url);
      request.respond({
        status: 200,
        contentType: "application/json",
        headers: corsHeaders(),
        body: JSON.stringify({
          analysis: {
            current_path: ["Greeting"],
            expected_path: ["Greeting", "Confirm order"],
            new_nodes: [],
            new_edges: [],
            analysis_summary:
              "Greeting was completed; confirmation was missed.",
          },
        }),
      });
      return;
    }

    if (url.includes("/simulate/test-executions/test-execution-1/kpis/")) {
      if (request.method() === "OPTIONS") {
        request.respond({
          status: 204,
          headers: corsHeaders(),
        });
        return;
      }

      kpiCalls.push(url);
      request.respond({
        status: 500,
        contentType: "application/json",
        headers: corsHeaders(),
        body: JSON.stringify({ detail: "KPI cache should not be required" }),
      });
      return;
    }

    request.continue();
  });

  try {
    await page.goto(`${APP_BASE}/.tmp-smoke/voice-path-analysis-smoke.html`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(() =>
      document.body.textContent.includes("1/2 steps"),
    );
    await page.waitForFunction(() =>
      document.body.textContent.includes("Confirm order"),
    );
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    assert(
      branchAnalysisCalls.length === 1,
      `Expected one branch-analysis request, got ${branchAnalysisCalls.length}`,
    );
    assert(
      kpiCalls.length === 0,
      `Scenario graph came from backend detail; KPI calls were unexpected: ${kpiCalls.join(
        ", ",
      )}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          screenshot: SCREENSHOT_PATH,
          evidence: {
            branch_analysis_calls: branchAnalysisCalls,
            kpi_calls: kpiCalls,
            simulate_requests: simulateRequests,
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
    console.error(
      JSON.stringify(
        {
          branch_analysis_calls: branchAnalysisCalls,
          kpi_calls: kpiCalls,
          simulate_requests: simulateRequests,
          page_errors: pageErrors,
        },
        null,
        2,
      ),
    );
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
    <title>Voice Path Analysis Smoke</title>
    <style>
      body {
        margin: 0;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #f6f7f9;
      }
      #root {
        max-width: 720px;
        margin: 24px auto;
        padding: 16px;
        background: #fff;
        border: 1px solid #e4e7ec;
      }
    </style>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";
      import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
      import PathAnalysisView from "/src/components/VoiceDetailDrawerV2/PathAnalysisView.jsx";

      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });

      createRoot(document.getElementById("root")).render(
        React.createElement(
          QueryClientProvider,
          { client: queryClient },
          React.createElement(PathAnalysisView, {
            data: {
              transcript: [
                {
                  role: "assistant",
                  content: "Hello, welcome. What would you like to order?",
                  start: 0,
                  end: 3,
                },
              ],
              scenario_graph: {
                nodes: [
                  {
                    name: "Greeting",
                    type: "conversation",
                    messagePlan: {
                      firstMessage: "Hello, what would you like to order?",
                    },
                  },
                  {
                    name: "Confirm order",
                    type: "conversation",
                    messagePlan: {
                      firstMessage: "Let me confirm your order.",
                    },
                  },
                ],
                edges: [{ from: "Greeting", to: "Confirm order" }],
              },
            },
            scenarioId: "scenario-1",
            openedExecutionId: "call-1",
            testExecutionId: "test-execution-1",
            enabled: true,
            viewMode: "checklist",
          }),
        ),
      );
    </script>
  </body>
</html>
`;
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
  };
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  }
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  return "/usr/bin/google-chrome";
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
