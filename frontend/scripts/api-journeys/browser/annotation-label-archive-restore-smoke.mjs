/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  currentUserId,
  isUuid,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const LABEL_PREFIX = "ui_aq_label_archive_";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_LABEL_ARCHIVE_RESTORE_SCREENSHOT ||
  "/tmp/annotation-label-archive-restore-smoke.png";
const ARCHIVED_SCREENSHOT_PATH =
  process.env.ANNOTATION_LABEL_ARCHIVE_RESTORE_ARCHIVED_SCREENSHOT ||
  SCREENSHOT_PATH.replace(/\.png$/, "-archived.png");
const FAILURE_SCREENSHOT_PATH = SCREENSHOT_PATH.replace(
  /\.png$/,
  "-failure.png",
);
const MUTATION_METHODS = new Set(["DELETE", "PATCH", "POST", "PUT"]);

async function main() {
  requireMutations();

  const auth = await createAuthenticatedContext();
  const userId = currentUserId(auth.user);
  assert(isUuid(userId), "Authenticated user id could not be resolved.");

  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const browserMutations = [];
  const labelRequests = [];
  let browser = null;
  let page = null;
  let fixture = null;
  let caughtError = null;

  try {
    await hardDeleteLabelFixturesByPrefix({
      organizationId: auth.organizationId,
      evidence: cleanupEvidence,
    });

    fixture = await createLabelFixture(auth);
    const createdLabel = await findAnnotationLabelByName(
      auth.client,
      fixture.labelName,
    );
    assert(createdLabel, "Created label was not returned by the list API.");
    assert(
      String(createdLabel.id) === fixture.labelId,
      `List API returned a different label id: ${JSON.stringify(createdLabel)}`,
    );
    assert(
      createdLabel.type === "text",
      `Created label type was not text: ${JSON.stringify(createdLabel)}`,
    );

    const dbAuditAfterCreate = await loadLabelFixtureAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      labelId: fixture.labelId,
      labelName: fixture.labelName,
    });
    assert(
      Number(dbAuditAfterCreate.matching_count) === 1 &&
        Number(dbAuditAfterCreate.active_count) === 1,
      `Created label DB audit failed: ${JSON.stringify(dbAuditAfterCreate)}`,
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
      if (!isAnnotationLabelApiUrl(request.url())) return;
      labelRequests.push(`${request.method()} ${request.url()}`);
      if (MUTATION_METHODS.has(request.method())) {
        browserMutations.push(`${request.method()} ${request.url()}`);
      }
    });
    page.on("response", (response) => {
      if (isAnnotationLabelApiUrl(response.url()) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${response.url()}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "annotation label list load",
      (response) => isLabelListResponse(response),
      () =>
        page.goto(`${APP_BASE}/dashboard/annotations/labels`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForVisibleText(page, "Annotation labels", { exact: true });

    await waitForResponseDuring(
      page,
      "annotation label active search",
      (response) =>
        isLabelListResponse(response, { search: fixture.labelName }),
      () => setSearchValue(page, "Search labels...", fixture.labelName),
    );
    await waitForVisibleText(page, fixture.labelName, { exact: true });
    await waitForVisibleText(page, "Text", { exact: true });

    const archiveResult = await archiveLabelThroughList(page, {
      id: fixture.labelId,
      name: fixture.labelName,
    });
    const dbAuditAfterArchive = await loadLabelFixtureAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      labelId: fixture.labelId,
      labelName: fixture.labelName,
    });
    assert(
      Number(dbAuditAfterArchive.matching_count) === 1 &&
        Number(dbAuditAfterArchive.archived_count) === 1 &&
        Number(dbAuditAfterArchive.deleted_at_set_count) === 1,
      `Archived label DB audit failed: ${JSON.stringify(dbAuditAfterArchive)}`,
    );

    await waitForResponseDuring(
      page,
      "annotation label archived tab search",
      (response) =>
        isLabelListResponse(response, {
          search: fixture.labelName,
          archived: "true",
        }),
      () => clickVisibleText(page, "Archived", { exact: true }),
    );
    await waitForVisibleText(page, fixture.labelName, { exact: true });
    await page.screenshot({ path: ARCHIVED_SCREENSHOT_PATH, fullPage: true });

    const restoreResult = await restoreLabelThroughList(page, {
      id: fixture.labelId,
      name: fixture.labelName,
    });
    const dbAuditAfterRestore = await loadLabelFixtureAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      labelId: fixture.labelId,
      labelName: fixture.labelName,
    });
    assert(
      Number(dbAuditAfterRestore.matching_count) === 1 &&
        Number(dbAuditAfterRestore.active_count) === 1 &&
        Number(dbAuditAfterRestore.archived_count) === 0 &&
        Number(dbAuditAfterRestore.restored_deleted_at_null_count) === 1,
      `Restored label DB audit failed: ${JSON.stringify(dbAuditAfterRestore)}`,
    );

    await waitForResponseDuring(
      page,
      "annotation label active tab search",
      (response) =>
        isLabelListResponse(response, { search: fixture.labelName }),
      () => clickVisibleText(page, "Active", { exact: true }),
    );
    await waitForVisibleText(page, fixture.labelName, { exact: true });
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      apiFailures.length === 0,
      `Unexpected annotation label API failures: ${apiFailures.join(", ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    const cleanup = await hardDeleteLabelFixturesByPrefix({
      organizationId: auth.organizationId,
      evidence: cleanupEvidence,
    });
    assert(
      Number(cleanup.remaining_label_count) === 0,
      `Annotation label archive/restore cleanup left residue: ${JSON.stringify(
        cleanup,
      )}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          label_id: fixture.labelId,
          label_name: fixture.labelName,
          create_result: fixture.createResult,
          archive_result: archiveResult,
          restore_result: restoreResult,
          db_audit_after_create: dbAuditAfterCreate,
          db_audit_after_archive: dbAuditAfterArchive,
          db_audit_after_restore: dbAuditAfterRestore,
          browser_mutations: browserMutations.map(maskRequest),
          label_requests: labelRequests.map(maskRequest),
          screenshots: {
            active: SCREENSHOT_PATH,
            archived: ARCHIVED_SCREENSHOT_PATH,
          },
          cleanup: cleanupEvidence,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    caughtError = error;
    const domDebug = page
      ? await page
          .evaluate(() => ({
            url: window.location.href,
            text: document.body?.innerText?.slice(0, 2500) || "",
          }))
          .catch(() => null)
      : null;
    if (page) {
      await page
        .screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
    }
    console.error(
      JSON.stringify(
        {
          status: "failed",
          error: error.message,
          dom: domDebug,
          api_failures: apiFailures.map(maskRequest),
          browser_mutations: browserMutations.map(maskRequest),
          label_requests: labelRequests.map(maskRequest),
          screenshot: FAILURE_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
  } finally {
    if (browser) await browser.close();
    try {
      await hardDeleteLabelFixturesByPrefix({
        organizationId: auth.organizationId,
        evidence: cleanupEvidence,
      });
    } catch (cleanupError) {
      if (caughtError) {
        caughtError.message = `${caughtError.message}; cleanup failed: ${cleanupError.message}`;
      } else {
        caughtError = cleanupError;
      }
    }
  }

  if (caughtError) throw caughtError;
}

async function createLabelFixture(auth) {
  const suffix = (auth.runId || randomUUID())
    .replace(/[^a-z0-9]/gi, "")
    .toLowerCase();
  const labelName = `${LABEL_PREFIX}${suffix}`;
  const response = await auth.client.post(
    apiPath("/model-hub/annotations-labels/"),
    {
      name: labelName,
      type: "text",
      description: "Disposable label for browser archive/restore coverage.",
      settings: {
        placeholder: "Archive restore coverage",
        min_length: 0,
        max_length: 280,
      },
      allow_notes: true,
    },
  );
  const data = response?.result || response;
  const labelId = data?.id || data?.label_id || null;
  assert(
    isUuid(labelId),
    `Label create did not return a UUID: ${JSON.stringify(response)}`,
  );
  assert(
    data?.name === labelName,
    `Label create returned unexpected name: ${JSON.stringify(response)}`,
  );
  return {
    labelId: String(labelId),
    labelName,
    createResult: {
      id: String(labelId),
      name: data.name,
      type: data.type,
      status: data.status || null,
    },
  };
}

async function findAnnotationLabelByName(client, labelName) {
  const response = await client.get(apiPath("/model-hub/annotations-labels/"), {
    query: {
      page: 1,
      limit: 10,
      search: labelName,
      include_usage_count: true,
    },
  });
  return asArray(response).find(
    (candidate) => String(candidate?.name || "") === labelName,
  );
}

async function archiveLabelThroughList(page, { id, name }) {
  await openLabelActionsForName(page, name, "Label actions");
  await clickVisibleText(page, "Archive", { exact: true });
  await waitForVisibleText(page, "Archive Label", { exact: true });
  const response = await waitForResponseDuring(
    page,
    "annotation label archive submit",
    (candidate) => {
      const url = new URL(candidate.url());
      return (
        url.pathname === `/model-hub/annotations-labels/${id}/` &&
        candidate.request().method() === "DELETE"
      );
    },
    () => clickButtonWithText(page, "Archive"),
  );
  const payload = await responseJson(response);
  assert(
    response.status() >= 200 && response.status() < 300,
    `Archive returned non-2xx: ${response.status()} ${JSON.stringify(payload)}`,
  );
  await waitForVisibleText(page, "Label archived", { exact: false });
  await waitForNoVisibleText(page, name);
  return {
    status: response.status(),
    id,
    name,
    response: payload?.result || payload,
  };
}

async function restoreLabelThroughList(page, { id, name }) {
  await openLabelActionsForName(page, name, "Restore label actions");
  const response = await waitForResponseDuring(
    page,
    "annotation label restore action",
    (candidate) => {
      const url = new URL(candidate.url());
      return (
        url.pathname === `/model-hub/annotations-labels/${id}/restore/` &&
        candidate.request().method() === "POST"
      );
    },
    () => clickVisibleText(page, "Restore", { exact: true }),
  );
  const payload = await responseJson(response);
  assert(
    response.status() >= 200 && response.status() < 300,
    `Restore returned non-2xx: ${response.status()} ${JSON.stringify(payload)}`,
  );
  await waitForVisibleText(page, "Label restored", { exact: false });
  await waitForNoVisibleText(page, name);
  return {
    status: response.status(),
    id,
    name,
    response: payload?.result || payload,
  };
}

async function loadLabelFixtureAudit({
  organizationId,
  workspaceId,
  labelId,
  labelName,
}) {
  const sql = `
SELECT json_build_object(
  'matching_count', COUNT(*),
  'active_count', COUNT(*) FILTER (WHERE deleted = FALSE),
  'archived_count', COUNT(*) FILTER (WHERE deleted = TRUE),
  'deleted_at_set_count', COUNT(*) FILTER (WHERE deleted = TRUE AND deleted_at IS NOT NULL),
  'restored_deleted_at_null_count', COUNT(*) FILTER (WHERE deleted = FALSE AND deleted_at IS NULL),
  'organization_match_count', COUNT(*) FILTER (WHERE organization_id = ${sqlUuid(organizationId)}),
  'workspace_match_count', COUNT(*) FILTER (WHERE workspace_id = ${sqlUuid(workspaceId)}),
  'text_type_count', COUNT(*) FILTER (WHERE type = 'text')
)
FROM model_hub_annotationslabels
WHERE id = ${sqlUuid(labelId)}
  AND name = ${sqlTextLiteral(labelName)};
`;
  return runPostgresJson(sql);
}

async function hardDeleteLabelFixturesByPrefix({ organizationId, evidence }) {
  const sql = `
WITH target_labels AS (
  SELECT id
  FROM model_hub_annotationslabels
  WHERE name LIKE ${sqlTextLiteral(`${LABEL_PREFIX}%`)}
    AND organization_id = ${sqlUuid(organizationId)}
),
deleted_bindings AS (
  DELETE FROM model_hub_annotationqueuelabel
  WHERE label_id IN (SELECT id FROM target_labels)
  RETURNING id
),
deleted_labels AS (
  DELETE FROM model_hub_annotationslabels
  WHERE id IN (SELECT id FROM target_labels)
  RETURNING id
)
SELECT json_build_object(
  'deleted_label_count', (SELECT COUNT(*) FROM deleted_labels),
  'deleted_binding_count', (SELECT COUNT(*) FROM deleted_bindings),
  'remaining_label_count', (
    SELECT COUNT(*)
    FROM model_hub_annotationslabels
    WHERE name LIKE ${sqlTextLiteral(`${LABEL_PREFIX}%`)}
      AND organization_id = ${sqlUuid(organizationId)}
      AND id NOT IN (SELECT id FROM deleted_labels)
  )
);
`;
  const result = await runPostgresJson(sql);
  if (
    Number(result.deleted_label_count) > 0 ||
    Number(result.deleted_binding_count) > 0 ||
    Number(result.remaining_label_count) > 0
  ) {
    evidence.push({
      cleanup: "hard delete annotation label archive/restore fixtures",
      status: Number(result.remaining_label_count) === 0 ? "passed" : "failed",
      audit: result,
    });
  }
  return result;
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFileAsync(
    "docker",
    ["exec", container, "psql", "-U", user, "-d", database, "-At", "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const text = stdout.trim();
  assert(text, "Postgres DB audit returned no JSON output.");
  return JSON.parse(text);
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
    window.dispatchClick = (element) => {
      element.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
      );
      element.dispatchEvent(
        new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
      );
      element.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
    };
  });
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      if (organizationId)
        sessionStorage.setItem("organizationId", organizationId);
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id) {
        sessionStorage.setItem("futureagi-current-user-id", user.id);
        sessionStorage.setItem("currentUserId", user.id);
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

async function waitForNoVisibleText(page, text, timeout = 30000) {
  await page.waitForFunction(
    (expectedText) =>
      !window
        .visibleElements()
        .some((element) =>
          window.normalizeText(element.textContent).includes(expectedText),
        ),
    { timeout },
    text,
  );
}

async function clickVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await waitForVisibleText(page, text, { exact, timeout });
  const clicked = await page.evaluate(
    ({ text: expectedText, exact: exactMatch }) => {
      const elements = window.visibleElements().filter((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
      const element =
        elements.find((candidate) => {
          const button = candidate.closest("button");
          return button && !button.disabled;
        }) ||
        elements.find((candidate) =>
          candidate.closest("a,[role='button'],[role='menuitem']"),
        ) ||
        elements[0];
      const clickable =
        element?.closest(
          "button,a,[role='button'],[role='menuitem'],label,tr",
        ) || element;
      if (!clickable || clickable.disabled) return false;
      clickable.click();
      return true;
    },
    { text, exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

async function clickButtonWithText(page, text) {
  await waitForVisibleText(page, text, { exact: true });
  const clicked = await page.evaluate((expectedText) => {
    const button = window
      .visibleElements("button")
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedText &&
          !candidate.disabled,
      );
    if (!button) return false;
    button.click();
    return true;
  }, text);
  assert(clicked, `Could not click button: ${text}`);
}

async function setSearchValue(page, placeholder, value) {
  await setInputValue(
    page,
    `input[placeholder="${cssEscape(placeholder)}"]`,
    value,
  );
}

async function setInputValue(page, selector, value) {
  await page.waitForSelector(selector, { timeout: 30000 });
  await page.evaluate(
    ({ selector: targetSelector, value: nextValue }) => {
      const input = document.querySelector(targetSelector);
      if (!input)
        throw new Error(`Missing field for selector: ${targetSelector}`);
      const prototype =
        input instanceof HTMLTextAreaElement
          ? window.HTMLTextAreaElement.prototype
          : window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, "value").set;
      setter.call(input, nextValue);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    },
    { selector, value },
  );
}

async function openLabelActionsForName(page, name, ariaLabel) {
  await waitForVisibleText(page, name, { exact: true });
  const target = await page.evaluate(
    ({ labelName, buttonLabel }) => {
      const match = window
        .visibleElements()
        .find(
          (element) => window.normalizeText(element.textContent) === labelName,
        );
      const row = match?.closest('[role="row"], .ag-row, tr, [data-rowindex]');
      const button = row?.querySelector(
        `button[aria-label="${CSS.escape(buttonLabel)}"]`,
      );
      const rect = button?.getBoundingClientRect();
      if (!rect) {
        return {
          ok: false,
          reason: "button missing",
          rowFound: Boolean(row),
          buttonLabels: Array.from(row?.querySelectorAll("button") || []).map(
            (candidate) => candidate.getAttribute("aria-label") || "",
          ),
        };
      }
      return {
        ok: true,
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
      };
    },
    { labelName: name, buttonLabel: ariaLabel },
  );
  assert(
    target?.ok,
    `Could not find ${ariaLabel} for ${name}: ${JSON.stringify(target)}`,
  );
  await page.mouse.click(target.x, target.y, { delay: 25 });
  await waitForVisibleText(
    page,
    ariaLabel === "Label actions" ? "Archive" : "Restore",
    {
      exact: true,
    },
  );
}

async function responseJson(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function isAnnotationLabelApiUrl(rawUrl) {
  const url = new URL(rawUrl);
  return (
    url.origin ===
      new URL(process.env.API_BASE || "http://localhost:8003").origin &&
    url.pathname.startsWith("/model-hub/annotations-labels/")
  );
}

function isLabelListResponse(response, expectedQuery = {}) {
  if (response.request().method() !== "GET") return false;
  const url = new URL(response.url());
  if (!isAnnotationLabelApiUrl(response.url())) return false;
  if (url.pathname !== "/model-hub/annotations-labels/") return false;
  if (response.status() >= 400) return false;
  return Object.entries(expectedQuery).every(
    ([key, value]) => url.searchParams.get(key) === value,
  );
}

function maskRequest(rawRequest) {
  const [method, rawUrl] = rawRequest.split(" ");
  const url = new URL(rawUrl);
  return `${method} ${url.pathname}`;
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlTextLiteral(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function cssEscape(value) {
  return String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"');
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
  process.exitCode = 1;
});
