import { execFile as execFileCallback } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  envFlag,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFile = promisify(execFileCallback);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/observe-projects-smoke.png";
const ADD_PROJECT_SCREENSHOT_PATH =
  "/tmp/observe-projects-add-drawer-smoke.png";
const TAG_SCREENSHOT_PATH = "/tmp/observe-projects-tag-edit-smoke.png";
const DELETE_SCREENSHOT_PATH = "/tmp/observe-projects-delete-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/observe-projects-smoke-failure.png";
const PROJECT_PREFIX = "ui_observe_browser_";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  const auth = await createAuthenticatedContext();
  const runMutations = envFlag("API_JOURNEY_MUTATIONS");
  const list = await auth.client.get(
    apiPath("/tracer/project/list_projects/"),
    {
      query: {
        project_type: "observe",
        page_number: 0,
        page_size: 25,
        sort_by: "updated_at",
        sort_direction: "desc",
      },
    },
  );
  const projects = asArray(list);
  assert(projects.length > 0, "Observe project list returned no projects.");

  const { project, detail } = await selectCurrentWorkspaceProject(
    auth,
    projects,
  );
  const searchTerm = String(project.name || "")
    .slice(0, 8)
    .trim();
  assert(
    searchTerm,
    "Selected observe project name could not produce a search term.",
  );

  const evidence = {
    project_id: project.id,
    project_name: project.name,
    project_workspace: detail.workspace,
    project_count: list.metadata?.total_rows || projects.length,
    visible_null_workspace_rows: await countNullWorkspaceRows(auth, projects),
    last_30_days_vol: project.last_30_days_vol,
    daily_volume_points: asArray(project.daily_volume).length,
  };
  const cleanupEvidence = [];
  let disposableProjectId = null;
  let mutationFixture = null;
  if (runMutations) {
    await hardDeleteObserveProjectFixturesByPrefix({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      prefix: PROJECT_PREFIX,
    });
    const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
    const projectName = `${PROJECT_PREFIX}${suffix}`;
    mutationFixture = await createObserveProject(auth.client, projectName);
    disposableProjectId = mutationFixture.project_id;
  }
  const apiFailures = [];
  const pageErrors = [];
  const unexpectedMutations = [];
  const browserMutations = [];

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

  page.on("request", (request) => {
    const url = request.url();
    if (isObserveProjectApiUrl(url) && MUTATION_METHODS.has(request.method())) {
      const mutation = `${request.method()} ${url}`;
      browserMutations.push(mutation);
      if (!isAllowedObserveProjectMutation(request.method(), url)) {
        unexpectedMutations.push(mutation);
      }
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (isObserveProjectApiUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await page.goto(`${APP_BASE}/dashboard/observe`, {
      waitUntil: "domcontentloaded",
    });

    await expectVisibleText(page, "Tracing", { exact: true });
    await expectVisibleText(page, "Project", { exact: true });
    await expectVisibleText(page, "Alerts", { exact: true });
    await expectVisibleText(page, "Volume (30d)", { exact: true });
    await expectVisibleText(page, "Tags", { exact: true });
    await expectVisibleText(page, "Last Active", { exact: true });
    await expectVisibleText(page, project.name, { exact: true });
    await page.waitForSelector('input[placeholder="Search"]', {
      timeout: 30000,
    });

    await typeSearch(page, searchTerm);
    await expectSearchValue(page, searchTerm);
    await expectVisibleText(page, project.name, { exact: true });

    await clickVisibleRowText(page, project.name);

    await page.waitForFunction(
      (projectId) =>
        window.location.pathname.endsWith(
          `/dashboard/observe/${projectId}/llm-tracing`,
        ),
      { timeout: 30000 },
      project.id,
    );
    await expectAnyVisibleText(page, ["Traces", "Trace"]);
    await expectAnyVisibleText(page, ["Trace Name", "Input", "Output"]);
    await expectVisibleText(page, "Filter", { exact: true });
    await expectVisibleText(page, "Past");
    await expectNoVisibleText(page, "Invalid Date");

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    if (mutationFixture) {
      evidence.mutation_coverage = await exerciseObserveProjectMutationFlow({
        page,
        auth,
        fixture: mutationFixture,
        browserMutations,
        cleanupEvidence,
      });
      disposableProjectId = null;
    }

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Read-only observe project smoke fired mutations: ${unexpectedMutations.join("; ")}`,
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
          browser_mutations: browserMutations.map(maskRequest),
          cleanup: cleanupEvidence,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await page.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true });
    console.error(`failure_screenshot=${FAILURE_SCREENSHOT_PATH}`);
    throw error;
  } finally {
    if (disposableProjectId) {
      await deleteObserveProjects(
        auth.client,
        [disposableProjectId],
        cleanupEvidence,
      ).catch((error) => {
        cleanupEvidence.push({
          cleanup: "delete observe project after failure",
          status: "failed",
          error: error.message,
        });
      });
      await hardDeleteObserveProjectFixturesByPrefix({
        organizationId: auth.organizationId,
        workspaceId: auth.workspaceId,
        prefix: PROJECT_PREFIX,
      }).catch((error) => {
        cleanupEvidence.push({
          cleanup: "hard delete observe project after failure",
          status: "failed",
          error: error.message,
        });
      });
    }
    await browser.close();
  }
}

async function exerciseObserveProjectMutationFlow({
  page,
  auth,
  fixture,
  browserMutations,
  cleanupEvidence,
}) {
  const projectId = fixture.project_id;
  const projectName = fixture.project_name;
  const tag = `ui-observe-${auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase()}`;
  const addDrawerMutationCount = browserMutations.length;

  await waitForResponsesDuring(
    page,
    "observe project list load for disposable project",
    [
      (response) =>
        response.url().includes("/tracer/project/list_projects/") &&
        response.url().includes("project_type=observe") &&
        response.status() < 400,
    ],
    () =>
      page.goto(`${APP_BASE}/dashboard/observe`, {
        waitUntil: "domcontentloaded",
      }),
  );
  await waitForPath(page, "/dashboard/observe");
  await expectVisibleText(page, "Add Project", { exact: true });

  const sdkCodeResponse = await waitForResponseDuring(
    page,
    "observe add project SDK drawer",
    (response) =>
      response.url().includes("/tracer/project/project_sdk_code/") &&
      response.url().includes("project_type=observe") &&
      response.request().method() === "GET" &&
      response.status() < 400,
    () => clickVisibleButton(page, "Add Project"),
  );
  const sdkCodePayload = await responseJson(sdkCodeResponse);
  await expectVisibleText(page, "New Projects", { exact: true });
  await expectVisibleText(page, "Install Dependencies", { exact: true });
  await expectVisibleText(page, "Load API keys", { exact: true });
  await expectVisibleText(page, "Setup Telemetry", { exact: true });
  await assertObserveDrawerHasPlaceholders(page);
  const addDrawerMutations = browserMutations.slice(addDrawerMutationCount);
  assert(
    addDrawerMutations.length === 0,
    `Observe Add Project drawer unexpectedly fired mutations: ${addDrawerMutations
      .map(maskRequest)
      .join(", ")}`,
  );
  await page.screenshot({ path: ADD_PROJECT_SCREENSHOT_PATH, fullPage: true });
  await closeDrawer(page);
  await waitForNoVisibleExactText(page, "New Projects");

  await waitForResponseDuring(
    page,
    "search disposable observe project",
    (response) => {
      if (
        !response.url().includes("/tracer/project/list_projects/") ||
        response.status() >= 400
      ) {
        return false;
      }
      const url = new URL(response.url());
      return url.searchParams.get("name") === projectName;
    },
    () => typeSearch(page, projectName),
  );
  await waitForPath(page, "/dashboard/observe");
  await expectVisibleText(page, projectName, { exact: true });

  const tagResponse = await waitForResponseDuring(
    page,
    "observe project tag update",
    (response) =>
      response.url().includes(`/tracer/project/${projectId}/tags/`) &&
      response.request().method() === "PATCH",
    () => addTagToProjectRow(page, projectName, tag),
  );
  const tagPayload = await responseJson(tagResponse);
  assert(
    tagResponse.status() >= 200 && tagResponse.status() < 300,
    `Observe project tag update returned HTTP ${tagResponse.status()}: ${JSON.stringify(
      tagPayload,
    )}`,
  );
  await page.keyboard.press("Escape");
  await expectVisibleText(page, tag, { exact: true });
  const tagAudit = await loadObserveProjectDbAudit({
    projectId,
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
  });
  assert(
    tagAudit.exists === true &&
      tagAudit.deleted === false &&
      asArray(tagAudit.tags).includes(tag),
    `Observe project tag DB audit failed: ${JSON.stringify(tagAudit)}`,
  );
  await page.screenshot({ path: TAG_SCREENSHOT_PATH, fullPage: true });

  await selectProjectRow(page, projectName);
  await expectVisibleText(page, "1 Selected", { exact: true });
  await clickVisibleButton(page, "Delete");
  await expectVisibleText(page, "Delete Project", { exact: true });
  const deleteResponse = await waitForResponseDuring(
    page,
    "delete disposable observe project",
    (response) =>
      response.url().includes("/tracer/project/") &&
      response.request().method() === "DELETE",
    () => clickDialogAction(page, "Delete"),
  );
  const deletePayload = await responseJson(deleteResponse);
  assert(
    deleteResponse.status() >= 200 && deleteResponse.status() < 300,
    `Observe project delete returned HTTP ${deleteResponse.status()}: ${JSON.stringify(
      deletePayload,
    )}`,
  );
  await waitForNoVisibleExactText(page, projectName);

  const deletedAudit = await loadObserveProjectDbAudit({
    projectId,
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
  });
  assert(
    deletedAudit.exists === true &&
      deletedAudit.deleted === true &&
      deletedAudit.deleted_at_set === true,
    `Observe project delete DB audit failed: ${JSON.stringify(deletedAudit)}`,
  );
  const deletedDetail = await expectDeletedProjectDetail(
    auth.client,
    projectId,
  );
  assert(
    deletedDetail?.message === "Project Not Found" ||
      deletedDetail?.detail === "Project Not Found",
    `Deleted observe project detail did not return Project Not Found: ${JSON.stringify(
      deletedDetail,
    )}`,
  );
  const cleanupAudit = await hardDeleteObserveProjectFixturesByPrefix({
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    prefix: PROJECT_PREFIX,
  });
  cleanupEvidence.push({
    cleanup: "hard delete observe project fixtures",
    status: "passed",
    audit: cleanupAudit,
  });
  assert(
    Number(cleanupAudit.remaining_project_count) === 0,
    `Observe project hard cleanup left residue: ${JSON.stringify(cleanupAudit)}`,
  );
  await page.screenshot({ path: DELETE_SCREENSHOT_PATH, fullPage: true });

  return {
    project_id: projectId,
    project_name: projectName,
    added_tag: tag,
    tag_response_tags: tagPayload?.result?.tags || tagPayload?.tags || [],
    tag_db_audit: tagAudit,
    deleted_db_audit: deletedAudit,
    add_project_behavior:
      "opens setup SDK drawer; no browser project create mutation",
    sdk_response_sections: Object.keys(
      sdkCodePayload?.result || sdkCodePayload || {},
    ),
    screenshots: [
      ADD_PROJECT_SCREENSHOT_PATH,
      TAG_SCREENSHOT_PATH,
      DELETE_SCREENSHOT_PATH,
    ],
  };
}

async function createObserveProject(client, name) {
  const created = await client.post(apiPath("/tracer/project/"), {
    name,
    model_type: "GenerativeLLM",
    trace_type: "observe",
  });
  const projectId = created.project_id || created.projectId || created.id;
  assert(
    isUuid(projectId),
    `Observe project create omitted a valid id: ${JSON.stringify(created)}`,
  );
  return {
    project_id: projectId,
    project_name: created.name || name,
  };
}

async function deleteObserveProjects(client, projectIds, evidence) {
  if (!projectIds.length) return;
  await client.delete(apiPath("/tracer/project/"), {
    body: {
      project_ids: projectIds,
      project_type: "observe",
    },
    okStatuses: [200, 400, 404],
  });
  evidence.push({
    cleanup: "delete observe project",
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
    if ([400, 404].includes(error?.status)) return error.body;
    throw error;
  }
}

async function selectCurrentWorkspaceProject(auth, projects) {
  for (const project of projects) {
    if (!isUuid(project?.id)) continue;
    const detail = await auth.client.get(
      apiPath("/tracer/project/{id}/", { id: project.id }),
    );
    if (
      detail?.trace_type === "observe" &&
      detail?.workspace === auth.workspaceId
    ) {
      return { project, detail };
    }
  }
  throw new Error(
    "No current-workspace observe project was found on the first page.",
  );
}

async function countNullWorkspaceRows(auth, projects) {
  let count = 0;
  for (const project of projects) {
    const detail = await auth.client.get(
      apiPath("/tracer/project/{id}/", { id: project.id }),
    );
    if (detail?.workspace == null) count += 1;
  }
  return count;
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

async function typeSearch(page, value) {
  await page.waitForSelector('input[placeholder="Search"]', { timeout: 30000 });
  await page.click('input[placeholder="Search"]');
  await page.keyboard.down(modifierKey());
  await page.keyboard.press("A");
  await page.keyboard.up(modifierKey());
  await page.keyboard.press("Backspace");
  await page.type('input[placeholder="Search"]', value);
}

async function expectSearchValue(page, value) {
  await page.waitForFunction(
    (expectedValue) =>
      document.querySelector('input[placeholder="Search"]')?.value ===
      expectedValue,
    { timeout: 30000 },
    value,
  );
}

async function waitForPath(page, pathName) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname === expectedPath,
    { timeout: 30000 },
    pathName,
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
    await Promise.all([
      ...predicates.map((predicate) =>
        page.waitForResponse(predicate, { timeout: 60000 }),
      ),
      action(),
    ]);
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
}

async function expectVisibleText(
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

async function expectAnyVisibleText(page, texts, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (expectedTexts) => {
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
      return Array.from(document.querySelectorAll("body *")).some(
        (element) =>
          isVisible(element) &&
          expectedTexts.some((text) =>
            String(element.textContent || "").includes(text),
          ),
      );
    },
    { timeout },
    texts,
  );
}

async function expectNoVisibleText(page, text, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (expectedText) => {
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
        (element) =>
          isVisible(element) && element.textContent?.includes(expectedText),
      );
    },
    { timeout },
    text,
  );
}

async function waitForNoVisibleExactText(page, text, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (expectedText) => {
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
        (element) =>
          isVisible(element) &&
          String(element.textContent || "").trim() === expectedText,
      );
    },
    { timeout },
    text,
  );
}

async function clickVisibleButton(page, text) {
  await page.waitForFunction(
    (expectedText) => {
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
      return Array.from(
        document.querySelectorAll("button,[role='button']"),
      ).some(
        (element) =>
          isVisible(element) &&
          String(element.textContent || "").trim() === expectedText,
      );
    },
    { timeout: 30000 },
    text,
  );
  await page.evaluate((expectedText) => {
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
    const element = Array.from(
      document.querySelectorAll("button,[role='button']"),
    ).find(
      (candidate) =>
        isVisible(candidate) &&
        String(candidate.textContent || "").trim() === expectedText,
    );
    element.click();
  }, text);
}

async function clickDialogAction(page, text) {
  await page.evaluate((expectedText) => {
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
    const dialogs = Array.from(
      document.querySelectorAll('[role="dialog"],.MuiDialog-root'),
    ).filter(isVisible);
    const root = dialogs.at(-1) || document.body;
    const button = Array.from(
      root.querySelectorAll("button,[role='button']"),
    ).find(
      (candidate) =>
        isVisible(candidate) &&
        String(candidate.textContent || "").trim() === expectedText,
    );
    if (!button) throw new Error(`Dialog action ${expectedText} not found.`);
    button.click();
  }, text);
}

async function closeDrawer(page) {
  await page.keyboard.press("Escape");
  await waitForNoVisibleExactText(page, "New Projects");
}

async function assertObserveDrawerHasPlaceholders(page) {
  await page.waitForFunction(
    () => {
      const text = document.body.textContent || "";
      return (
        text.includes("YOUR_FI_API_KEY") &&
        text.includes("YOUR_FI_SECRET_KEY") &&
        !/fi-[A-Za-z0-9_-]{20,}/.test(text)
      );
    },
    { timeout: 30000 },
  );
}

async function addTagToProjectRow(page, projectName, tag) {
  await clickProjectGridCell(page, projectName, "Tags");
  await page.waitForSelector(
    'input[placeholder="Type new tag and press Enter"]',
    { timeout: 30000 },
  );
  await page.click('input[placeholder="Type new tag and press Enter"]');
  await page.type('input[placeholder="Type new tag and press Enter"]', tag);
  await page.keyboard.press("Enter");
}

async function selectProjectRow(page, projectName) {
  await page.waitForFunction(
    (expectedName) =>
      Array.from(
        document.querySelectorAll(".MuiDataGrid-row,[role='row']"),
      ).some((row) => String(row.textContent || "").includes(expectedName)),
    { timeout: 30000 },
    projectName,
  );
  await page.evaluate((expectedName) => {
    const row = Array.from(
      document.querySelectorAll(".MuiDataGrid-row,[role='row']"),
    ).find((candidate) =>
      String(candidate.textContent || "").includes(expectedName),
    );
    if (!row) throw new Error(`Project row ${expectedName} not found.`);
    const checkbox =
      row.querySelector('input[type="checkbox"]') ||
      row.querySelector('[data-field="__check__"]') ||
      row.querySelector(".MuiDataGrid-cellCheckbox");
    if (!checkbox)
      throw new Error(`Project row ${expectedName} has no checkbox.`);
    checkbox.click();
  }, projectName);
}

async function clickProjectGridCell(page, projectName, headerText) {
  await page.waitForFunction(
    ({ projectName: expectedName }) =>
      Array.from(
        document.querySelectorAll(".MuiDataGrid-row,[role='row']"),
      ).some((row) => String(row.textContent || "").includes(expectedName)),
    { timeout: 30000 },
    { projectName },
  );
  await page.evaluate(
    ({ projectName: expectedName, headerText: expectedHeader }) => {
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
      const headers = Array.from(
        document.querySelectorAll(
          '[role="columnheader"],.MuiDataGrid-columnHeader',
        ),
      );
      const header = headers.find((candidate) =>
        String(candidate.textContent || "").includes(expectedHeader),
      );
      const colIndex = header?.getAttribute("aria-colindex");
      const row = Array.from(
        document.querySelectorAll(".MuiDataGrid-row,[role='row']"),
      ).find(
        (candidate) =>
          isVisible(candidate) &&
          String(candidate.textContent || "").includes(expectedName),
      );
      if (!row) throw new Error(`Project row ${expectedName} not found.`);
      const cell =
        row.querySelector('[data-field="tags"]') ||
        row.querySelector(`[data-field="${expectedHeader.toLowerCase()}"]`) ||
        (colIndex &&
          row.querySelector(
            `[role="gridcell"][aria-colindex="${colIndex}"]`,
          )) ||
        Array.from(
          row.querySelectorAll('[role="gridcell"],.MuiDataGrid-cell'),
        ).find((candidate) => candidate.getAttribute("data-field") === "tags");
      if (!cell)
        throw new Error(`Project row ${expectedName} tag cell not found.`);
      const target =
        cell.querySelector("button,[role='button'],.MuiBox-root,svg") || cell;
      target.click();
    },
    { projectName, headerText },
  );
}

async function responseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

async function clickVisibleRowText(page, text) {
  await page.waitForFunction(
    (expectedText) => {
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
        if (String(element.textContent || "").trim() !== expectedText)
          return false;
        return Boolean(
          element.closest("tr,[role='row'],.MuiTableRow-root,[data-row-id]"),
        );
      });
    },
    { timeout: 30000 },
    text,
  );
  await page.evaluate((expectedText) => {
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
    const element = Array.from(document.querySelectorAll("body *")).find(
      (candidate) =>
        isVisible(candidate) &&
        String(candidate.textContent || "").trim() === expectedText &&
        Boolean(
          candidate.closest("tr,[role='row'],.MuiTableRow-root,[data-row-id]"),
        ),
    );
    const row = element.closest(
      "tr,[role='row'],.MuiTableRow-root,[data-row-id]",
    );
    row.click();
  }, text);
}

function isObserveProjectApiUrl(url) {
  return (
    url.includes("/tracer/project/list_projects/") ||
    url.includes("/tracer/project/") ||
    url.includes("/tracer/observation-span/") ||
    url.includes("/tracer/trace/list_traces_of_session/") ||
    url.includes("/tracer/dashboard/metrics/")
  );
}

function isAllowedObserveProjectMutation(method, url) {
  const pathName = new URL(url).pathname;
  if (
    method === "PATCH" &&
    /^\/tracer\/project\/[^/]+\/tags\/$/.test(pathName)
  ) {
    return true;
  }
  if (method === "DELETE" && pathName === "/tracer/project/") {
    return true;
  }
  return false;
}

async function loadObserveProjectDbAudit({
  projectId,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id
)
SELECT COALESCE(
  (
    SELECT jsonb_build_object(
      'exists', true,
      'project_id', project.id::text,
      'name', project.name,
      'trace_type', project.trace_type,
      'organization_id', project.organization_id::text,
      'workspace_id', project.workspace_id::text,
      'tags', COALESCE(project.tags, '[]'::jsonb),
      'deleted', project.deleted,
      'deleted_at_set', project.deleted_at IS NOT NULL
    )
    FROM requested
    JOIN tracer_project project
      ON project.id = requested.project_id
     AND project.organization_id = requested.organization_id
     AND project.workspace_id = requested.workspace_id
  ),
  jsonb_build_object('exists', false)
)::text;
`;
  return runPostgresJson(sql);
}

async function hardDeleteObserveProjectFixturesByPrefix({
  organizationId,
  workspaceId,
  prefix,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlString(`${prefix}%`)} AS name_pattern
),
target_projects AS (
  SELECT project.id
  FROM tracer_project project
  JOIN requested r
    ON project.organization_id = r.organization_id
   AND project.workspace_id = r.workspace_id
  WHERE project.name LIKE r.name_pattern
),
deleted_trace_scan_config AS (
  DELETE FROM tracer_trace_scan_config scan
  USING target_projects target
  WHERE scan.project_id = target.id
  RETURNING 1
),
deleted_eval_tasks AS (
  DELETE FROM tracer_eval_task task
  USING target_projects target
  WHERE task.project_id = target.id
  RETURNING 1
),
deleted_monitors AS (
  DELETE FROM tracer_useralertmonitor monitor
  USING target_projects target
  WHERE monitor.project_id = target.id
  RETURNING 1
),
deleted_spans AS (
  DELETE FROM tracer_observation_span span
  USING target_projects target
  WHERE span.project_id = target.id
  RETURNING 1
),
deleted_traces AS (
  DELETE FROM tracer_trace trace
  USING target_projects target
  WHERE trace.project_id = target.id
  RETURNING 1
),
deleted_sessions AS (
  DELETE FROM trace_session session
  USING target_projects target
  WHERE session.project_id = target.id
  RETURNING 1
),
deleted_project_versions AS (
  DELETE FROM tracer_project_version version
  USING target_projects target
  WHERE version.project_id = target.id
  RETURNING 1
),
deleted_projects AS (
  DELETE FROM tracer_project project
  USING target_projects target
  WHERE project.id = target.id
  RETURNING 1
)
SELECT jsonb_build_object(
  'deleted_trace_scan_config_count', (SELECT count(*) FROM deleted_trace_scan_config),
  'deleted_eval_task_count', (SELECT count(*) FROM deleted_eval_tasks),
  'deleted_monitor_count', (SELECT count(*) FROM deleted_monitors),
  'deleted_span_count', (SELECT count(*) FROM deleted_spans),
  'deleted_trace_count', (SELECT count(*) FROM deleted_traces),
  'deleted_session_count', (SELECT count(*) FROM deleted_sessions),
  'deleted_project_version_count', (SELECT count(*) FROM deleted_project_versions),
  'deleted_project_count', (SELECT count(*) FROM deleted_projects),
  'remaining_project_count', CASE
    WHEN (SELECT count(*) FROM deleted_projects) > 0 THEN 0
    ELSE (
      SELECT count(*)
      FROM tracer_project project
      JOIN requested r
        ON project.organization_id = r.organization_id
       AND project.workspace_id = r.workspace_id
      WHERE project.name LIKE r.name_pattern
    )
  END
)::text;
`;
  return runPostgresJson(sql);
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFile(
    "docker",
    ["exec", container, "psql", "-qAt", "-U", user, "-d", database, "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const line = stdout.trim().split(/\r?\n/).find(Boolean);
  assert(line, "Postgres DB audit returned no JSON output.");
  return JSON.parse(line);
}

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID value, got ${value}.`);
  return `'${value}'::uuid`;
}

function sqlString(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

function maskRequest(request) {
  return String(request)
    .replace(/access_token=[^&\s]+/g, "access_token=<redacted>")
    .replace(/token=[^&\s]+/g, "token=<redacted>")
    .replace(/Bearer\s+[A-Za-z0-9._-]+/g, "Bearer <redacted>");
}

function modifierKey() {
  return process.platform === "darwin" ? "Meta" : "Control";
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH)
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  if (process.platform === "linux") return "/usr/bin/google-chrome";
  return undefined;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
