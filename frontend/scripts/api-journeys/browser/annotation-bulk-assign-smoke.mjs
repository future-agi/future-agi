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
  skip,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const QUEUE_PREFIX = "ui_aq_assign_";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_BULK_ASSIGN_SCREENSHOT ||
  "/tmp/annotation-bulk-assign-smoke.png";
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
    await hardDeleteAssignmentFixturesByPrefix({
      organizationId: auth.organizationId,
      evidence: cleanupEvidence,
    });

    const source = await resolveTraceAndSpanSample(auth.client);
    fixture = await seedAssignmentQueueFixture({
      runId: auth.runId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      userId,
    });

    const added = await auth.client.post(
      apiPath("/model-hub/annotation-queues/{queue_id}/items/add-items/", {
        queue_id: fixture.queueId,
      }),
      {
        items: [
          { source_type: "trace", source_id: source.traceId },
          { source_type: "observation_span", source_id: source.spanId },
        ],
      },
    );
    assert(
      Number(added?.added) === 2,
      `Expected add-items to create two rows: ${JSON.stringify(added)}`,
    );

    const createdItems = await loadFixtureItems(auth.client, fixture.queueId);
    assert(
      createdItems.length === 2,
      `Expected two fixture queue items, saw ${createdItems.length}.`,
    );
    fixture.itemIds = createdItems.map((item) => item.id);

    const beforeAudit = await loadAssignmentAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      queueId: fixture.queueId,
      userId,
    });
    assertAssignmentAudit(beforeAudit, {
      expectedAssigned: 0,
      expectedItems: 2,
      label: "before assignment",
    });

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });
    page = await browser.newPage();
    page.setDefaultTimeout(45_000);
    page.setDefaultNavigationTimeout(45_000);
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
      "annotation queue detail load",
      (response) => isQueueDetailResponse(response, fixture.queueId),
      () =>
        page.goto(
          `${APP_BASE}/dashboard/annotations/queues/${fixture.queueId}`,
          { waitUntil: "domcontentloaded" },
        ),
    );
    await waitForPathIncludes(
      page,
      `/dashboard/annotations/queues/${fixture.queueId}`,
    );
    await waitForVisibleText(page, fixture.queueName, { exact: true });
    await waitForVisibleText(page, "2 of 2 items", { exact: true });

    const selected = await selectAllVisibleRows(page, 2);
    assert(selected >= 2, `Expected to select two rows, selected ${selected}.`);
    await waitForVisibleText(page, "Assign Selected (2)", { exact: true });

    await clickButtonWithText(page, "Assign Selected (2)");
    await waitForVisibleText(page, "Assign Selected Items", { exact: true });
    const selectedAnnotator = await selectFirstDialogCheckbox(page);

    const assignResponse = await waitForResponseDuring(
      page,
      "bulk assign selected queue items",
      (response) => isAssignResponse(response, fixture.queueId),
      () => clickDialogButtonWithText(page, "Assign"),
    );
    const assignPayload = await assignResponse.json();
    assert(
      Number(assignPayload?.result?.assigned ?? assignPayload?.assigned) === 2,
      `Bulk assign response did not assign two items: ${JSON.stringify(
        assignPayload,
      )}`,
    );
    await waitForNoDialog(page);

    const afterAudit = await loadAssignmentAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      queueId: fixture.queueId,
      userId,
    });
    assertAssignmentAudit(afterAudit, {
      expectedAssigned: 2,
      expectedItems: 2,
      label: "after assignment",
    });

    const myItems = asArray(
      await auth.client.get(
        apiPath("/model-hub/annotation-queues/{queue_id}/items/", {
          queue_id: fixture.queueId,
        }),
        {
          query: {
            assigned_to: "me",
            status: ["pending", "in_progress", "completed", "skipped"],
            limit: 25,
          },
        },
      ),
    );
    assert(
      myItems.length === 2 &&
        myItems.every((item) =>
          asArray(item.assigned_users).some(
            (assigned) => String(assigned.id) === String(userId),
          ),
        ),
      `Assigned-to-me API did not return both assigned items: ${JSON.stringify(
        myItems,
      )}`,
    );

    await waitForResponseDuring(
      page,
      "assigned-to-me filter refresh",
      (response) => isQueueItemsResponse(response, fixture.queueId, "me"),
      () => clickToggleButton(page, "My Items"),
    );
    await waitForVisibleText(page, "2 of 2 items", { exact: true });

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    const expectedMutation = `POST /model-hub/annotation-queues/${fixture.queueId}/items/assign/`;
    const mutationPaths = browserMutations.map(maskRequest);
    assert(
      mutationPaths.length === 1 && mutationPaths[0] === expectedMutation,
      `Unexpected browser mutations: ${mutationPaths.join(", ")}`,
    );
    assert(
      apiFailures.length === 0,
      `Unexpected annotation queue API failures: ${apiFailures.join(", ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    const queueEvidence = {
      id: fixture.queueId,
      name: fixture.queueName,
    };
    const cleanup = await hardDeleteAssignmentFixturesByPrefix({
      organizationId: auth.organizationId,
      evidence: cleanupEvidence,
    });
    assert(
      Number(cleanup.remaining_queue_count) === 0 &&
        Number(cleanup.remaining_item_count) === 0 &&
        Number(cleanup.remaining_assignment_count) === 0,
      `Annotation bulk assignment cleanup left residue: ${JSON.stringify(
        cleanup,
      )}`,
    );
    fixture = null;

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          queue_id: queueEvidence.id,
          queue_name: queueEvidence.name,
          source,
          item_ids: beforeAudit.item_ids,
          selected_annotator: selectedAnnotator,
          before_assignment: beforeAudit,
          after_assignment: afterAudit,
          assigned_to_me_count: myItems.length,
          model_hub_request_count: modelHubRequests.length,
          browser_mutations: mutationPaths,
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
    if (fixture) {
      await hardDeleteAssignmentFixturesByPrefix({
        organizationId: auth.organizationId,
        evidence: cleanupEvidence,
      }).catch(() => null);
    }
  }

  if (caughtError) throw caughtError;
}

async function loadFixtureItems(client, queueId) {
  return asArray(
    await client.get(
      apiPath("/model-hub/annotation-queues/{queue_id}/items/", {
        queue_id: queueId,
      }),
      {
        query: {
          status: ["pending", "in_progress", "completed", "skipped"],
          source_type: ["trace", "observation_span"],
          ordering: "-created_at",
          limit: 25,
        },
      },
    ),
  );
}

async function resolveTraceAndSpanSample(client) {
  const projects = asArray(
    await client.get(apiPath("/tracer/project/list_projects/"), {
      query: { page_number: 0, page_size: 25 },
    }),
  );
  const accessibleProjectIds = new Set(
    projects.map((project) => String(project?.id || "")).filter(Boolean),
  );

  const preferredTraceId = process.env.OBSERVE_TRACE_ID;
  const preferredSpanId = process.env.OBSERVE_SPAN_ID;
  if (preferredTraceId && preferredSpanId) {
    try {
      const detail = await client.get(
        apiPath("/tracer/trace/{id}/", { id: preferredTraceId }),
      );
      const projectId = traceProjectId(detail);
      const spans = flattenTraceEntries(detail);
      if (
        projectId &&
        accessibleProjectIds.has(String(projectId)) &&
        spans.some((row) => String(row.spanId) === preferredSpanId)
      ) {
        return {
          traceId: preferredTraceId,
          spanId: preferredSpanId,
          projectId,
        };
      }
    } catch {
      // Fall back to discovery below.
    }
  }

  for (const project of projects) {
    if (!project?.id) continue;
    const list = await client.get(
      apiPath("/tracer/trace/list_traces_of_session/"),
      {
        query: {
          project_id: project.id,
          page_number: 0,
          page_size: 10,
        },
      },
    );
    for (const trace of asArray(list.table || list)) {
      const traceId = trace.trace_id || trace.id;
      if (!traceId) continue;
      try {
        const detail = await client.get(
          apiPath("/tracer/trace/{id}/", { id: traceId }),
        );
        const span = flattenTraceEntries(detail).find((row) => row.spanId);
        if (span?.spanId) {
          return {
            traceId,
            spanId: span.spanId,
            projectId: traceProjectId(detail) || relatedId(trace.project),
          };
        }
      } catch {
        // Try the next trace.
      }
    }
  }

  skip(
    "No trace with a resolvable observation span is available for disposable queue assignment coverage.",
  );
}

async function seedAssignmentQueueFixture({
  runId,
  organizationId,
  workspaceId,
  userId,
}) {
  const suffix = runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const queueId = randomUUID();
  const queueName = `${QUEUE_PREFIX}${suffix}`;
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
    ${sqlUuid(queueId)},
    ${sqlTextLiteral(queueName)},
    ${sqlTextLiteral("Browser smoke manual assignment queue")},
    ${sqlTextLiteral("Disposable annotation queue bulk assignment fixture")},
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
  RETURNING id
)
SELECT json_build_object(
  'queue_id', (SELECT id FROM inserted_queue),
  'queue_name', ${sqlTextLiteral(queueName)},
  'member_count', (SELECT COUNT(*) FROM inserted_member)
);
`;
  const result = await runPostgresJson(sql);
  assert(
    result.queue_id === queueId,
    "Assignment queue fixture insert failed.",
  );
  assert(Number(result.member_count) === 1, "Expected one manager membership.");
  return { queueId, queueName, itemIds: [] };
}

