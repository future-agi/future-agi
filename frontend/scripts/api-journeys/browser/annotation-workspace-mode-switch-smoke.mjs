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
const QUEUE_PREFIX = "ui_aq_mode_";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_WORKSPACE_MODE_SCREENSHOT ||
  "/tmp/annotation-workspace-mode-switch-smoke.png";
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
  const annotationRequests = [];
  const browserMutations = [];
  let browser = null;
  let page = null;
  let fixture = null;
  let caughtError = null;

  try {
    await hardDeleteModeFixturesByPrefix({
      organizationId: auth.organizationId,
      evidence: cleanupEvidence,
    });

    const source = await resolveTraceAndSpanSample(auth.client);
    fixture = await seedModeSwitchFixture({
      runId: auth.runId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      userId,
      source,
    });

    const apiAudit = await loadModeFixtureAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      queueId: fixture.queueId,
      userId,
    });
    assertModeFixtureAudit(apiAudit, fixture);
    const apiEvidence = await assertModeApis(auth.client, fixture);

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });
    page = await browser.newPage();
    page.setDefaultTimeout(60_000);
    page.setDefaultNavigationTimeout(60_000);
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installBrowserState(page, auth);

    page.on("request", (request) => {
      const url = request.url();
      if (!isAnnotationQueueApiUrl(url)) return;
      annotationRequests.push(maskRequest(`${request.method()} ${url}`));
      if (MUTATION_METHODS.has(request.method())) {
        browserMutations.push(maskRequest(`${request.method()} ${url}`));
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
      "annotate-mode next item",
      (response) =>
        isNextItemResponse(response, fixture.queueId, {
          exclude_review_status: "pending_review",
        }),
      () =>
        page.goto(
          `${APP_BASE}/dashboard/annotations/queues/${fixture.queueId}/annotate?mode=annotate`,
          { waitUntil: "domcontentloaded" },
        ),
    );
    await waitForPathIncludes(
      page,
      `/dashboard/annotations/queues/${fixture.queueId}/annotate`,
    );
    await waitForVisibleText(page, "Workspace action", { exact: true });
    await waitForVisibleText(page, "Annotate my answers", { exact: true });
    await waitForVisibleText(page, "Review submissions", { exact: true });
    await waitForVisibleText(page, "Submit for Review", { exact: true });
    await waitForVisibleText(page, fixture.labelName, { exact: false });
    await assertTogglePressed(page, "Annotate my answers");

    const annotateModeState = await collectModeState(page);
    assert(
      annotateModeState.submitButtonVisible,
      `Annotate mode did not show the submit action: ${JSON.stringify(
        annotateModeState,
      )}`,
    );

    await waitForResponseDuring(
      page,
      "review-mode next item",
      (response) =>
        isNextItemResponse(response, fixture.queueId, {
          view_mode: "review",
          review_status: "pending_review",
        }),
      () => clickToggleButton(page, "Review submissions"),
    );
    await waitForLocationSearchParam(page, "mode", "review");
    await waitForVisibleText(page, "Review Annotations", { exact: true });
    await waitForVisibleText(page, fixture.reviewValueText, { exact: false });
    await assertTogglePressed(page, "Review submissions");

    const reviewModeState = await collectModeState(page);
    if (fixture.hasAlternateAnnotator) {
      await waitForVisibleText(page, "Ready for review", { exact: true });
      await waitForVisibleText(page, "Approve", { exact: true });
      await waitForVisibleText(page, "Request changes", { exact: true });
      assert(
        reviewModeState.reviewActionBarVisible,
        `Review mode did not expose reviewer actions: ${JSON.stringify(
          reviewModeState,
        )}`,
      );
    } else {
      await waitForVisibleText(page, "In review", { exact: true });
      await waitForVisibleText(
        page,
        "You submitted annotations for this item",
        { exact: false },
      );
    }

    await waitForRequestSeen(
      annotationRequests,
      (request) =>
        request.includes("/annotate-detail/") &&
        request.includes("exclude_review_status=pending_review"),
    );
    await waitForRequestSeen(
      annotationRequests,
      (request) =>
        request.includes("/annotate-detail/") &&
        request.includes("view_mode=review") &&
        request.includes("review_status=pending_review"),
    );

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      browserMutations.length === 0,
      `Unexpected browser mutations: ${browserMutations.join(", ")}`,
    );
    assert(
      apiFailures.length === 0,
      `Unexpected annotation queue API failures: ${apiFailures.join(", ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    const cleanup = await hardDeleteModeFixturesByPrefix({
      organizationId: auth.organizationId,
      evidence: cleanupEvidence,
    });
    assert(
      Number(cleanup.remaining_queue_count) === 0 &&
        Number(cleanup.remaining_item_count) === 0 &&
        Number(cleanup.remaining_score_count) === 0,
      `Annotation mode-switch cleanup left residue: ${JSON.stringify(cleanup)}`,
    );
    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          queue_id: apiAudit.queue_id,
          queue_name: fixture?.queueName || apiAudit.queue_name,
          pending_item_id: apiEvidence.annotate_next_item_id,
          review_item_id: apiEvidence.review_next_item_id,
          review_annotator: {
            id: apiAudit.review_annotator_id,
            email: apiAudit.review_annotator_email,
            is_alternate: fixture?.hasAlternateAnnotator,
          },
          browser_request_count: annotationRequests.length,
          browser_mutations: browserMutations,
          annotate_mode_state: annotateModeState,
          review_mode_state: reviewModeState,
          screenshot: SCREENSHOT_PATH,
          source,
          cleanup: cleanupEvidence,
        },
        null,
        2,
      ),
    );
    fixture = null;
  } catch (error) {
    if (error?.name === "SkipJourney") {
      throw error;
    }
    caughtError = error;
    const domDebug = page
      ? await page
          .evaluate(() => ({
            url: window.location.href,
            text: document.body?.innerText?.slice(0, 3000) || "",
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
          browser_mutations: browserMutations,
          requests: annotationRequests,
          screenshot: FAILURE_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
  } finally {
    if (browser) await browser.close();
    if (fixture) {
      await hardDeleteModeFixturesByPrefix({
        organizationId: auth.organizationId,
        evidence: cleanupEvidence,
      }).catch(() => null);
    }
  }

  if (caughtError) throw caughtError;
}

async function assertModeApis(client, fixture) {
  const queue = await client.get(
    apiPath("/model-hub/annotation-queues/{id}/", { id: fixture.queueId }),
  );
  assert(
    asArray(queue.viewer_roles).includes("manager") &&
      asArray(queue.viewer_roles).includes("reviewer") &&
      asArray(queue.viewer_roles).includes("annotator"),
    `Queue detail did not expose all viewer roles: ${JSON.stringify(
      queue.viewer_roles,
    )}`,
  );

  const annotateNext = await client.get(
    apiPath("/model-hub/annotation-queues/{queue_id}/items/next-item/", {
      queue_id: fixture.queueId,
    }),
    { query: { exclude_review_status: "pending_review" } },
  );
  assert(
    String(annotateNext.item?.id) === String(fixture.pendingItemId),
    `Annotate navigation chose the wrong item: ${JSON.stringify(annotateNext)}`,
  );

  const reviewNext = await client.get(
    apiPath("/model-hub/annotation-queues/{queue_id}/items/next-item/", {
      queue_id: fixture.queueId,
    }),
    { query: { view_mode: "review", review_status: "pending_review" } },
  );
  assert(
    String(reviewNext.item?.id) === String(fixture.reviewItemId),
    `Review navigation chose the wrong item: ${JSON.stringify(reviewNext)}`,
  );

  const reviewDetail = await client.get(
    apiPath(
      "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
      {
        queue_id: fixture.queueId,
        id: fixture.reviewItemId,
      },
    ),
    { query: { view_mode: "review", review_status: "pending_review" } },
  );
  assert(
    reviewDetail.item?.review_status === "pending_review" &&
      asArray(reviewDetail.annotations).some(
        (annotation) =>
          String(annotation.annotator) === String(fixture.reviewAnnotatorId) &&
          annotation.value?.text === fixture.reviewValueText,
      ),
    `Review detail did not expose the seeded submitted answer: ${JSON.stringify(
      reviewDetail,
    )}`,
  );

  return {
    annotate_next_item_id: annotateNext.item?.id,
    review_next_item_id: reviewNext.item?.id,
    review_annotation_count: asArray(reviewDetail.annotations).length,
  };
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

  return {
    traceId: null,
    spanId: null,
    projectId: null,
    source: "nullable-source-fallback",
    reason: "No trace with a resolvable observation span is available.",
  };
}

async function seedModeSwitchFixture({
  runId,
  organizationId,
  workspaceId,
  userId,
  source,
}) {
  const suffix = runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const queueId = randomUUID();
  const labelId = randomUUID();
  const queueLabelId = randomUUID();
  const memberId = randomUUID();
  const altMemberId = randomUUID();
  const pendingItemId = randomUUID();
  const reviewItemId = randomUUID();
  const scoreId = randomUUID();
  const queueName = `${QUEUE_PREFIX}${suffix}`;
  const labelName = `${QUEUE_PREFIX}${suffix}_text`;
  const reviewValueText = `Mode switch review answer ${suffix}`;
  const labelSettings = {
    placeholder: "Mode switch answer",
    min_length: 0,
    max_length: 500,
  };
  const metadata = {
    journey: "annotation-workspace-mode-switch-smoke",
    run_id: runId,
  };

  const sql = `
WITH alternate_user AS (
  SELECT u.id, u.email
  FROM accounts_user u
  LEFT JOIN accounts_workspacemembership wm
    ON wm.user_id = u.id
   AND wm.workspace_id = ${sqlUuid(workspaceId)}
   AND wm.deleted = false
   AND wm.is_active = true
  LEFT JOIN accounts_organization_membership om
    ON om.user_id = u.id
   AND om.organization_id = ${sqlUuid(organizationId)}
   AND om.deleted = false
   AND om.is_active = true
  WHERE u.id <> ${sqlUuid(userId)}
    AND u.is_active = true
  ORDER BY
    CASE WHEN wm.id IS NOT NULL THEN 0 ELSE 1 END,
    CASE
      WHEN u.organization_id = ${sqlUuid(organizationId)} OR om.id IS NOT NULL
      THEN 0
      ELSE 1
    END,
    u.email ASC
  LIMIT 1
),
fixture_user AS (
  SELECT
    COALESCE((SELECT id FROM alternate_user), ${sqlUuid(userId)}) AS review_annotator_id,
    COALESCE((SELECT email FROM alternate_user), (SELECT email FROM accounts_user WHERE id = ${sqlUuid(userId)})) AS review_annotator_email,
    EXISTS(SELECT 1 FROM alternate_user) AS has_alternate_annotator
),
inserted_label AS (
  INSERT INTO model_hub_annotationslabels (
    created_at, updated_at, deleted, deleted_at, id, name, type, settings,
    description, organization_id, project_id, workspace_id, metadata, allow_notes
  )
  VALUES (
    now(), now(), false, NULL,
    ${sqlUuid(labelId)},
    ${sqlText(labelName)},
    'text',
    ${sqlJson(labelSettings)},
    ${sqlText("Disposable text label for annotation workspace mode switch coverage.")},
    ${sqlUuid(organizationId)},
    NULL,
    ${sqlUuid(workspaceId)},
    ${sqlJson(metadata)},
    true
  )
  RETURNING id
),
inserted_queue AS (
  INSERT INTO model_hub_annotationqueue (
    created_at, updated_at, deleted, deleted_at, id, name, description,
    instructions, status, assignment_strategy, annotations_required,
    reservation_timeout_minutes, requires_review, created_by_id,
    organization_id, workspace_id, project_id, is_default, dataset_id,
    agent_definition_id, auto_assign
  )
  VALUES (
    now(), now(), false, NULL,
    ${sqlUuid(queueId)},
    ${sqlText(queueName)},
    ${sqlText("Disposable queue for annotation workspace mode switch coverage.")},
    ${sqlText("Verify a user with all roles can switch annotate and review workspaces.")},
    'active',
    'manual',
    1,
    60,
    true,
    ${sqlUuid(userId)},
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)},
    NULL,
    false,
    NULL,
    NULL,
    true
  )
  RETURNING id
),
inserted_queue_label AS (
  INSERT INTO model_hub_annotationqueuelabel (
    created_at, updated_at, deleted, deleted_at, id, required, "order", label_id, queue_id
  )
  SELECT
    now(), now(), false, NULL,
    ${sqlUuid(queueLabelId)},
    true,
    0,
    inserted_label.id,
    inserted_queue.id
  FROM inserted_label, inserted_queue
  RETURNING id
),
inserted_member AS (
  INSERT INTO model_hub_annotationqueueannotator (
    created_at, updated_at, deleted, deleted_at, id, role, roles, queue_id, user_id
  )
  SELECT
    now(), now(), false, NULL,
    ${sqlUuid(memberId)},
    'manager',
    ${sqlJson(["manager", "reviewer", "annotator"])},
    inserted_queue.id,
    ${sqlUuid(userId)}
  FROM inserted_queue
  RETURNING id
),
inserted_alt_member AS (
  INSERT INTO model_hub_annotationqueueannotator (
    created_at, updated_at, deleted, deleted_at, id, role, roles, queue_id, user_id
  )
  SELECT
    now(), now(), false, NULL,
    ${sqlUuid(altMemberId)},
    'annotator',
    ${sqlJson(["annotator"])},
    inserted_queue.id,
    fixture_user.review_annotator_id
  FROM inserted_queue, fixture_user
  WHERE fixture_user.has_alternate_annotator = true
  RETURNING id
),
inserted_items AS (
  INSERT INTO model_hub_queueitem (
    created_at, updated_at, deleted, deleted_at, id, source_type, status,
    priority, "order", metadata, reserved_at, reservation_expires_at,
    review_status, reviewed_at, review_notes, assigned_to_id, call_execution_id,
    dataset_row_id, observation_span_id, organization_id, prototype_run_id,
    queue_id, reserved_by_id, reviewed_by_id, trace_id, workspace_id,
    trace_session_id
  )
  VALUES
    (
      now() + interval '2 seconds', now() + interval '2 seconds', false, NULL,
      ${sqlUuid(pendingItemId)},
      'trace',
      'pending',
      10,
      0,
      ${sqlJson({ ...metadata, mode_item: "annotate" })},
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      ${sqlUuid(organizationId)},
      NULL,
      ${sqlUuid(queueId)},
      NULL,
      NULL,
      ${sqlNullableUuid(source.traceId)},
      ${sqlUuid(workspaceId)},
      NULL
    ),
    (
      now(), now(), false, NULL,
      ${sqlUuid(reviewItemId)},
      'observation_span',
      'in_progress',
      20,
      1,
      ${sqlJson({ ...metadata, mode_item: "review" })},
      NULL,
      NULL,
      'pending_review',
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      ${sqlNullableUuid(source.spanId)},
      ${sqlUuid(organizationId)},
      NULL,
      ${sqlUuid(queueId)},
      NULL,
      NULL,
      NULL,
      ${sqlUuid(workspaceId)},
      NULL
    )
  RETURNING id
),
inserted_score AS (
  INSERT INTO model_hub_score (
    created_at, updated_at, deleted, deleted_at, id, source_type, value,
    score_source, notes, annotator_id, call_execution_id, dataset_row_id,
    label_id, observation_span_id, organization_id, project_id, prototype_run_id,
    queue_item_id, trace_id, trace_session_id, workspace_id, value_history
  )
  SELECT
    now(), now(), false, NULL,
    ${sqlUuid(scoreId)},
    'observation_span',
    ${sqlJson({ text: reviewValueText })},
    'human',
    ${sqlText("Submitted answer for annotation workspace mode switch coverage.")},
    fixture_user.review_annotator_id,
    NULL,
    NULL,
    ${sqlUuid(labelId)},
    ${sqlNullableUuid(source.spanId)},
    ${sqlUuid(organizationId)},
    NULL,
    NULL,
    ${sqlUuid(reviewItemId)},
    NULL,
    NULL,
    ${sqlUuid(workspaceId)},
    '[]'::jsonb
  FROM fixture_user
  RETURNING id
)
SELECT json_build_object(
  'queue_id', ${sqlText(queueId)},
  'queue_name', ${sqlText(queueName)},
  'label_id', ${sqlText(labelId)},
  'label_name', ${sqlText(labelName)},
  'pending_item_id', ${sqlText(pendingItemId)},
  'review_item_id', ${sqlText(reviewItemId)},
  'score_id', ${sqlText(scoreId)},
  'review_value_text', ${sqlText(reviewValueText)},
  'review_annotator_id', (SELECT review_annotator_id::text FROM fixture_user),
  'review_annotator_email', (SELECT review_annotator_email FROM fixture_user),
  'has_alternate_annotator', (SELECT has_alternate_annotator FROM fixture_user),
  'member_count',
    (SELECT count(*) FROM inserted_member) + (SELECT count(*) FROM inserted_alt_member),
  'item_count', (SELECT count(*) FROM inserted_items),
  'score_count', (SELECT count(*) FROM inserted_score)
)::text;
`;
  const result = await runPostgresJson(sql);
  assert(
    result.queue_id === queueId,
    "Mode-switch queue fixture insert failed.",
  );
  assert(Number(result.item_count) === 2, "Expected two mode-switch items.");
  assert(Number(result.score_count) === 1, "Expected one review score.");
  return {
    queueId,
    queueName,
    labelId,
    labelName,
    pendingItemId,
    reviewItemId,
    scoreId,
    reviewValueText,
    reviewAnnotatorId: result.review_annotator_id,
    reviewAnnotatorEmail: result.review_annotator_email,
    hasAlternateAnnotator: Boolean(result.has_alternate_annotator),
  };
}

async function loadModeFixtureAudit({
  organizationId,
  workspaceId,
  queueId,
  userId,
}) {
  const sql = `
