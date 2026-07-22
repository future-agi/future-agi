/* eslint-disable no-console */
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const PROJECT_PREFIX = "ui_prototype_browser_";
const ADD_SCREENSHOT_PATH = "/tmp/prototype-add-drawer-smoke.png";
const DELETE_SCREENSHOT_PATH = "/tmp/prototype-project-delete-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/prototype-project-delete-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const projectName = `${PROJECT_PREFIX}${suffix}`;
  const apiFailures = [];
  const pageErrors = [];
  const prototypeRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  const cleanupEvidence = [];
  let browser = null;
  let page = null;
  let projectId = null;

  await cleanupProjectsByPrefix(auth.client, PROJECT_PREFIX, cleanupEvidence);

  try {
    const created = await createPrototypeProject(auth.client, projectName);
    projectId = created.project_id || created.projectId;
    assert(
      projectId,
      `Prototype project create omitted id: ${JSON.stringify(created)}`,
    );

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });
    page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installBrowserState(page, auth);

    page.on("request", (request) => {
      const url = request.url();
      if (!isPrototypeApiUrl(url)) return;
      const requestKey = `${request.method()} ${url}`;
      prototypeRequests.push(requestKey);
      if (MUTATION_METHODS.has(request.method())) {
        browserMutations.push(requestKey);
        if (!isAllowedPrototypeMutation(request.method(), url)) {
          unexpectedMutations.push(requestKey);
        }
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isPrototypeApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponsesDuring(
      page,
      "prototype project list load",
      [
        (response) =>
          isProjectListResponse(response) &&
          response.url().includes("project_type=experiment") &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/prototype`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/prototype");
    await waitForVisibleText(page, "Prototype", { exact: true });
    await waitForVisibleText(page, "Add Prototype", { exact: true });

    const sdkCodeResponse = await waitForResponseDuring(
      page,
      "prototype add drawer SDK code",
      (response) =>
        response.url().includes("/tracer/project/project_sdk_code/") &&
        response.url().includes("project_type=experiment") &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () => clickVisibleButton(page, "Add Prototype"),
    );
    const sdkCodePayload = await responseJson(sdkCodeResponse);
    await waitForVisibleText(page, "New Projects", { exact: true });
    await waitForVisibleText(page, "Install Dependencies", { exact: true });
    await waitForVisibleText(page, "Load API keys", { exact: true });
    await waitForVisibleText(page, "Setup Telemetry", { exact: true });
    await assertPrototypeDrawerHasPlaceholders(page);
    assert(
      !browserMutations.some((request) => request.includes("/tracer/project/")),
      `Add Prototype unexpectedly fired a project mutation: ${browserMutations
        .map(maskRequest)
        .join(", ")}`,
    );
    await page.screenshot({ path: ADD_SCREENSHOT_PATH, fullPage: true });

    await closeDrawer(page);
    await waitForNoVisibleExactText(page, "New Projects");

    const searchResponse = waitForResponseDuring(
      page,
      "search disposable prototype project",
      (response) => {
        if (!isProjectListResponse(response) || response.status() >= 400) {
          return false;
        }
        const url = new URL(response.url());
        return url.searchParams.get("name") === projectName;
      },
      () => typeIntoSearchInput(page, projectName),
    );
    await searchResponse;
    await waitForVisibleText(page, projectName, { exact: true });
    await selectProjectRow(page, projectName);
    await waitForVisibleText(page, "1 Selected", { exact: true });
    await clickVisibleButton(page, "Delete");
    await waitForVisibleText(page, "Delete Project", { exact: true });

    const deleteResponse = await waitForResponseDuring(
      page,
      "delete disposable prototype project",
      (response) =>
        response.url().includes("/tracer/project/") &&
        response.request().method() === "DELETE",
      () => clickDialogAction(page, "Delete"),
    );
    const deletePayload = await responseJson(deleteResponse);
    assert(
      deleteResponse.status() >= 200 && deleteResponse.status() < 300,
      `Prototype project delete returned HTTP ${deleteResponse.status()}: ${JSON.stringify(
        deletePayload,
      )}`,
    );
    await waitForNoVisibleExactText(page, projectName);

    const deletedDetail = await expectDeletedProjectDetail(
      auth.client,
      projectId,
    );
    assert(
      deletedDetail?.message === "Project Not Found" ||
        deletedDetail?.detail === "Project Not Found",
      `Deleted prototype project detail did not return Project Not Found: ${JSON.stringify(
        deletedDetail,
      )}`,
    );
    await cleanupProjectsByPrefix(auth.client, PROJECT_PREFIX, cleanupEvidence);

    await page.screenshot({ path: DELETE_SCREENSHOT_PATH, fullPage: true });

    assert(
      unexpectedMutations.length === 0,
      `Unexpected prototype mutations: ${unexpectedMutations
        .map(maskRequest)
        .join(", ")}`,
    );
    assert(
      !apiFailures.some((failure) => !failure.startsWith("404 ")),
      `Prototype API failures: ${apiFailures.join("; ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    const finalProjectId = projectId;
    projectId = null;
    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          project_id: finalProjectId,
          project_name: projectName,
          add_prototype_behavior:
            "opens setup SDK drawer; no browser project create mutation",
          sdk_sections_present: [
            "Install Dependencies",
            "Load API keys",
            "Setup Telemetry",
          ],
          sdk_response_sections: Object.keys(
            sdkCodePayload?.result || sdkCodePayload || {},
          ),
          browser_mutations: browserMutations.map(maskRequest),
          prototype_request_count: prototypeRequests.length,
          screenshots: [ADD_SCREENSHOT_PATH, DELETE_SCREENSHOT_PATH],
          cleanup: cleanupEvidence,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    if (page) {
      await page.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true });
      console.error(`failure_screenshot=${FAILURE_SCREENSHOT_PATH}`);
    }
    throw error;
  } finally {
    if (projectId) {
      await deleteProjects(auth.client, [projectId], cleanupEvidence).catch(
        (error) => {
          cleanupEvidence.push({
            cleanup: "delete prototype project after failure",
            status: "failed",
            error: error.message,
          });
        },
      );
    }
    await cleanupProjectsByPrefix(auth.client, PROJECT_PREFIX, cleanupEvidence);
    if (browser) await browser.close();
  }
}

async function createPrototypeProject(client, name) {
  return client.post(apiPath("/tracer/project/"), {
    name,
    model_type: "GenerativeLLM",
    trace_type: "experiment",
  });
}

async function cleanupProjectsByPrefix(client, prefix, evidence) {
  const listPayload = await client.get(apiPath("/tracer/project/"), {
    query: {
      project_type: "experiment",
      page_number: 0,
      page_size: 100,
      sort_by: "created_at",
      sort_direction: "desc",
    },
  });
  const projects = asArray(listPayload?.projects || listPayload).filter(
    (project) => String(project?.name || "").startsWith(prefix),
  );
  const ids = projects.map((project) => project.id).filter(Boolean);
  if (ids.length) {
    await deleteProjects(client, ids, evidence);
  }
}

async function deleteProjects(client, projectIds, evidence) {
  if (!projectIds.length) return;
  await client.delete(apiPath("/tracer/project/"), {
    body: {
      project_ids: projectIds,
      project_type: "experiment",
    },
    okStatuses: [200, 400, 404],
  });
  evidence.push({
    cleanup: "delete prototype project",
    status: "passed",
    project_ids: projectIds,
  });
}

async function expectDeletedProjectDetail(client, projectId) {
  try {
    const detail = await client.get(
      apiPath("/tracer/project/{id}/", { id: projectId }),
      { okStatuses: [400, 404] },
    );
    return detail;
  } catch (error) {
    if (
      [400, 404].includes(error?.status) &&
      (error?.body?.message === "Project Not Found" ||
        error?.body?.detail === "Project Not Found")
    ) {
      return error.body;
    }
    throw error;
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

async function installBrowserState(page, auth) {
  await page.evaluateOnNewDocument(() => {
    window.normalizeText = (value) =>
      String(value || "")
        .replace(/\s+/g, " ")
        .trim();
    window.visibleElements = (selector = "body *") => {
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
      return Array.from(document.querySelectorAll(selector)).filter(isVisible);
    };
  });
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      if (organizationId) {
        sessionStorage.setItem("organizationId", organizationId);
      }
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id) {
        sessionStorage.setItem("futureagi-current-user-id", user.id);
      }
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );
}

async function waitForResponseDuring(page, label, predicate, action) {
  try {
    const [response] = await Promise.all([
      page.waitForResponse(predicate, { timeout: 60000 }),
      action(),
    ]);
    return response;
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
}

async function waitForResponsesDuring(page, label, predicates, action) {
  try {
    return await Promise.all([
      ...predicates.map((predicate) =>
        page.waitForResponse(predicate, { timeout: 60000 }),
      ),
      action(),
    ]);
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
}

async function waitForPath(page, pathname, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname === expectedPath,
    { timeout },
    pathname,
  );
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) =>
      window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      }),
    { timeout },
    { text, exact },
  );
}