async function loadAssignmentAudit({
  organizationId,
  workspaceId,
  queueId,
  userId,
}) {
  const sql = `
WITH target_items AS (
  SELECT id, source_type, status, assigned_to_id
  FROM model_hub_queueitem
  WHERE queue_id = ${sqlUuid(queueId)}
    AND deleted = FALSE
)
SELECT json_build_object(
  'queue_count', (
    SELECT COUNT(*)
    FROM model_hub_annotationqueue
    WHERE id = ${sqlUuid(queueId)}
      AND deleted = FALSE
      AND organization_id = ${sqlUuid(organizationId)}
      AND workspace_id = ${sqlUuid(workspaceId)}
  ),
  'item_count', (SELECT COUNT(*) FROM target_items),
  'item_ids', COALESCE((SELECT json_agg(id::text ORDER BY id::text) FROM target_items), '[]'::json),
  'source_types', COALESCE((SELECT json_agg(source_type ORDER BY source_type) FROM target_items), '[]'::json),
  'assigned_to_current_count', (
    SELECT COUNT(*) FROM target_items WHERE assigned_to_id = ${sqlUuid(userId)}
  ),
  'active_assignment_count', (
    SELECT COUNT(*)
    FROM model_hub_queueitemassignment qa
    WHERE qa.queue_item_id IN (SELECT id FROM target_items)
      AND qa.deleted = FALSE
  ),
  'current_user_assignment_count', (
    SELECT COUNT(*)
    FROM model_hub_queueitemassignment qa
    WHERE qa.queue_item_id IN (SELECT id FROM target_items)
      AND qa.user_id = ${sqlUuid(userId)}
      AND qa.deleted = FALSE
  ),
  'manager_membership_count', (
    SELECT COUNT(*)
    FROM model_hub_annotationqueueannotator member
    WHERE member.queue_id = ${sqlUuid(queueId)}
      AND member.user_id = ${sqlUuid(userId)}
      AND member.deleted = FALSE
      AND member.roles @> '["manager", "annotator"]'::jsonb
  )
);
`;
  return runPostgresJson(sql);
}