WITH target_queue AS (
  SELECT id, name, requires_review, auto_assign, status
  FROM model_hub_annotationqueue
  WHERE id = ${sqlUuid(queueId)}
    AND deleted = false
    AND organization_id = ${sqlUuid(organizationId)}
    AND workspace_id = ${sqlUuid(workspaceId)}
),
target_items AS (
  SELECT id, status, review_status, source_type, "order"
  FROM model_hub_queueitem
  WHERE queue_id IN (SELECT id FROM target_queue)
    AND deleted = false
),
target_scores AS (
  SELECT s.id, s.queue_item_id, s.annotator_id, u.email AS annotator_email, s.value
  FROM model_hub_score s
  LEFT JOIN accounts_user u ON u.id = s.annotator_id
  WHERE s.queue_item_id IN (SELECT id FROM target_items)
    AND s.deleted = false
)
SELECT json_build_object(
  'queue_id', (SELECT id::text FROM target_queue),
  'queue_name', (SELECT name FROM target_queue),
  'requires_review', (SELECT requires_review FROM target_queue),
  'auto_assign', (SELECT auto_assign FROM target_queue),
  'status', (SELECT status FROM target_queue),
  'item_count', (SELECT count(*) FROM target_items),
  'pending_item_count', (
    SELECT count(*) FROM target_items
    WHERE status = 'pending' AND review_status IS NULL
  ),
  'pending_review_item_count', (
    SELECT count(*) FROM target_items
    WHERE status = 'in_progress' AND review_status = 'pending_review'
  ),
  'score_count', (SELECT count(*) FROM target_scores),
  'review_annotator_id', (SELECT annotator_id::text FROM target_scores LIMIT 1),
  'review_annotator_email', (SELECT annotator_email FROM target_scores LIMIT 1),
  'current_member_count', (
    SELECT count(*)
    FROM model_hub_annotationqueueannotator
    WHERE queue_id IN (SELECT id FROM target_queue)
      AND user_id = ${sqlUuid(userId)}
      AND deleted = false
      AND roles @> '["manager", "reviewer", "annotator"]'::jsonb
  ),
  'member_count', (
    SELECT count(*)
    FROM model_hub_annotationqueueannotator
    WHERE queue_id IN (SELECT id FROM target_queue)
      AND deleted = false
  )
)::text;
`;
  return runPostgresJson(sql);
}

function assertModeFixtureAudit(audit, fixture) {
  assert(audit.queue_id === fixture.queueId, "Mode-switch queue is missing.");
  assert(
    audit.requires_review === true && audit.auto_assign === true,
    `Mode-switch queue flags are wrong: ${JSON.stringify(audit)}`,
  );
  assert(
    Number(audit.current_member_count) === 1,
    `Current user all-role membership missing: ${JSON.stringify(audit)}`,
  );
  assert(
    Number(audit.item_count) === 2 &&
      Number(audit.pending_item_count) === 1 &&
      Number(audit.pending_review_item_count) === 1 &&
      Number(audit.score_count) === 1,
    `Mode-switch item/score audit mismatch: ${JSON.stringify(audit)}`,
  );
  assert(
    String(audit.review_annotator_id) === String(fixture.reviewAnnotatorId),
    `Review annotator mismatch: ${JSON.stringify(audit)}`,
  );
}

async function hardDeleteModeFixturesByPrefix({
  organizationId,
  evidence = [],
}) {
  const sql = `