async function waitForNoVisibleExactText(page, text, timeout = 30000) {
  await page.waitForFunction(
    (expectedText) =>
      !window
        .visibleElements()
        .some(
          (element) =>
            window.normalizeText(element.textContent) === expectedText,
        ),
    { timeout },
    text,
  );
}

async function clickVisibleButton(page, text) {
  await page.waitForFunction(
    (expectedText) =>
      window
        .visibleElements("button, [role='button']")
        .some(
          (element) =>
            window.normalizeText(element.textContent) === expectedText,
        ),
    { timeout: 30000 },
    text,
  );
  await page.evaluate((expectedText) => {
    const button = window
      .visibleElements("button, [role='button']")
      .find(
        (element) => window.normalizeText(element.textContent) === expectedText,
      );
    button.click();
  }, text);
}

async function clickDialogAction(page, text) {
  await page.waitForFunction(
    (expectedText) => {
      const dialogs = window.visibleElements(
        "[role='dialog'], .MuiDialog-root",
      );
      return dialogs.some((dialog) =>
        Array.from(dialog.querySelectorAll("button")).some(
          (button) =>
            window.getComputedStyle(button).display !== "none" &&
            window.normalizeText(button.textContent) === expectedText,
        ),
      );
    },
    { timeout: 30000 },
    text,
  );
  await page.evaluate((expectedText) => {
    const dialogs = window.visibleElements("[role='dialog'], .MuiDialog-root");
    for (const dialog of dialogs) {
      const button = Array.from(dialog.querySelectorAll("button")).find(
        (candidate) =>
          window.getComputedStyle(candidate).display !== "none" &&
          window.normalizeText(candidate.textContent) === expectedText,
      );
      if (button) {
        button.click();
        return;
      }
    }
  }, text);
}