function assertAssignmentAudit(
  audit,
  { expectedAssigned, expectedItems, label },
) {
  assert(Number(audit.queue_count) === 1, `${label}: queue missing.`);
  assert(
    Number(audit.item_count) === expectedItems,
    `${label}: expected ${expectedItems} items: ${JSON.stringify(audit)}`,
  );
  assert(
    Number(audit.manager_membership_count) === 1,
    `${label}: manager/annotator membership missing.`,
  );
  assert(
    Number(audit.assigned_to_current_count) === expectedAssigned &&
      Number(audit.active_assignment_count) === expectedAssigned &&
      Number(audit.current_user_assignment_count) === expectedAssigned,
    `${label}: assignment audit mismatch: ${JSON.stringify(audit)}`,
  );
}

async function hardDeleteAssignmentFixturesByPrefix({
  organizationId,
  evidence,
}) {
  const sql = `
WITH target_queues AS (
  SELECT id
  FROM model_hub_annotationqueue
  WHERE name LIKE ${sqlTextLiteral(`${QUEUE_PREFIX}%`)}
    AND organization_id = ${sqlUuid(organizationId)}
),
target_items AS (
  SELECT id
  FROM model_hub_queueitem
  WHERE queue_id IN (SELECT id FROM target_queues)
),
deleted_review_mentions AS (
  DELETE FROM model_hub_queueitemreviewcomment_mentioned_users
  WHERE queueitemreviewcomment_id IN (
    SELECT id FROM model_hub_queueitemreviewcomment
    WHERE queue_item_id IN (SELECT id FROM target_items)
  )
  RETURNING queueitemreviewcomment_id
),
deleted_review_comments AS (
  DELETE FROM model_hub_queueitemreviewcomment
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_review_threads AS (
  DELETE FROM model_hub_queueitemreviewthread
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_assignments AS (
  DELETE FROM model_hub_queueitemassignment
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_scores AS (
  DELETE FROM model_hub_score
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_notes AS (
  DELETE FROM model_hub_queueitemnote
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
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
  WHERE id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_queues AS (
  DELETE FROM model_hub_annotationqueue
  WHERE id IN (SELECT id FROM target_queues)
  RETURNING id
)
SELECT json_build_object(
  'deleted_queue_count', (SELECT COUNT(*) FROM deleted_queues),
  'deleted_queue_ids', COALESCE((SELECT json_agg(id::text) FROM deleted_queues), '[]'::json),
  'deleted_item_count', (SELECT COUNT(*) FROM deleted_queue_items),
  'deleted_assignment_count', (SELECT COUNT(*) FROM deleted_assignments),
  'deleted_member_count', (SELECT COUNT(*) FROM deleted_members),
  'deleted_score_count', (SELECT COUNT(*) FROM deleted_scores),
  'deleted_note_count', (SELECT COUNT(*) FROM deleted_notes),
  'remaining_queue_count', (
    SELECT COUNT(*)
    FROM target_queues
    WHERE id NOT IN (SELECT id FROM deleted_queues)
  ),
  'remaining_item_count', (
    SELECT COUNT(*)
    FROM target_items
    WHERE id NOT IN (SELECT id FROM deleted_queue_items)
  ),
  'remaining_assignment_count', (
    SELECT COUNT(*)
    FROM model_hub_queueitemassignment
    WHERE queue_item_id IN (SELECT id FROM target_items)
      AND id NOT IN (SELECT id FROM deleted_assignments)
  )
);
`;
  const result = await runPostgresJson(sql);
  if (
    Number(result.deleted_queue_count) > 0 ||
    Number(result.remaining_queue_count) > 0 ||
    Number(result.remaining_assignment_count) > 0
  ) {
    evidence.push({
      cleanup: "hard delete annotation bulk assignment fixtures",
      status:
        Number(result.remaining_queue_count) === 0 &&
        Number(result.remaining_item_count) === 0 &&
        Number(result.remaining_assignment_count) === 0
          ? "passed"
          : "failed",
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
  assert(text, "Postgres assignment audit returned no JSON output.");
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

async function selectAllVisibleRows(page, expectedCount) {
  await page.waitForFunction(
    (count) =>
      document.querySelectorAll(".ag-center-cols-container .ag-row").length >=
      count,
    {},
    expectedCount,
  );
  const box = await page.evaluate(() => {
    const headerCell =
      document.querySelector(
        '.ag-header-cell[col-id="ag-Grid-SelectionColumn"]',
      ) || document.querySelector(".ag-header-cell");
    const rect = headerCell?.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return null;
    return {
      x: rect.left + Math.min(rect.width / 2, 24),
      y: rect.top + rect.height / 2,
    };
  });
  assert(box, "Could not locate the queue-item select-all checkbox.");
  await page.mouse.click(box.x, box.y);
  await page.waitForFunction(
    (count) =>
      document.querySelectorAll(".ag-row-selected").length >= count ||
      document.body.textContent.includes(`Assign Selected (${count})`),
    { timeout: 5000 },
    expectedCount,
  );
  return expectedCount;
}

async function selectFirstDialogCheckbox(page) {
  const selected = await page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"]');
    if (!dialog) return null;
    const labels = Array.from(dialog.querySelectorAll("label")).filter(
      (label) => label.querySelector('input[type="checkbox"]'),
    );
    const label = labels[0];
    const input = label?.querySelector('input[type="checkbox"]');
    if (!label || !input) return null;
    if (!input.checked) label.click();
    return window.normalizeText(label.textContent);
  });
  assert(selected, "Could not select an annotator in the bulk assign dialog.");
  await page.waitForFunction(() =>
    Boolean(
      document.querySelector('[role="dialog"] input[type="checkbox"]:checked'),
    ),
  );
  return selected;
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

async function waitForPathIncludes(page, pathName, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname.includes(expectedPath),
    { timeout },
    pathName,
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

async function waitForNoDialog(page) {
  await page.waitForFunction(() => !document.querySelector('[role="dialog"]'), {
    timeout: 30000,
  });
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

async function clickDialogButtonWithText(page, text) {
  await waitForVisibleText(page, text, { exact: true });
  const clicked = await page.evaluate((expectedText) => {
    const dialog = document.querySelector('[role="dialog"]');
    if (!dialog) return false;
    const button = Array.from(dialog.querySelectorAll("button")).find(
      (candidate) =>
        window.normalizeText(candidate.textContent) === expectedText &&
        !candidate.disabled,
    );
    if (!button) return false;
    button.click();
    return true;
  }, text);
  assert(clicked, `Could not click dialog button: ${text}`);
}

async function clickToggleButton(page, text) {
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
  assert(clicked, `Could not click toggle button: ${text}`);
}

function isQueueDetailResponse(response, queueId) {
  if (response.request().method() !== "GET") return false;
  if (response.status() >= 400) return false;
  const url = new URL(response.url());
  return (
    isAnnotationQueueApiUrl(response.url()) &&
    url.pathname === `/model-hub/annotation-queues/${queueId}/`
  );
}

function isQueueItemsResponse(response, queueId, assignedTo = null) {
  if (response.request().method() !== "GET") return false;
  if (response.status() >= 400) return false;
  const url = new URL(response.url());
  if (!isAnnotationQueueApiUrl(response.url())) return false;
  if (url.pathname !== `/model-hub/annotation-queues/${queueId}/items/`) {
    return false;
  }
  if (assignedTo !== null)
    return url.searchParams.get("assigned_to") === assignedTo;
  return true;
}

function isAssignResponse(response, queueId) {
  if (response.request().method() !== "POST") return false;
  if (response.status() >= 400) return false;
  const url = new URL(response.url());
  return (
    isAnnotationQueueApiUrl(response.url()) &&
    url.pathname === `/model-hub/annotation-queues/${queueId}/items/assign/`
  );
}

function isAnnotationQueueApiUrl(rawUrl) {
  const url = new URL(rawUrl);
  return (
    url.origin ===
      new URL(process.env.API_BASE || "http://localhost:8003").origin &&
    url.pathname.startsWith("/model-hub/annotation-queues/")
  );
}

function maskRequest(rawRequest) {
  const [method, rawUrl] = rawRequest.split(" ");
  const url = new URL(rawUrl);
  return `${method} ${url.pathname}`;
}

function traceProjectId(detail) {
  return relatedId(detail?.trace?.project || detail?.project);
}

function relatedId(value) {
  if (!value) return null;
  if (typeof value === "string") return value;
  return value.id || value.project_id || value.uuid || null;
}

function flattenTraceEntries(detail) {
  const roots = asArray(detail?.observation_spans).length
    ? asArray(detail.observation_spans)
    : [detail?.root || detail?.data || detail?.trace || detail];
  const rows = [];
  function walk(entry) {
    if (!entry || typeof entry !== "object") return;
    const span = entry.observation_span || entry.span || entry;
    const spanId = span?.id || span?.span_id;
    rows.push({ entry, spanId });
    for (const child of asArray(entry.children)) walk(child);
  }
  for (const root of roots) walk(root);
  return rows;
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlTextLiteral(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

function sqlJsonLiteral(value) {
  return `'${JSON.stringify(value).replace(/'/g, "''")}'::jsonb`;
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
  if (error?.name === "SkipJourney") {
    console.log(JSON.stringify({ status: "skipped", reason: error.reason }));
    return;
  }
  console.error(error);
  process.exitCode = 1;
});
