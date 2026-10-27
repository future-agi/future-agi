/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
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
const QUEUE_PREFIX = "ui_aq_form_";
const COPY_PREFIX = `Copy of ${QUEUE_PREFIX}`;
const SCREENSHOT_PATH =
  process.env.ANNOTATION_QUEUE_FORM_SCREENSHOT ||
  "/tmp/annotation-queue-create-duplicate-drawer-smoke.png";
const FAILURE_SCREENSHOT_PATH = SCREENSHOT_PATH.replace(
  /\.png$/,
  "-failure.png",
);

async function main() {
  requireMutations();

  const auth = await createAuthenticatedContext();
  const userId = currentUserId(auth.user);
  assert(isUuid(userId), "Authenticated user id could not be resolved.");

  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const browserMutations = [];
  let browser = null;
  let page = null;
  let fixture = null;
  let caughtError = null;

  try {
    await hardDeleteQueueFixturesByPrefix({
      organizationId: auth.organizationId,
      evidence: cleanupEvidence,
    });
    fixture = await seedDuplicateSourceQueue({
      runId: auth.runId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      userId,
    });

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
      if (!isAnnotationQueueApiUrl(request.url())) return;
      if (request.method() !== "GET" && request.method() !== "OPTIONS") {
        browserMutations.push(`${request.method()} ${request.url()}`);
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isAnnotationQueueApiUrl(url) && response.status() >= 500) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "annotation queue list load",
      (response) =>
        isQueueListResponse(response, {
          include_counts: "true",
          archived: "false",
        }),
      () =>
        page.goto(`${APP_BASE}/dashboard/annotations/queues`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForVisibleText(page, "Annotation queues", { exact: true });

    const createResult = await exerciseCreateDrawer(page, fixture.createName);
    assertCreateResult(createResult, fixture.createName);

    await waitForResponseDuring(
      page,
      "annotation queue source search",
      (response) =>
        isQueueListResponse(response, {
          search: fixture.sourceName,
          archived: "false",
        }),
      () => setSearchValue(page, fixture.sourceName),
    );
    await waitForVisibleText(page, fixture.sourceName, { exact: true });

    const duplicateResult = await exerciseDuplicateDrawer(page, fixture);
    assertCreateResult(duplicateResult, fixture.copyName);

    const dbAudit = await loadFormJourneyAudit({
      organizationId: auth.organizationId,
      sourceName: fixture.sourceName,
      createName: fixture.createName,
      copyName: fixture.copyName,
    });
    assert(
      Number(dbAudit.source_queue_count) === 1,
      `Source queue missing before cleanup: ${JSON.stringify(dbAudit)}`,
    );
    if (createResult.created) {
      assert(
        Number(dbAudit.create_queue_count) === 1,
        `Created queue not found in DB audit: ${JSON.stringify(dbAudit)}`,
      );
    } else {
      assert(
        Number(dbAudit.create_queue_count) === 0,
        `Blocked create left DB residue: ${JSON.stringify(dbAudit)}`,
      );
    }
    if (duplicateResult.created) {
      assert(
        Number(dbAudit.copy_queue_count) === 1,
        `Duplicate queue not found in DB audit: ${JSON.stringify(dbAudit)}`,
      );
    } else {
      assert(
        Number(dbAudit.copy_queue_count) === 0,
        `Blocked duplicate left DB residue: ${JSON.stringify(dbAudit)}`,
      );
    }

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      apiFailures.length === 0,
      `Unexpected annotation queue API failures: ${apiFailures.join(", ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    const cleanup = await hardDeleteQueueFixturesByPrefix({
      organizationId: auth.organizationId,
      evidence: cleanupEvidence,
    });
    assert(
      Number(cleanup.remaining_queue_count) === 0,
      `Annotation queue form cleanup left residue: ${JSON.stringify(cleanup)}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          source_queue_id: fixture.sourceId,
          create_result: summarizeCreateResult(createResult),
          duplicate_result: summarizeCreateResult(duplicateResult),
          db_audit: dbAudit,
          browser_mutations: browserMutations.map(maskRequest),
          screenshot: SCREENSHOT_PATH,
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
          screenshot: FAILURE_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
  } finally {
    if (browser) await browser.close();
    try {
      await hardDeleteQueueFixturesByPrefix({
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

async function exerciseCreateDrawer(page, createName) {
  await clickButtonWithText(page, "Create Queue");
  await waitForVisibleText(page, "Create annotation queue", { exact: true });
  await submitAnnotationQueueForm(page);
  await waitForVisibleText(page, "Queue name is required", { exact: true });
  await setFieldByPlaceholder(
    page,
    "eg: Hallucination analysis v2",
    createName,
  );
  await setFieldByPlaceholder(
    page,
    "Brief description of this queue's purpose",
    "Browser smoke create form",
  );
  const response = await waitForResponseDuring(
    page,
    "annotation queue create submit",
    (candidate) =>
      candidate.url().endsWith("/model-hub/annotation-queues/") &&
      candidate.request().method() === "POST",
    () => submitAnnotationQueueForm(page),
  );
  const payload = await responseJson(response);
  const result = normalizeCreateResponse(response, payload);
  if (result.created) {
    await waitForNoVisibleText(page, "Create annotation queue");
  } else {
    await waitForVisibleText(page, "annotation queues limit");
    await clickButtonWithText(page, "Cancel");
    await waitForNoVisibleText(page, "Create annotation queue");
  }
  return result;
}

async function exerciseDuplicateDrawer(page, fixture) {
  await openQueueActionsForName(page, fixture.sourceName);
  await clickVisibleText(page, "Duplicate", { exact: true });
  await waitForVisibleText(page, "Create annotation queue", { exact: true });
  await waitForFieldValue(
    page,
    "eg: Hallucination analysis v2",
    fixture.copyName,
  );
  await waitForFieldValue(
    page,
    "Brief description of this queue's purpose",
    "Browser smoke duplicate source",
  );
  await waitForVisibleText(page, "Annotators", { exact: true });
  const response = await waitForResponseDuring(
    page,
    "annotation queue duplicate submit",
    (candidate) =>
      candidate.url().endsWith("/model-hub/annotation-queues/") &&
      candidate.request().method() === "POST",
    () => submitAnnotationQueueForm(page),
  );
  const payload = await responseJson(response);
  const result = normalizeCreateResponse(response, payload);
  if (result.created) {
    await waitForNoVisibleText(page, "Create annotation queue");
  } else {
    await waitForVisibleText(page, "annotation queues limit");
    await clickButtonWithText(page, "Cancel");
    await waitForNoVisibleText(page, "Create annotation queue");
  }
  return result;
}

function normalizeCreateResponse(response, payload) {
  const created = response.status() >= 200 && response.status() < 300;
  const data = payload?.result || payload;
  return {
    created,
    status: response.status(),
    id: data?.id || null,
    name: data?.name || null,
    code: payload?.code || payload?.error?.code || null,
    message:
      payload?.message ||
      payload?.detail ||
      payload?.result ||
      payload?.error?.message ||
      null,
  };
}

function assertCreateResult(result, expectedName) {
  if (result.created) {
    assert(
      isUuid(result.id),
      `Create succeeded without UUID: ${JSON.stringify(result)}`,
    );
    assert(
      result.name === expectedName,
      `Create returned unexpected name: ${JSON.stringify(result)}`,
    );
    return;
  }
  assert(
    result.status === 402 && result.code === "ENTITLEMENT_LIMIT",
    `Expected create to succeed or return ENTITLEMENT_LIMIT: ${JSON.stringify(
      result,
    )}`,
  );
  assert(
    String(result.message || "").includes("annotation queues limit"),
    `Create entitlement message was not actionable: ${JSON.stringify(result)}`,
  );
}

function summarizeCreateResult(result) {
  return {
    created: result.created,
    status: result.status,
    id: result.id,
    code: result.code,
    message: result.message ? String(result.message).slice(0, 180) : null,
  };
}

async function seedDuplicateSourceQueue({
  runId,
  organizationId,
  workspaceId,
  userId,
}) {
  const suffix = runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const sourceId = randomUUID();
  const sourceName = `${QUEUE_PREFIX}${suffix}_source`;
  const createName = `${QUEUE_PREFIX}${suffix}_create`;
  const copyName = `Copy of ${sourceName}`;
  const fullRoles = ["manager", "reviewer", "annotator"];
  const sql = `
WITH inserted_queue AS (
  INSERT INTO model_hub_annotationqueue (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    description,
    instructions,
    status,
    assignment_strategy,
    annotations_required,
    reservation_timeout_minutes,
    requires_review,
    auto_assign,
    created_by_id,
    organization_id,
    workspace_id,
    is_default
  )
  VALUES (
    NOW(),
    NOW(),
    FALSE,
    NULL,
    ${sqlUuid(sourceId)},
    ${sqlTextLiteral(sourceName)},
    ${sqlTextLiteral("Browser smoke duplicate source")},
    ${sqlTextLiteral("Disposable annotation queue form fixture")},
    'active',
    'manual',
    1,
    60,
    FALSE,
    FALSE,
    ${sqlUuid(userId)},
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)},
    FALSE
  )
  RETURNING id
),
inserted_member AS (
  INSERT INTO model_hub_annotationqueueannotator (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    role,
    roles,
    queue_id,
    user_id
  )
  SELECT
    NOW(),
    NOW(),
    FALSE,
    NULL,
    gen_random_uuid(),
    'manager',
    ${sqlJsonLiteral(fullRoles)},
    id,
    ${sqlUuid(userId)}
  FROM inserted_queue
  RETURNING queue_id
)
SELECT json_build_object(
  'fixture_created', TRUE,
  'queue_count', (SELECT COUNT(*) FROM inserted_queue),
  'membership_count', (SELECT COUNT(*) FROM inserted_member)
);
`;
  const result = await runPostgresJson(sql);
  assert(
    result.fixture_created === true,
    "Duplicate source fixture insert failed.",
  );
  assert(
    Number(result.queue_count) === 1,
    "Expected one source queue fixture.",
  );
  assert(
    Number(result.membership_count) === 1,
    "Expected manager membership on source queue fixture.",
  );
  return {
    sourceId,
    sourceName,
    createName,
    copyName,
  };
}

async function loadFormJourneyAudit({
  organizationId,
  sourceName,
  createName,
  copyName,
}) {
  const sql = `
SELECT json_build_object(
  'source_queue_count', COUNT(*) FILTER (WHERE name = ${sqlTextLiteral(sourceName)}),
  'create_queue_count', COUNT(*) FILTER (WHERE name = ${sqlTextLiteral(createName)}),
  'copy_queue_count', COUNT(*) FILTER (WHERE name = ${sqlTextLiteral(copyName)}),
  'active_fixture_count', COUNT(*) FILTER (WHERE deleted = FALSE),
  'manager_membership_count', (
    SELECT COUNT(*)
    FROM model_hub_annotationqueueannotator qa
    JOIN model_hub_annotationqueue q ON q.id = qa.queue_id
    WHERE (${fixtureNamePredicate("q.name")})
      AND q.organization_id = ${sqlUuid(organizationId)}
      AND qa.deleted = FALSE
      AND qa.role = 'manager'
  )
)
FROM model_hub_annotationqueue
WHERE (${fixtureNamePredicate("name")})
  AND organization_id = ${sqlUuid(organizationId)};
`;
  return runPostgresJson(sql);
}

async function hardDeleteQueueFixturesByPrefix({ organizationId, evidence }) {
  const sql = `
WITH target_queues AS (
  SELECT id
  FROM model_hub_annotationqueue
  WHERE (${fixtureNamePredicate("name")})
    AND organization_id = ${sqlUuid(organizationId)}
),
deleted_members AS (
  DELETE FROM model_hub_annotationqueueannotator
  WHERE queue_id IN (SELECT id FROM target_queues)
  RETURNING id
),
deleted_labels AS (
  DELETE FROM model_hub_annotationqueuelabel
  WHERE queue_id IN (SELECT id FROM target_queues)
  RETURNING id
),
deleted_rules AS (
  DELETE FROM model_hub_automationrule
  WHERE queue_id IN (SELECT id FROM target_queues)
  RETURNING id
),
deleted_queue_items AS (
  DELETE FROM model_hub_queueitem
  WHERE queue_id IN (SELECT id FROM target_queues)
  RETURNING id
),
deleted_queues AS (
  DELETE FROM model_hub_annotationqueue
  WHERE id IN (SELECT id FROM target_queues)
  RETURNING id
)
SELECT json_build_object(
  'deleted_queue_count', (SELECT COUNT(*) FROM deleted_queues),
  'deleted_member_count', (SELECT COUNT(*) FROM deleted_members),
  'deleted_label_count', (SELECT COUNT(*) FROM deleted_labels),
  'deleted_rule_count', (SELECT COUNT(*) FROM deleted_rules),
  'deleted_queue_item_count', (SELECT COUNT(*) FROM deleted_queue_items),
  'remaining_queue_count', (
    SELECT COUNT(*)
    FROM model_hub_annotationqueue
    WHERE (${fixtureNamePredicate("name")})
      AND organization_id = ${sqlUuid(organizationId)}
      AND id NOT IN (SELECT id FROM deleted_queues)
  )
);
`;
  const result = await runPostgresJson(sql);
  if (
    Number(result.deleted_queue_count) > 0 ||
    Number(result.remaining_queue_count) > 0
  ) {
    evidence.push({
      cleanup: "hard delete annotation queue form fixtures",
      status: Number(result.remaining_queue_count) === 0 ? "passed" : "failed",
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

async function submitAnnotationQueueForm(page) {
  const submitted = await page.evaluate(() => {
    const forms = window.visibleElements("form");
    const form = forms.find((candidate) =>
      window
        .normalizeText(candidate.textContent)
        .includes("Create annotation queue"),
    );
    const submitter = form
      ? Array.from(form.querySelectorAll("button")).find(
          (button) =>
            window.normalizeText(button.textContent) ===
              "Create annotation queue" && !button.disabled,
        )
      : null;
    if (!form || !submitter) return false;
    const event =
      typeof SubmitEvent === "function"
        ? new SubmitEvent("submit", {
            bubbles: true,
            cancelable: true,
            submitter,
          })
        : new Event("submit", { bubbles: true, cancelable: true });
    form.dispatchEvent(event);
    return true;
  });
  assert(submitted, "Could not submit annotation queue form.");
}

async function setSearchValue(page, value) {
  await setInputValue(page, 'input[placeholder="Search"]', value);
}

async function setFieldByPlaceholder(page, placeholder, value) {
  await setInputValue(
    page,
    `input[placeholder="${cssEscape(placeholder)}"], textarea[placeholder="${cssEscape(
      placeholder,
    )}"]`,
    value,
  );
}

async function waitForFieldValue(page, placeholder, expectedValue) {
  await page.waitForFunction(
    ({ placeholder: fieldPlaceholder, expectedValue: value }) => {
      const fields = Array.from(
        document.querySelectorAll("input[placeholder], textarea[placeholder]"),
      );
      return fields.some(
        (field) =>
          field.placeholder === fieldPlaceholder && field.value === value,
      );
    },
    { timeout: 30000 },
    { placeholder, expectedValue },
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

async function openQueueActionsForName(page, name) {
  await waitForVisibleText(page, name, { exact: true });
  const target = await page.evaluate((queueName) => {
    const match = window
      .visibleElements()
      .find(
        (element) => window.normalizeText(element.textContent) === queueName,
      );
    const row = match?.closest('[role="row"], .ag-row, tr, [data-rowindex]');
    const button = row?.querySelector('button[aria-label="Queue actions"]');
    const rect = button?.getBoundingClientRect();
    if (!rect) return null;
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
  }, name);
  assert(target, `Could not find queue actions for: ${name}`);
  await page.mouse.click(target.x, target.y, { delay: 25 });
  await waitForVisibleText(page, "Duplicate", { exact: true });
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

function isAnnotationQueueApiUrl(rawUrl) {
  const url = new URL(rawUrl);
  return (
    url.origin ===
      new URL(process.env.API_BASE || "http://localhost:8003").origin &&
    url.pathname.startsWith("/model-hub/annotation-queues/")
  );
}

function isQueueListResponse(response, expectedQuery = {}) {
  if (response.request().method() !== "GET") return false;
  const url = new URL(response.url());
  if (!isAnnotationQueueApiUrl(response.url())) return false;
  if (url.pathname !== "/model-hub/annotation-queues/") return false;
  if (response.status() >= 400) return false;
  return Object.entries(expectedQuery).every(
    ([key, value]) => url.searchParams.get(key) === value,
  );
}

function fixtureNamePredicate(columnName) {
  return `${columnName} LIKE ${sqlTextLiteral(`${QUEUE_PREFIX}%`)} OR ${columnName} LIKE ${sqlTextLiteral(`${COPY_PREFIX}%`)}`;
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

function sqlJsonLiteral(value) {
  return `${sqlTextLiteral(JSON.stringify(value))}::jsonb`;
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
