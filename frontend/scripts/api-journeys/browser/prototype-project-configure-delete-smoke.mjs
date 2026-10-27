/* eslint-disable no-console */
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  isUuid,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const PROJECT_PREFIX = "ui_prototype_config_browser_";
const CONFIGURE_SCREENSHOT_PATH = "/tmp/prototype-configure-rename-smoke.png";
const DELETE_SCREENSHOT_PATH = "/tmp/prototype-configure-delete-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/prototype-configure-delete-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const marker = suffix.slice(0, 18);
  const projectName = `${PROJECT_PREFIX}${marker}`;
  const renamedProjectName = `${projectName}_renamed`;
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
    projectId = created.project_id || created.projectId || created.id;
    assert(isUuid(projectId), "Prototype project create omitted a valid id.");

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
      "prototype detail load",
      [
        (response) =>
          response.url().includes(`/tracer/project/${projectId}/`) &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/tracer/project-version/list_runs/") &&
          response.url().includes(projectId) &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/prototype/${projectId}`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, `/dashboard/prototype/${projectId}`);
    await waitForVisibleText(page, projectName, { exact: true });
    await waitForVisibleText(page, "All runs", { exact: true });
    await waitForVisibleText(page, "Configure", { exact: true });

    await clickVisibleButton(page, "Configure");
    await waitForVisibleText(page, "Configure Project", { exact: true });
    await fillProjectNameField(page, renamedProjectName);
    await assertDialogActionEnabled(page, "Update");

    const updateResponse = await waitForResponseDuring(
      page,
      "prototype configure rename",
      (response) =>
        response.url().includes("/tracer/project/update_project_name/") &&
        response.request().method() === "POST",
      () => clickDialogAction(page, "Update", "Configure Project"),
    );
    const updatePayload = await responseJson(updateResponse);
    assert(
      updateResponse.status() < 400,
      `Prototype configure update returned HTTP ${updateResponse.status()}: ${JSON.stringify(
        updatePayload,
      )}`,
    );
    await waitForNoVisibleExactText(page, "Configure Project");

    const renamedDetail = await pollProjectDetailName(
      auth.client,
      projectId,
      renamedProjectName,
    );
    assert(
      renamedDetail?.name === renamedProjectName,
      `Prototype detail did not persist renamed project: ${JSON.stringify(
        renamedDetail,
      )}`,
    );
    await waitForVisibleText(page, renamedProjectName, { exact: true });
    await page.screenshot({ path: CONFIGURE_SCREENSHOT_PATH, fullPage: true });

    await clickVisibleButton(page, "Configure");
    await waitForVisibleText(page, "Configure Project", { exact: true });
    await clickDialogAction(page, "Delete", "Configure Project");
    await waitForVisibleText(page, "Delete Project", { exact: true });
    await waitForVisibleText(
      page,
      "Are you sure you want to delete this project?",
      { exact: true },
    );

    const deleteResponse = await waitForResponseDuring(
      page,
      "prototype configure delete",
      (response) =>
        response.url().includes("/tracer/project/") &&
        response.request().method() === "DELETE",
      () => clickDialogAction(page, "Delete", "Delete Project"),
    );
    const deletePayload = await responseJson(deleteResponse);
    assert(
      deleteResponse.status() >= 200 && deleteResponse.status() < 300,
      `Prototype configure delete returned HTTP ${deleteResponse.status()}: ${JSON.stringify(
        deletePayload,
      )}`,
    );
    await waitForPath(page, "/dashboard/prototype");
    await waitForNoVisibleExactText(page, renamedProjectName);
    await page.screenshot({ path: DELETE_SCREENSHOT_PATH, fullPage: true });

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

    assert(
      unexpectedMutations.length === 0,
      `Unexpected prototype browser mutations: ${unexpectedMutations
        .map(maskRequest)
        .join(", ")}`,
    );
    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
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
          original_project_name: projectName,
          renamed_project_name: renamedProjectName,
          browser_mutations: browserMutations.map(maskRequest),
          prototype_request_count: prototypeRequests.length,
          screenshots: [CONFIGURE_SCREENSHOT_PATH, DELETE_SCREENSHOT_PATH],
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
  if (ids.length) await deleteProjects(client, ids, evidence);
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
    return await client.get(
      apiPath("/tracer/project/{id}/", { id: projectId }),
      {
        okStatuses: [400, 404],
      },
    );
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

async function pollProjectDetailName(client, projectId, expectedName) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const detail = await client.get(
      apiPath("/tracer/project/{id}/", { id: projectId }),
    );
    if (detail?.name === expectedName) return detail;
    await delay(500);
  }
  return client.get(apiPath("/tracer/project/{id}/", { id: projectId }));
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

async function clickDialogAction(page, text, dialogText) {
  await assertDialogActionEnabled(page, text, dialogText);
  await page.evaluate(
    ({ expectedText, expectedDialogText }) => {
      const dialogs = window
        .visibleElements("[role='dialog']")
        .filter(
          (dialog) =>
            !expectedDialogText ||
            window
              .normalizeText(dialog.textContent)
              .includes(expectedDialogText),
        );
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
    },
    { expectedText: text, expectedDialogText: dialogText },
  );
}

async function assertDialogActionEnabled(page, text, dialogText) {
  const stateHandle = await page.waitForFunction(
    ({ expectedText, expectedDialogText }) => {
      const dialogs = window
        .visibleElements("[role='dialog']")
        .filter(
          (dialog) =>
            !expectedDialogText ||
            window
              .normalizeText(dialog.textContent)
              .includes(expectedDialogText),
        );
      for (const dialog of dialogs) {
        const button = Array.from(dialog.querySelectorAll("button")).find(
          (candidate) =>
            window.getComputedStyle(candidate).display !== "none" &&
            window.normalizeText(candidate.textContent) === expectedText,
        );
        if (!button) continue;
        return {
          disabled: Boolean(button.disabled),
          ariaDisabled: button.getAttribute("aria-disabled"),
          text: window.normalizeText(button.textContent),
        };
      }
      return false;
    },
    { timeout: 30000 },
    { expectedText: text, expectedDialogText: dialogText },
  );
  const state = await stateHandle.jsonValue();
  assert(
    !state.disabled && state.ariaDisabled !== "true",
    `Dialog action "${text}" is disabled: ${JSON.stringify(state)}`,
  );
}

async function fillProjectNameField(page, value) {
  const selector = 'input[placeholder="Enter project name"]';
  await page.waitForSelector(selector, { timeout: 30000 });
  const updated = await page.evaluate(
    ({ selector: targetSelector, value: nextValue }) => {
      const input = document.querySelector(targetSelector);
      if (!input) return false;
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      ).set;
      setter.call(input, nextValue);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return input.value === nextValue;
    },
    { selector, value },
  );
  assert(updated, "Could not fill project name input.");
}

async function responseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
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
  return (
    (method === "POST" &&
      pathname === "/tracer/project/update_project_name/") ||
    (method === "DELETE" && pathname === "/tracer/project/")
  );
}

function maskRequest(value) {
  const urlPattern = /(https?:\/\/[^/]+)(\/[^ ]*)/g;
  return value.replace(urlPattern, "$2");
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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
