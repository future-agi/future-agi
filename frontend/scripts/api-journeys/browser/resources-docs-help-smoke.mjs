import { createRequire } from "node:module";
import process from "node:process";
import {
  assert,
  createAuthenticatedContext,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/resources-docs-help-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const apiFailures = [];
  const pageErrors = [];
  const unexpectedMutations = [];
  const evidence = {};

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });

  const page = await browser.newPage();
  await installRuntimeConfig(page, auth);
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      localStorage.setItem(
        "settings",
        JSON.stringify({
          themeMode: "system",
          themeDirection: "ltr",
          themeContrast: "default",
          themeLayout: "vertical",
          themeColorPresets: "purple",
          themeStretch: false,
        }),
      );
      if (organizationId) sessionStorage.setItem("organizationId", organizationId);
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id) sessionStorage.setItem("futureagi-current-user-id", user.id);
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );

  page.on("request", (request) => {
    const url = request.url();
    if (
      isLocalApiUrl(url, auth.apiBase) &&
      ["POST", "PATCH", "PUT", "DELETE"].includes(request.method())
    ) {
      unexpectedMutations.push(`${request.method()} ${url}`);
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (isLocalApiUrl(url, auth.apiBase) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await page.goto(`${APP_BASE}/dashboard/get-started`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/get-started",
      { timeout: 30000 },
    );
    await waitForVisibleText(page, "Get Started with FutureAGI", { exact: true });
    await waitForVisibleText(page, "Resources", { exact: true });

    const docsLink = await findVisibleSidebarExternalLink(page, "Docs");
    assert(
      docsLink.href === "https://docs.futureagi.com/",
      `Docs href mismatch: ${docsLink.href}`,
    );
    assert(docsLink.target === "_blank", "Docs link does not open a new tab.");
    const docsTargetUrl = await clickExternalLink(browser, page, docsLink);
    assert(
      docsTargetUrl.startsWith("https://docs.futureagi.com"),
      `Docs tab opened unexpected URL: ${docsTargetUrl}`,
    );
    await assertOriginalSessionStillUsable(page);

    const helpLink = await findVisibleSidebarExternalLink(page, "Help");
    assert(helpLink.href.startsWith("http"), `Help href is not external: ${helpLink.href}`);
    assert(helpLink.target === "_blank", "Help link does not open a new tab.");
    const helpTargetUrl = await clickExternalLink(browser, page, helpLink);
    assert(
      helpTargetUrl.startsWith(helpLink.href),
      `Help tab opened unexpected URL: ${helpTargetUrl}; expected ${helpLink.href}`,
    );
    await assertOriginalSessionStillUsable(page);
    await assertNoVisibleOverlay(page);
    await waitForNoVisibleText(page, "Invalid Date");

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.docs_href = docsLink.href;
    evidence.docs_target_url = docsTargetUrl;
    evidence.help_href = helpLink.href;
    evidence.help_target_url = helpTargetUrl;
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Resources smoke fired local API mutations: ${unexpectedMutations.join("; ")}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
        },
        null,
        2,
      ),
    );
  } finally {
    await browser.close();
  }
}

async function installRuntimeConfig(page, auth) {
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/config.js") {
      request.respond({
        status: 200,
        contentType: "application/javascript",
        body: `window.__FUTURE_AGI_CONFIG__ = ${JSON.stringify({
          VITE_HOST_API: auth.apiBase,
          VITE_ASSETS_API: APP_BASE,
        })};`,
      });
      return;
    }
    request.continue();
  });
}

async function findVisibleSidebarExternalLink(page, label) {
  const links = await page.$$eval("a", (anchors, expectedLabel) => {
    const isVisible = (element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    };

    return anchors
      .filter((anchor) => isVisible(anchor))
      .map((anchor, index) => {
        const rect = anchor.getBoundingClientRect();
        return {
          index,
          text: String(anchor.textContent || "").replace(/\s+/g, " ").trim(),
          href: anchor.href,
          target: anchor.target,
          x: rect.x,
          y: rect.y,
          width: rect.width,
          height: rect.height,
        };
      })
      .filter(
        (link) =>
          link.text === expectedLabel &&
          link.href &&
          link.target === "_blank" &&
          link.x >= 0 &&
          link.x < 320,
      );
  }, label);

  assert(links.length > 0, `No visible sidebar external link found for ${label}.`);
  return links[0];
}

async function clickExternalLink(browser, page, link) {
  const existingTargets = new Set(browser.targets());
  const targetPromise = browser.waitForTarget(
    (target) => target.type() === "page" && !existingTargets.has(target),
    { timeout: 15000 },
  );
  await page.mouse.click(link.x + link.width / 2, link.y + link.height / 2);
  const target = await targetPromise;
  const newPage = await target.page();
  await newPage
    .waitForFunction(() => window.location.href !== "about:blank", {
      timeout: 15000,
    })
    .catch(() => null);
  const targetUrl = newPage.url();
  await newPage.close().catch(() => null);
  await page.bringToFront();
  return targetUrl;
}

async function assertOriginalSessionStillUsable(page) {
  await page.waitForFunction(
    () => window.location.pathname === "/dashboard/get-started",
    { timeout: 30000 },
  );
  await waitForVisibleText(page, "Get Started with FutureAGI", { exact: true });
  await waitForVisibleText(page, "Resources", { exact: true });
}

async function assertNoVisibleOverlay(page) {
  const overlays = await page.$$eval(
    '[role="dialog"], .MuiBackdrop-root, .MuiPopover-root',
    (elements) => {
      const isVisible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      };
      return elements
        .filter((element) => isVisible(element))
        .map((element) => ({
          role: element.getAttribute("role") || "",
          className: element.className || "",
          text: String(element.textContent || "").replace(/\s+/g, " ").trim(),
        }));
    },
  );
  assert(overlays.length === 0, `Visible overlay remained: ${JSON.stringify(overlays)}`);
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
      const isVisible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      };
      return Array.from(document.querySelectorAll("body *")).some((element) => {
        if (!isVisible(element)) return false;
        const textContent = normalized(element.textContent);
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function waitForNoVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
      const isVisible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      };
      return !Array.from(document.querySelectorAll("body *")).some((element) => {
        if (!isVisible(element)) return false;
        const textContent = normalized(element.textContent);
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

function isLocalApiUrl(rawUrl, apiBase) {
  try {
    return new URL(rawUrl).origin === new URL(apiBase).origin;
  } catch {
    return false;
  }
}

function browserExecutablePath() {
  return (
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  );
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
