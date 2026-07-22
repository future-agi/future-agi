import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const GRID_SCREENSHOT_PATH = "/tmp/eval-groups-grid-smoke.png";
const DETAIL_SCREENSHOT_PATH = "/tmp/eval-groups-detail-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/eval-groups-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const template = await resolveEvalTemplate(auth.client);
  const groupName = `ui eval group ${shortRunId(auth.runId)}`;
  const groupDescription = `Eval group browser smoke ${auth.runId}`;
  let groupId = null;
  let browser = null;
  let caughtError = null;

  const apiFailures = [];
  const pageErrors = [];
  const evidence = {
    template_id: template.id,
    template_name: template.name,
    group_name: groupName,
  };

  try {
    const created = await auth.client.post(apiPath("/model-hub/eval-groups/"), {
      name: groupName,
      description: groupDescription,
      eval_template_ids: [template.id],
    });
    groupId = created?.id;
    assert(isUuid(groupId), "Eval group create did not return a UUID id.");
    evidence.group_id = groupId;
    evidence.required_keys = asArray(created?.required_keys);

    browser = await puppeteer.launch({
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
        if (organizationId)
          sessionStorage.setItem("organizationId", organizationId);
        if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
        if (user?.id)
          sessionStorage.setItem("futureagi-current-user-id", user.id);
      },
      {
        tokens: auth.tokens,
        organizationId: auth.organizationId,
        workspaceId: auth.workspaceId,
        user: auth.user,
      },
    );
    page.on("response", (response) => {
      const url = response.url();
      if (url.includes("/model-hub/eval-groups/") && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto(`${APP_BASE}/dashboard/evaluations/groups`, {
      waitUntil: "domcontentloaded",
    });
    await waitForVisibleText(page, "Groups (");

    const searchResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/model-hub/eval-groups/") &&
        new URL(response.url()).searchParams.get("name") === groupName &&
        response.status() < 400,
      { timeout: 5000 },
    ).catch(() => null);
    await fillFirstInputByPlaceholder(page, "Search", groupName);

    await waitForVisibleText(page, groupName, { exact: true });
    await waitForVisibleText(page, "Evaluations: 1", { exact: true });
    await waitForVisibleText(page, "Required Columns:");
    const searchResultResponse = await searchResponse;
    if (searchResultResponse) {
      evidence.search_response = summarizeEvalGroupsListResponse(
        await readJsonResponse(searchResultResponse),
      );
    }

    await page.screenshot({ path: GRID_SCREENSHOT_PATH, fullPage: true });
    evidence.grid_screenshot = GRID_SCREENSHOT_PATH;

    await clickVisibleText(page, groupName, { exact: true });
    await page.waitForFunction(
      (id) => window.location.pathname.endsWith(`/dashboard/evaluations/groups/${id}`),
      { timeout: 30000 },
      groupId,
    );

    await waitForVisibleText(page, `Group Name : ${groupName}`, {
      exact: true,
      timeout: 30000,
    });
    await waitForVisibleText(page, groupDescription, {
      exact: true,
      timeout: 30000,
    });
    await waitForVisibleText(page, "Edit List", { exact: true });
    await waitForVisibleText(page, template.name, { exact: true });
    await waitForVisibleText(page, "All (1)", { exact: true });
    await waitForNoVisibleText(page, "Invalid Date", { exact: true });

    await page.screenshot({ path: DETAIL_SCREENSHOT_PATH, fullPage: true });
    evidence.detail_screenshot = DETAIL_SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

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
  } catch (error) {
    caughtError = error;
    console.error(
      JSON.stringify(
        {
          status: "failed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
          api_failures: apiFailures,
          page_errors: pageErrors,
        },
        null,
        2,
      ),
    );
    if (browser) {
      const pages = await browser.pages();
      const page = pages[pages.length - 1];
      await page
        ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
    }
  } finally {
    if (browser) await browser.close();
    if (groupId) {
      await auth.client
        .delete(apiPath("/model-hub/eval-groups/{id}/", { id: groupId }), {
          okStatuses: [200, 204, 404],
        })
        .catch((error) => {
          if (!caughtError) caughtError = error;
          else
            caughtError.message = `${caughtError.message}; cleanup failed: ${error.message}`;
        });
    }
  }

  if (caughtError) throw caughtError;
}

async function resolveEvalTemplate(client) {
  const payload = await client.post(apiPath("/model-hub/eval-templates/list/"), {
    page: 0,
    page_size: 50,
    owner_filter: "all",
    sort_by: "updated_at",
    sort_order: "desc",
  });
  const templates = asArray(payload?.items || payload).filter((template) =>
    isUuid(template?.id),
  );
  const template = templates.find((item) => templateName(item));
  if (!template) {
    throw new Error("No eval template with a visible name was available.");
  }
  return {
    id: template.id,
    name: templateName(template),
  };
}

function templateName(template) {
  return (
    template?.name ||
    template?.eval_template_name ||
    template?.template_name ||
    template?.label ||
    ""
  );
}

function shortRunId(runId) {
  return String(runId || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .slice(-8);
}

async function readJsonResponse(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function summarizeEvalGroupsListResponse(payload) {
  const result = payload?.result || payload || {};
  const rows = asArray(result?.data);
  return {
    status: payload?.status ?? null,
    total_count: result?.total_count ?? null,
    total_pages: result?.total_pages ?? null,
    rows: rows.map((row) => ({
      id: row?.id,
      name: row?.name,
      evals_count: row?.evals_count,
      required_keys: row?.required_keys,
    })),
  };
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

async function fillFirstInputByPlaceholder(page, placeholder, value) {
  const selector = `input[placeholder="${cssString(placeholder)}"]`;
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.type(selector, value);
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
      return !Array.from(document.querySelectorAll("body *")).some(
        (element) => {
          if (!isVisible(element)) return false;
          const textContent = normalized(element.textContent);
          if (exactMatch) return textContent === expectedText;
          return textContent.includes(expectedText);
        },
      );
    },
    { timeout },
    { text, exact },
  );
}

async function clickVisibleText(page, text, { exact = false } = {}) {
  const target = await page.evaluate(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
      const isVisible = (candidate) => {
        const style = window.getComputedStyle(candidate);
        const rect = candidate.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      };
      const element = Array.from(document.querySelectorAll("body *")).find(
        (candidate) => {
          if (!isVisible(candidate)) return false;
          const textContent = normalized(candidate.textContent);
          if (exactMatch) return textContent === expectedText;
          return textContent.includes(expectedText);
        },
      );
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return {
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
      };
    },
    { text, exact },
  );
  assert(target, `Unable to click visible text: ${text}`);
  await page.mouse.click(target.x, target.y);
}

function cssString(value) {
  return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
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