WITH target_queues AS (
  SELECT id
  FROM model_hub_annotationqueue
  WHERE name LIKE ${sqlText(`${QUEUE_PREFIX}%`)}
    AND organization_id = ${sqlUuid(organizationId)}
),
target_labels AS (
  SELECT id
  FROM model_hub_annotationslabels
  WHERE name LIKE ${sqlText(`${QUEUE_PREFIX}%`)}
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
     OR label_id IN (SELECT id FROM target_labels)
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
deleted_queue_labels AS (
  DELETE FROM model_hub_annotationqueuelabel
  WHERE queue_id IN (SELECT id FROM target_queues)
     OR label_id IN (SELECT id FROM target_labels)
  RETURNING id
),
deleted_items AS (
  DELETE FROM model_hub_queueitem
  WHERE id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_queues AS (
  DELETE FROM model_hub_annotationqueue
  WHERE id IN (SELECT id FROM target_queues)
  RETURNING id
),
deleted_labels AS (
  DELETE FROM model_hub_annotationslabels
  WHERE id IN (SELECT id FROM target_labels)
  RETURNING id
)
SELECT json_build_object(
  'deleted_queue_count', (SELECT count(*) FROM deleted_queues),
  'deleted_label_count', (SELECT count(*) FROM deleted_labels),
  'deleted_item_count', (SELECT count(*) FROM deleted_items),
  'deleted_score_count', (SELECT count(*) FROM deleted_scores),
  'deleted_member_count', (SELECT count(*) FROM deleted_members),
  'remaining_queue_count', (
    SELECT count(*)
    FROM target_queues
    WHERE id NOT IN (SELECT id FROM deleted_queues)
  ),
  'remaining_item_count', (
    SELECT count(*)
    FROM target_items
    WHERE id NOT IN (SELECT id FROM deleted_items)
  ),
  'remaining_score_count', (
    SELECT count(*)
    FROM model_hub_score
    WHERE (
        queue_item_id IN (SELECT id FROM target_items)
        OR label_id IN (SELECT id FROM target_labels)
      )
      AND id NOT IN (SELECT id FROM deleted_scores)
  )
)::text;
`;
  const result = await runPostgresJson(sql);
  if (
    Number(result.deleted_queue_count) > 0 ||
    Number(result.deleted_label_count) > 0 ||
    Number(result.remaining_queue_count) > 0 ||
    Number(result.remaining_score_count) > 0
  ) {
    evidence.push({
      cleanup: "hard delete annotation workspace mode fixtures",
      status:
        Number(result.remaining_queue_count) === 0 &&
        Number(result.remaining_item_count) === 0 &&
        Number(result.remaining_score_count) === 0
          ? "passed"
          : "failed",
      audit: result,
    });
  }
  return result;
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

async function collectModeState(page) {
  return page.evaluate(() => {
    const hasVisibleText = (text) =>
      window
        .visibleElements()
        .some((element) =>
          window.normalizeText(element.textContent).includes(text),
        );
    return {
      url: window.location.href,
      annotatePressed: Boolean(
        Array.from(document.querySelectorAll("button")).find(
          (button) =>
            window.normalizeText(button.textContent) ===
              "Annotate my answers" &&
            button.getAttribute("aria-pressed") === "true",
        ),
      ),
      reviewPressed: Boolean(
        Array.from(document.querySelectorAll("button")).find(
          (button) =>
            window.normalizeText(button.textContent) === "Review submissions" &&
            button.getAttribute("aria-pressed") === "true",
        ),
      ),
      submitButtonVisible: hasVisibleText("Submit for Review"),
      reviewAnnotationsVisible: hasVisibleText("Review Annotations"),
      reviewActionBarVisible: Boolean(
        document.querySelector('[data-testid="review-action-bar"]'),
      ),
    };
  });
}

async function waitForResponseDuring(page, label, predicate, action) {
  try {
    const [response] = await Promise.all([
      page.waitForResponse(predicate, { timeout: 60_000 }),
      action(),
    ]);
    return response;
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
}

async function waitForPathIncludes(page, pathName, timeout = 30_000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname.includes(expectedPath),
    { timeout },
    pathName,
  );
}

async function waitForLocationSearchParam(
  page,
  param,
  value,
  timeout = 30_000,
) {
  await page.waitForFunction(
    ({ key, expected }) =>
      new URLSearchParams(window.location.search).get(key) === expected,
    { timeout },
    { key: param, expected: value },
  );
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30_000 } = {},
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

async function assertTogglePressed(page, text) {
  const pressed = await page.evaluate((expectedText) => {
    const button = window
      .visibleElements("button")
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedText,
      );
    return button?.getAttribute("aria-pressed") === "true";
  }, text);
  assert(pressed, `${text} toggle is not pressed.`);
}

async function waitForRequestSeen(requests, predicate, timeout = 30_000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (requests.some(predicate)) return;
    await sleep(100);
  }
  throw new Error(
    `Timed out waiting for request. Seen requests: ${requests.join(", ")}`,
  );
}

function isNextItemResponse(response, queueId, expectedParams = {}) {
  if (response.request().method() !== "GET" || response.status() >= 400) {
    return false;
  }
  const url = new URL(response.url());
  if (
    !isAnnotationQueueApiUrl(response.url()) ||
    url.pathname !== `/model-hub/annotation-queues/${queueId}/items/next-item/`
  ) {
    return false;
  }
  return Object.entries(expectedParams).every(
    ([key, value]) => url.searchParams.get(key) === String(value),
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
  const parts = rawRequest.split(" ");
  if (parts.length < 2) return rawRequest;
  const [method, rawUrl] = parts;
  try {
    const url = new URL(rawUrl);
    return `${method} ${url.pathname}${url.search}`;
  } catch {
    return rawRequest;
  }
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
  assert(text, "Postgres mode-switch audit returned no JSON output.");
  return JSON.parse(text);
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlNullableUuid(value) {
  if (value === null || value === undefined || value === "") return "NULL";
  return sqlUuid(value);
}

function sqlText(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function sqlJson(value) {
  return `${sqlText(JSON.stringify(value ?? null))}::jsonb`;
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
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
