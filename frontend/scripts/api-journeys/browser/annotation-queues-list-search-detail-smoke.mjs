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
const QUEUE_PREFIX = "ui_aq_list_";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_QUEUES_LIST_SCREENSHOT ||
  "/tmp/annotation-queues-list-search-detail-smoke.png";
const FAILURE_SCREENSHOT_PATH = SCREENSHOT_PATH.replace(
  /\.png$/,
  "-failure.png",
);
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();

  const auth = await createAuthenticatedContext();
  const userId = currentUserId(auth.user);
  assert(isUuid(userId), "Authenticated user id could not be resolved.");

  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const modelHubRequests = [];
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
    fixture = await seedQueueFixtures({
      runId: auth.runId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      userId,
    });

    const apiAudit = await loadQueueFixtureAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      namePrefix: fixture.namePrefix,
    });
    assertQueueFixtureAudit(apiAudit, fixture);
    await assertQueuesVisibleViaApi(auth.client, fixture);

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
      if (!isAnnotationQueueApiUrl(url)) return;
      modelHubRequests.push(`${request.method()} ${url}`);
      if (MUTATION_METHODS.has(request.method())) {
        browserMutations.push(`${request.method()} ${url}`);
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isAnnotationQueueApiUrl(url) && response.status() >= 400) {
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
    await waitForPathIncludes(page, "/dashboard/annotations/queues");
    await waitForVisibleText(page, "Annotation queues", { exact: true });

    await waitForResponseDuring(
      page,
      "annotation queue search",
      (response) =>
        isQueueListResponse(response, {
          search: fixture.activeName,
          archived: "false",
        }),
      () => setSearchValue(page, fixture.activeName),
    );
    await waitForVisibleText(page, fixture.activeName, { exact: true });
    await waitForNoVisibleText(page, fixture.pausedName);
    await waitForNoVisibleText(page, fixture.archivedName);

    await waitForResponseDuring(
      page,
      "annotation queue paused status filter",
      (response) =>
        isQueueListResponse(response, {
          status: "paused",
          archived: "false",
        }),
      async () => {
        await setSearchValue(page, "");
        await selectQueueStatus(page, "paused");
      },
    );
    await waitForVisibleText(page, fixture.pausedName, { exact: true });
    await waitForNoVisibleText(page, fixture.activeName);

    await waitForResponseDuring(
      page,
      "annotation queue list reset before archived tab",
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
    await waitForResponseDuring(
      page,
      "annotation queue archived tab",
      (response) =>
        isQueueListResponse(response, {
          archived: "true",
        }),
      () => clickToggle(page, "archived"),
    );
    await waitForVisibleText(page, fixture.archivedName, { exact: true });
    await waitForNoVisibleText(page, fixture.activeName);

    await waitForResponseDuring(
      page,
      "annotation queue list reset before detail",
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
    await waitForResponseDuring(
      page,
      "annotation queue row search before detail",
      (response) =>
        isQueueListResponse(response, {
          search: fixture.activeName,
          archived: "false",
        }),
      () => setSearchValue(page, fixture.activeName),
    );

    await waitForResponseDuring(
      page,
      "annotation queue detail navigation",
      (response) =>
        response
          .url()
          .includes(`/model-hub/annotation-queues/${fixture.activeId}/`) &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () => clickQueueRowByName(page, fixture.activeName),
    );
    await waitForPathIncludes(
      page,
      `/dashboard/annotations/queues/${fixture.activeId}`,
    );
    await waitForVisibleText(page, fixture.activeName, { exact: true });

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      browserMutations.length === 0,
      `Unexpected browser mutations: ${browserMutations.map(maskRequest).join(", ")}`,
    );
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
      `Annotation queue cleanup left residue: ${JSON.stringify(cleanup)}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          queue_ids: {
            active: fixture.activeId,
            paused: fixture.pausedId,
            archived: fixture.archivedId,
          },
          db_audit: apiAudit,
          model_hub_request_count: modelHubRequests.length,
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
      const cleanup = await hardDeleteQueueFixturesByPrefix({
        organizationId: auth.organizationId,
        evidence: cleanupEvidence,
      });
      if (caughtError && Number(cleanup.remaining_queue_count) > 0) {
        caughtError.message = `${caughtError.message}; cleanup residue: ${JSON.stringify(
          cleanup,
        )}`;
      }
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

async function assertQueuesVisibleViaApi(client, fixture) {
  const activeRows = asArray(
    await client.get(apiPath("/model-hub/annotation-queues/"), {
      query: {
        page: 1,
        limit: 10,
        include_counts: true,
        archived: false,
        search: fixture.activeName,
      },
    }),
  );
  assert(
    activeRows.some((row) => row.id === fixture.activeId),
    "Seeded active queue was not visible through the list API.",
  );
  assert(
    activeRows.every((row) => row.id !== fixture.archivedId),
    "Archived queue leaked into the active list API.",
  );

  const pausedRows = asArray(
    await client.get(apiPath("/model-hub/annotation-queues/"), {
      query: {
        page: 1,
        limit: 10,
        include_counts: true,
        archived: false,
        status: "paused",
        search: fixture.namePrefix,
      },
    }),
  );
  assert(
    pausedRows.some((row) => row.id === fixture.pausedId),
    "Seeded paused queue was not visible through the paused list API.",
  );

  const archivedRows = asArray(
    await client.get(apiPath("/model-hub/annotation-queues/"), {
      query: {
        page: 1,
        limit: 10,
        include_counts: true,
        archived: true,
        search: fixture.namePrefix,
      },
    }),
  );
  assert(
    archivedRows.some((row) => row.id === fixture.archivedId),
    "Seeded archived queue was not visible through the archived list API.",
  );
}

async function seedQueueFixtures({
  runId,
  organizationId,
  workspaceId,
  userId,
}) {
  const suffix = runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const activeId = randomUUID();
  const pausedId = randomUUID();
  const archivedId = randomUUID();
  const namePrefix = `${QUEUE_PREFIX}${suffix}_`;
  const activeName = `${namePrefix}active`;
  const pausedName = `${namePrefix}paused`;
  const archivedName = `${namePrefix}archived`;
  const fullRoles = ["manager", "reviewer", "annotator"];
  const sql = `
WITH inserted_queues AS (
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
  VALUES
    (
      NOW(),
      NOW(),
      FALSE,
      NULL,
      ${sqlUuid(activeId)},
      ${sqlTextLiteral(activeName)},
      ${sqlTextLiteral("Browser smoke active queue")},
      ${sqlTextLiteral("Disposable annotation queue list fixture")},
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
    ),
    (
      NOW() - INTERVAL '1 second',
      NOW() - INTERVAL '1 second',
      FALSE,
      NULL,
      ${sqlUuid(pausedId)},
      ${sqlTextLiteral(pausedName)},
      ${sqlTextLiteral("Browser smoke paused queue")},
      ${sqlTextLiteral("Disposable annotation queue status fixture")},
      'paused',
      'manual',
      1,
      60,
      FALSE,
      FALSE,
      ${sqlUuid(userId)},
      ${sqlUuid(organizationId)},
      ${sqlUuid(workspaceId)},
      FALSE
    ),
    (
      NOW() - INTERVAL '2 seconds',
      NOW() - INTERVAL '2 seconds',
      TRUE,
      NOW() - INTERVAL '2 seconds',
      ${sqlUuid(archivedId)},
      ${sqlTextLiteral(archivedName)},
      ${sqlTextLiteral("Browser smoke archived queue")},
      ${sqlTextLiteral("Disposable annotation queue archived fixture")},
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
inserted_members AS (
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
  FROM inserted_queues
  RETURNING queue_id
)
SELECT json_build_object(
  'fixture_created', TRUE,
  'queue_count', (SELECT COUNT(*) FROM inserted_queues),
  'membership_count', (SELECT COUNT(*) FROM inserted_members)
);
`;
  const result = await runPostgresJson(sql);
  assert(
    result.fixture_created === true,
    "Annotation queue fixture insert failed.",
  );
  assert(Number(result.queue_count) === 3, "Expected three queue fixtures.");
  assert(
    Number(result.membership_count) === 3,
    "Expected manager membership on each queue fixture.",
  );
  return {
    namePrefix,
    activeId,
    pausedId,
    archivedId,
    activeName,
    pausedName,
    archivedName,
  };
}

async function loadQueueFixtureAudit({
  organizationId,
  workspaceId,
  namePrefix,
}) {
  const sql = `
SELECT json_build_object(
  'queue_count', COUNT(*),
  'active_count', COUNT(*) FILTER (WHERE deleted = FALSE AND status = 'active'),
  'paused_count', COUNT(*) FILTER (WHERE deleted = FALSE AND status = 'paused'),
  'archived_count', COUNT(*) FILTER (WHERE deleted = TRUE),
  'workspace_match_count', COUNT(*) FILTER (WHERE workspace_id = ${sqlUuid(
    workspaceId,
  )}),
  'organization_match_count', COUNT(*) FILTER (WHERE organization_id = ${sqlUuid(
    organizationId,
  )}),
  'manager_membership_count', (
    SELECT COUNT(*)
    FROM model_hub_annotationqueueannotator qa
    JOIN model_hub_annotationqueue q ON q.id = qa.queue_id
    WHERE q.name LIKE ${sqlTextLiteral(`${namePrefix}%`)}
      AND q.organization_id = ${sqlUuid(organizationId)}
      AND qa.deleted = FALSE
      AND qa.role = 'manager'
      AND qa.roles @> '["manager"]'::jsonb
  ),
  'names', COALESCE(json_agg(name ORDER BY created_at DESC), '[]'::json)
)
FROM model_hub_annotationqueue
WHERE name LIKE ${sqlTextLiteral(`${namePrefix}%`)}
  AND organization_id = ${sqlUuid(organizationId)};
`;
  return runPostgresJson(sql);
}

function assertQueueFixtureAudit(audit, fixture) {
  assert(
    Number(audit.queue_count) === 3,
    `Queue fixture audit mismatch: ${JSON.stringify(audit)}`,
  );
  assert(
    Number(audit.active_count) === 1,
    "Expected one active queue fixture.",
  );
  assert(
    Number(audit.paused_count) === 1,
    "Expected one paused queue fixture.",
  );
  assert(
    Number(audit.archived_count) === 1,
    "Expected one archived queue fixture.",
  );
  assert(
    Number(audit.workspace_match_count) === 3 &&
      Number(audit.organization_match_count) === 3,
    `Queue fixture context mismatch: ${JSON.stringify(audit)}`,
  );
  assert(
    Number(audit.manager_membership_count) === 3,
    "Expected manager membership for all queue fixtures.",
  );
  for (const name of [
    fixture.activeName,
    fixture.pausedName,
    fixture.archivedName,
  ]) {
    assert(
      audit.names.includes(name),
      `Fixture queue missing from DB audit: ${name}`,
    );
  }
}

async function hardDeleteQueueFixturesByPrefix({ organizationId, evidence }) {
  const sql = `
WITH target_queues AS (
  SELECT id
  FROM model_hub_annotationqueue
  WHERE name LIKE ${sqlTextLiteral(`${QUEUE_PREFIX}%`)}
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
    WHERE name LIKE ${sqlTextLiteral(`${QUEUE_PREFIX}%`)}
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
      cleanup: "hard delete annotation queue list fixtures",
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

async function waitForPathIncludes(page, pathname, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname.includes(expectedPath),
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

async function setSearchValue(page, value) {
  await page.waitForSelector('input[placeholder="Search"]', {
    timeout: 30000,
  });
  await page.evaluate((nextValue) => {
    const input = document.querySelector('input[placeholder="Search"]');
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value",
    ).set;
    setter.call(input, nextValue);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }, value);
}

async function selectQueueStatus(page, value) {
  await page.waitForSelector('[role="combobox"]', { timeout: 30000 });
  await page.evaluate(() => {
    const combos = Array.from(document.querySelectorAll('[role="combobox"]'));
    const combo = combos.find((element) =>
      ["All Statuses", "Draft", "Active", "Paused", "Completed"].includes(
        window.normalizeText(element.textContent),
      ),
    );
    if (!combo)
      throw new Error("Could not find annotation queue status filter.");
    window.dispatchClick(combo);
  });
  await page.waitForSelector(`[role="option"][data-value="${value}"]`, {
    timeout: 30000,
  });
  await page.click(`[role="option"][data-value="${value}"]`);
}

async function clickToggle(page, value) {
  await page.waitForSelector(`button[value="${value}"]`, { timeout: 30000 });
  await page.click(`button[value="${value}"]`);
}

async function clickQueueRowByName(page, name) {
  await waitForVisibleText(page, name, { exact: true });
  const clickTarget = await page.evaluate((queueName) => {
    const match = window
      .visibleElements()
      .find(
        (element) => window.normalizeText(element.textContent) === queueName,
      );
    const row =
      match?.closest('[role="row"], .ag-row, tr, [data-rowindex]') || match;
    const cell = match?.closest('[role="gridcell"], .ag-cell') || match;
    const target = cell || row;
    const rect = target?.getBoundingClientRect();
    if (!rect) return null;
    return {
      x: rect.left + Math.min(rect.width / 2, 80),
      y: rect.top + rect.height / 2,
    };
  }, name);
  assert(clickTarget, `Could not click queue row: ${name}`);
  await page.mouse.click(clickTarget.x, clickTarget.y, { delay: 25 });
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
