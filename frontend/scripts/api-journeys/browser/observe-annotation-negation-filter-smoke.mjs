import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.OBSERVE_ANNOTATION_NEGATION_SCREENSHOT ||
  "/tmp/observe-annotation-negation-filter-smoke.png";

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(
    harnessDir,
    "observe-annotation-negation-filter-smoke.html",
  );
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 980, height: 560 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await page.goto(
      `${APP_BASE}/.tmp-smoke/observe-annotation-negation-filter-smoke.html`,
      { waitUntil: "domcontentloaded" },
    );
    await page.waitForFunction(() =>
      document.body.textContent.includes("Quality Score"),
    );
    await page.waitForFunction(() =>
      Array.from(document.querySelectorAll('[role="combobox"]')).some(
        (node) => node.textContent?.trim() === "not equals",
      ),
    );
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });
    await clickByText(page, "Apply");
    await page.waitForFunction(
      () => window.__appliedFilters?.[0]?.operator === "not_equals",
    );

    const evidence = await page.evaluate(() => window.__appliedFilters?.[0]);
    assert(
      evidence?.field === "quality-score-label" &&
        evidence?.fieldCategory === "annotation" &&
        evidence?.apiColType === "ANNOTATION" &&
        evidence?.fieldType === "number" &&
        evidence?.operator === "not_equals" &&
        String(evidence?.value) === "45",
      `Expected annotation not_equals filter, got ${JSON.stringify(evidence)}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          screenshot: SCREENSHOT_PATH,
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
    <title>Observe Annotation Negation Filter Smoke</title>
    <style>
      body {
        margin: 0;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #f7f8fa;
      }
      #root {
        padding: 32px;
      }
      .anchor {
        margin-bottom: 8px;
      }
    </style>
    <script>
      window.__FUTURE_AGI_CONFIG__ = { VITE_HOST_API: window.location.origin };
      window.__appliedFilters = [];
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";
      import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
      import TraceFilterPanel from "/src/sections/projects/LLMTracing/TraceFilterPanel.jsx";

      const queryClient = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      });

      function Harness() {
        const anchorRef = React.useRef(null);
        const [anchorEl, setAnchorEl] = React.useState(null);
        React.useEffect(() => {
          setAnchorEl(anchorRef.current);
        }, []);
        return React.createElement(
          React.Fragment,
          null,
          React.createElement(
            "button",
            { className: "anchor", ref: anchorRef, type: "button" },
            "Filter",
          ),
          React.createElement(TraceFilterPanel, {
            anchorEl,
            open: Boolean(anchorEl),
            onClose: () => {},
            showAi: false,
            showQueryTab: false,
            projectId: "11111111-1111-4111-8111-111111111111",
            properties: [
              {
                id: "quality-score-label",
                name: "Quality Score",
                category: "annotation",
                type: "number",
                apiColType: "ANNOTATION",
              },
            ],
            currentFilters: [
              {
                field: "quality-score-label",
                fieldName: "Quality Score",
                fieldCategory: "annotation",
                apiColType: "ANNOTATION",
                fieldType: "number",
                operator: "not_equals",
                value: "45",
              },
            ],
            onApply: (nextFilters) => {
              window.__appliedFilters = nextFilters;
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

async function clickByText(page, text) {
  const clicked = await page.evaluate((needle) => {
    const elements = Array.from(
      document.querySelectorAll("button, [role='button'], li, span, p, div"),
    ).filter((element) => {
      if (element.id === "root") return false;
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return (
        rect.width > 0 &&
        rect.height > 0 &&
        style.visibility !== "hidden" &&
        style.display !== "none"
      );
    });
    const target = elements.find(
      (element) => element.textContent?.trim() === needle,
    );
    if (!target) return false;
    target.click();
    return true;
  }, text);
  if (!clicked) throw new Error(`Could not find text: ${text}`);
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
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
  return "google-chrome";
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