async function typeIntoSearchInput(page, value) {
  await page.waitForSelector('input[placeholder="Search"]', { timeout: 30000 });
  await page.click('input[placeholder="Search"]');
  await page.keyboard.down(modifierKey());
  await page.keyboard.press("A");
  await page.keyboard.up(modifierKey());
  await page.keyboard.press("Backspace");
  await page.type('input[placeholder="Search"]', value);
}

async function selectProjectRow(page, projectName) {
  await page.waitForFunction(
    (expectedName) => {
      const rows = window.visibleElements('[role="row"], .MuiDataGrid-row');
      return rows.some(
        (row) =>
          window.normalizeText(row.textContent).includes(expectedName) &&
          row.querySelector('input[type="checkbox"]'),
      );
    },
    { timeout: 30000 },
    projectName,
  );
  await page.evaluate((expectedName) => {
    const row = window
      .visibleElements('[role="row"], .MuiDataGrid-row')
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent).includes(expectedName) &&
          candidate.querySelector('input[type="checkbox"]'),
      );
    const checkbox = row.querySelector('input[type="checkbox"]');
    checkbox.click();
  }, projectName);
}

async function closeDrawer(page) {
  await page.evaluate(() => {
    const drawer = window
      .visibleElements(".MuiDrawer-paper")
      .find((element) => {
        return window
          .normalizeText(element.textContent)
          .includes("New Projects");
      });
    const button = drawer?.querySelector("button");
    button?.click();
  });
}

async function assertPrototypeDrawerHasPlaceholders(page) {
  const visibleText = await page.evaluate(() => document.body.innerText || "");
  assert(
    visibleText.includes("<YOUR_FI_API_KEY>") ||
      visibleText.includes("YOUR_FI_API_KEY") ||
      visibleText.includes("FI_API_KEY"),
    "Prototype SDK drawer did not render API-key setup code.",
  );
  assert(
    !/fi_[A-Za-z0-9]{20,}/.test(visibleText),
    "Prototype SDK drawer exposed a raw-looking Future AGI key.",
  );
}

async function responseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function isProjectListResponse(response) {
  try {
    const url = new URL(response.url());
    return url.pathname.endsWith("/tracer/project/");
  } catch {
    return false;
  }
}

function isPrototypeApiUrl(url) {
  try {
    const parsed = new URL(url);
    return (
      parsed.pathname.startsWith("/tracer/project/") ||
      parsed.pathname.startsWith("/tracer/project-version/")
    );
  } catch {
    return false;
  }
}

function isAllowedPrototypeMutation(method, url) {
  const pathname = new URL(url).pathname;
  return method === "DELETE" && pathname === "/tracer/project/";
}

function maskRequest(value) {
  const urlPattern = /(https?:\/\/[^/]+)(\/[^ ]*)/g;
  return value.replace(urlPattern, "$2");
}

function modifierKey() {
  return process.platform === "darwin" ? "Meta" : "Control";
}

function browserExecutablePath() {
  return (
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  );
}

main().catch((error) => {
  if (error?.name === "SkipJourney") {
    console.log(JSON.stringify({ status: "skipped", reason: error.reason }));
    process.exit(0);
  }
  console.error(error);
  process.exit(1);
});
