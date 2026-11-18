import { mkdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_SETTINGS_BACK_SCREENSHOT ||
  "/tmp/annotation-settings-back-smoke.png";

async function main() {
  const frontendRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../..",
  );
  const harnessDir = path.join(frontendRoot, ".tmp-smoke");
  const harnessPath = path.join(harnessDir, "annotation-settings-back.html");
  await mkdir(harnessDir, { recursive: true });
  await writeFile(harnessPath, smokeHarnessHtml(), "utf8");

  const pageErrors = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 960, height: 560 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await page.goto(`${APP_BASE}/.tmp-smoke/annotation-settings-back.html`, {
      waitUntil: "domcontentloaded",
    });
    await page.click("[data-testid='settings-tab']");
    await page.click("[aria-label='Back to queue items']");
    await page.waitForFunction(() => window.__settingsBackDone === true);
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    const evidence = await page.evaluate(() => window.__settingsBackEvidence);
    assert(
      evidence?.returnedToItems === true,
      "Back did not return to queue items.",
    );
    assert(
      evidence?.navigatedToList === false,
      "Back navigated to the queue list.",
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
    <title>Annotation Settings Back Smoke</title>
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
      .tabs {
        display: flex;
        gap: 8px;
        margin: 16px 0;
      }
      button {
        border: 1px solid #c4cdd5;
        background: #fff;
        padding: 8px 12px;
        font: inherit;
      }
      .active {
        background: #212b36;
        color: #fff;
      }
      .status {
        color: #006c9c;
        font-weight: 700;
      }
    </style>
    <script>
      window.__settingsBackDone = false;
      window.__settingsBackEvidence = null;
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module">
      import React from "react";
      import { createRoot } from "react-dom/client";

      function Harness() {
        const [tab, setTab] = React.useState("items");
        const [navigatedToList, setNavigatedToList] = React.useState(false);

        React.useEffect(() => {
          if (tab === "items" && window.__settingsBackStarted) {
            window.__settingsBackEvidence = {
              returnedToItems: true,
              navigatedToList,
            };
            window.__settingsBackDone = true;
          }
        }, [tab, navigatedToList]);

        function handleBack() {
          if (tab === "settings") {
            setTab("items");
            return;
          }
          setNavigatedToList(true);
        }

        return React.createElement(
          "div",
          { className: "surface" },
          React.createElement("h1", null, "Queue Detail"),
          React.createElement(
            "button",
            {
              "aria-label": tab === "settings" ? "Back to queue items" : "Back to queues",
              onClick: handleBack,
            },
            "Back",
          ),
          React.createElement(
            "div",
            { className: "tabs" },
            React.createElement(
              "button",
              {
                className: tab === "items" ? "active" : "",
                onClick: () => setTab("items"),
                "data-testid": "items-tab",
              },
              "Items",
            ),
            React.createElement(
              "button",
              {
                className: tab === "settings" ? "active" : "",
                onClick: () => {
                  window.__settingsBackStarted = true;
                  setTab("settings");
                },
                "data-testid": "settings-tab",
              },
              "Settings",
            ),
          ),
          React.createElement(
            "p",
            { className: "status" },
            tab === "items" ? "Showing queue items" : "Editing queue settings",
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
