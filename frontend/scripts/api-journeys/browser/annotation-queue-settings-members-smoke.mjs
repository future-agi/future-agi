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
const QUEUE_PREFIX = "ui_aq_settings_";
const SCREENSHOT_PATH =
  process.env.ANNOTATION_QUEUE_SETTINGS_SCREENSHOT ||
  "/tmp/annotation-queue-settings-members-smoke.png";
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
  const browserMutations = [];
  const queueRequests = [];
  let browser = null;
  let page = null;
  let fixture = null;
  let caughtError = null;

  try {
    await hardDeleteSettingsFixturesByPrefix({
      organizationId: auth.organizationId,
      evidence: cleanupEvidence,
    });

    fixture = await seedSettingsFixture({
      runId: auth.runId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      userId,
    });

    const detailBefore = await auth.client.get(
      apiPath("/model-hub/annotation-queues/{id}/", { id: fixture.queueId }),
    );
    assertQueueDetail(detailBefore, fixture, { afterSave: false });

    const membersBefore = await auth.client.get(
      apiPath("/model-hub/organizations/{organization_id}/users/", {
        organization_id: auth.organizationId,
      }),
      { query: { search: fixture.altEmail, limit: 30 }, unwrap: false },
    );
    assert(
      asArray(membersBefore).some((member) => member.id === fixture.altUserId),
      `Temporary alternate member is not visible to the member picker API: ${JSON.stringify(
        membersBefore,
      )}`,
    );

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
      if (!isJourneyApiUrl(request.url())) return;
      const masked = maskRequest(`${request.method()} ${request.url()}`);
      queueRequests.push(masked);
      if (MUTATION_METHODS.has(request.method())) {
        browserMutations.push(masked);
      }
    });
    page.on("response", (response) => {
      if (isJourneyApiUrl(response.url()) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${response.url()}`);
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
    await waitForVisibleText(page, fixture.queueName, { exact: true });
    await clickButtonByText(page, "Settings");
    await waitForVisibleText(page, "General", { exact: true });
    await waitForVisibleText(page, "Labels", { exact: true });
    await waitForVisibleText(page, "Members", { exact: true });
    await waitForVisibleText(page, "Workflow", { exact: true });
    await waitForVisibleText(page, "Danger zone", { exact: true });
    await waitForVisibleText(page, fixture.labelName, { exact: false });
    await waitForVisibleText(page, fixture.altEmail, { exact: false });

    const beforeVisualState = await collectSettingsVisualState(page, {
      ...fixture,
      currentUserId: userId,
    });
    assertSettingsVisualState(beforeVisualState, fixture);

    await setFieldValue(
      page,
      'textarea[name="instructions"]',
      fixture.updatedInstructions,
    );
    await clickCheckboxByLabel(
      page,
      "Auto-assign items to all annotator members",
    );

    const patchResult = await waitForResponseDuring(
      page,
      "annotation queue settings PATCH",
      (response) => isQueuePatchResponse(response, fixture.queueId),
      () => clickButtonByText(page, "Save Changes"),
    );
    const patchJson = await responseJson(patchResult);
    assertQueueDetail(patchJson?.result || patchJson, fixture, {
      afterSave: true,
    });
    await waitForVisibleText(page, "Queue updated successfully", {
      exact: false,
    });

    const dbAudit = await loadSettingsFixtureAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      queueId: fixture.queueId,
      labelId: fixture.labelId,
      currentUserId: userId,
      altUserId: fixture.altUserId,
    });
    assertSettingsDbAudit(dbAudit, fixture);

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      browserMutations.length === 1 &&
        browserMutations[0] ===
          `PATCH /model-hub/annotation-queues/${fixture.queueId}/`,
      `Unexpected browser mutations: ${browserMutations.join(", ")}`,
    );
    assert(
      apiFailures.length === 0,
      `Unexpected API failures: ${apiFailures.map(maskRequest).join(", ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    const cleanup = await hardDeleteSettingsFixturesByPrefix({
      organizationId: auth.organizationId,
      evidence: cleanupEvidence,
    });
    assert(
      Number(cleanup.remaining_queue_count) === 0 &&
        Number(cleanup.remaining_label_count) === 0 &&
        Number(cleanup.remaining_member_count) === 0 &&
        Number(cleanup.remaining_temp_workspace_membership_count) === 0 &&
        Number(cleanup.remaining_temp_org_membership_count) === 0,
      `Annotation queue settings cleanup left residue: ${JSON.stringify(
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
          queue_id: fixture.queueId,
          queue_name: fixture.queueName,
          label_id: fixture.labelId,
          label_name: fixture.labelName,
          queue_label_id: fixture.queueLabelId,
          alternate_member: {
            id: fixture.altUserId,
            email: fixture.altEmail,
            temp_org_membership_id: fixture.tempOrgMembershipId,
            temp_workspace_membership_id: fixture.tempWorkspaceMembershipId,
          },
          browser_request_count: queueRequests.length,
          browser_mutations: browserMutations,
          visual_state: beforeVisualState,
          required_label_state: {
            api_required_preserved: true,
            db_required_label_binding_count:
              dbAudit.required_label_binding_count,
            settings_ui_required_marker_visible:
              beforeVisualState.requiredLabelVisual.requiredTextVisible,
            settings_ui_required_control_visible:
              beforeVisualState.requiredLabelVisual.requiredControlVisible,
          },
          patch: summarizePatch(patchJson?.result || patchJson),
          db_audit: dbAudit,
          screenshot: SCREENSHOT_PATH,
          cleanup: cleanupEvidence,
        },
        null,
        2,
      ),
    );
    fixture = null;
  } catch (error) {
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
          screenshot: FAILURE_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    if (browser) await browser.close().catch(() => null);
    if (fixture || caughtError) {
      await hardDeleteSettingsFixturesByPrefix({
        organizationId: auth.organizationId,
        evidence: cleanupEvidence,
      }).catch((cleanupError) => {
        console.error(
          JSON.stringify({
            status: "cleanup_failed",
            error: cleanupError.message,
          }),
        );
      });
    }
  }
}

async function seedSettingsFixture({
  runId,
  organizationId,
  workspaceId,
  userId,
}) {
  const suffix = runId.replace(/[^a-z0-9]/gi, "").slice(-16);
  const queueId = randomUUID();
  const labelId = randomUUID();
  const queueLabelId = randomUUID();
  const creatorMemberId = randomUUID();
  const altQueueMemberId = randomUUID();
  const tempOrgMembershipId = randomUUID();
  const tempWorkspaceMembershipId = randomUUID();
  const queueName = `${QUEUE_PREFIX}${suffix}`;
  const labelName = `${QUEUE_PREFIX}label_${suffix}`;
  const initialInstructions =
    "Initial settings smoke instructions for annotators.";
  const updatedInstructions =
    "Updated settings smoke instructions from browser save.";

  const sql = `
WITH candidate_user AS (
  SELECT u.id, u.email, u.name
  FROM accounts_user u
  WHERE u.is_active = true
    AND u.id <> ${sqlUuid(userId)}
    AND NOT EXISTS (
      SELECT 1
      FROM accounts_workspacemembership wm
      WHERE wm.workspace_id = ${sqlUuid(workspaceId)}
        AND wm.user_id = u.id
        AND wm.deleted = false
    )
    AND NOT EXISTS (
      SELECT 1
      FROM accounts_organization_membership om
      WHERE om.organization_id = ${sqlUuid(organizationId)}
        AND om.user_id = u.id
        AND om.deleted = false
    )
  ORDER BY u.created_at ASC
  LIMIT 1
),
inserted_org_membership AS (
  INSERT INTO accounts_organization_membership (
    created_at, updated_at, deleted, deleted_at, id, role, joined_at,
    is_active, invited_by_id, organization_id, user_id, level
  )
  SELECT
    now(), now(), false, NULL,
    ${sqlUuid(tempOrgMembershipId)},
    'Member',
    now(),
    true,
    ${sqlUuid(userId)},
    ${sqlUuid(organizationId)},
    candidate_user.id,
    3
  FROM candidate_user
  RETURNING id, user_id
),
inserted_workspace_membership AS (
  INSERT INTO accounts_workspacemembership (
    created_at, updated_at, deleted, deleted_at, id, role, is_active,
    invited_by_id, user_id, workspace_id, granted_at, granted_by_id, level,
    organization_membership_id
  )
  SELECT
    now(), now(), false, NULL,
    ${sqlUuid(tempWorkspaceMembershipId)},
    'workspace_member',
    true,
    ${sqlUuid(userId)},
    candidate_user.id,
    ${sqlUuid(workspaceId)},
    now(),
    ${sqlUuid(userId)},
    3,
    inserted_org_membership.id
  FROM candidate_user
  JOIN inserted_org_membership ON inserted_org_membership.user_id = candidate_user.id
  RETURNING id, user_id
),
inserted_label AS (
  INSERT INTO model_hub_annotationslabels (
    created_at, updated_at, deleted, deleted_at, id, name, type, settings,
    organization_id, description, project_id, workspace_id, metadata, allow_notes
  )
  VALUES (
    now(), now(), false, NULL,
    ${sqlUuid(labelId)},
    ${sqlText(labelName)},
    'text',
    ${sqlJson({
      placeholder: "Write queue settings feedback",
      min_length: 0,
      max_length: 500,
    })},
    ${sqlUuid(organizationId)},
    ${sqlText("Disposable label for annotation queue settings smoke.")},
    NULL,
    ${sqlUuid(workspaceId)},
    '{}'::jsonb,
    true
  )
  RETURNING id
),
inserted_queue AS (
  INSERT INTO model_hub_annotationqueue (
    created_at, updated_at, deleted, deleted_at, id, name, description,
    instructions, status, assignment_strategy, annotations_required,
    reservation_timeout_minutes, requires_review, auto_assign,
    organization_id, workspace_id, project_id, dataset_id,
    agent_definition_id, is_default, created_by_id
  )
  VALUES (
    now(), now(), false, NULL,
    ${sqlUuid(queueId)},
    ${sqlText(queueName)},
    ${sqlText("Disposable queue for settings and member-role browser coverage.")},
    ${sqlText(initialInstructions)},
    'active',
    'manual',
    1,
    60,
    false,
    false,
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)},
    NULL,
    NULL,
    NULL,
    false,
    ${sqlUuid(userId)}
  )
  RETURNING id
),
inserted_queue_label AS (
  INSERT INTO model_hub_annotationqueuelabel (
    created_at, updated_at, deleted, deleted_at, id, required, "order",
    label_id, queue_id
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
inserted_creator_member AS (
  INSERT INTO model_hub_annotationqueueannotator (
    created_at, updated_at, deleted, deleted_at, id, role, roles, queue_id,
    user_id
  )
  SELECT
    now(), now(), false, NULL,
    ${sqlUuid(creatorMemberId)},
    'manager',
    ${sqlJson(["manager", "reviewer", "annotator"])},
    inserted_queue.id,
    ${sqlUuid(userId)}
  FROM inserted_queue
  RETURNING id
),
inserted_alt_member AS (
  INSERT INTO model_hub_annotationqueueannotator (
    created_at, updated_at, deleted, deleted_at, id, role, roles, queue_id,
    user_id
  )
  SELECT
    now(), now(), false, NULL,
    ${sqlUuid(altQueueMemberId)},
    'reviewer',
    ${sqlJson(["annotator", "reviewer"])},
    inserted_queue.id,
    candidate_user.id
  FROM inserted_queue, candidate_user
  RETURNING id, user_id
)
SELECT json_build_object(
  'queue_id', ${sqlText(queueId)},
  'queue_name', ${sqlText(queueName)},
  'label_id', ${sqlText(labelId)},
  'label_name', ${sqlText(labelName)},
  'initial_instructions', ${sqlText(initialInstructions)},
  'updated_instructions', ${sqlText(updatedInstructions)},
  'alt_user_id', (SELECT user_id::text FROM inserted_alt_member),
  'alt_email', (SELECT email FROM candidate_user),
  'temp_org_membership_id', (SELECT id::text FROM inserted_org_membership),
  'temp_workspace_membership_id', (SELECT id::text FROM inserted_workspace_membership),
  'queue_count', (SELECT count(*) FROM inserted_queue),
  'label_count', (SELECT count(*) FROM inserted_label),
  'queue_label_count', (SELECT count(*) FROM inserted_queue_label),
  'queue_member_count',
    (SELECT count(*) FROM inserted_creator_member) + (SELECT count(*) FROM inserted_alt_member)
)::text;
`;
  const result = await runPostgresJson(sql);
  assert(result.queue_id === queueId, "Settings queue fixture insert failed.");
  assert(
    Number(result.queue_count) === 1 &&
      Number(result.label_count) === 1 &&
      Number(result.queue_label_count) === 1 &&
      Number(result.queue_member_count) === 2,
    `Settings fixture counts are wrong: ${JSON.stringify(result)}`,
  );
  assert(
    isUuid(result.alt_user_id) &&
      isUuid(result.temp_org_membership_id) &&
      isUuid(result.temp_workspace_membership_id),
    `Could not seed a temporary alternate workspace member: ${JSON.stringify(
      result,
    )}`,
  );
  return {
    queueId,
    queueName,
    labelId,
    queueLabelId,
    labelName,
    initialInstructions,
    updatedInstructions,
    altUserId: result.alt_user_id,
    altEmail: result.alt_email,
    tempOrgMembershipId: result.temp_org_membership_id,
    tempWorkspaceMembershipId: result.temp_workspace_membership_id,
  };
}

function assertQueueDetail(queue, fixture, { afterSave }) {
  assert(queue?.id === fixture.queueId, "Queue detail id mismatch.");
  assert(queue?.name === fixture.queueName, "Queue detail name mismatch.");
  const labels = asArray(queue?.labels);
  const annotators = asArray(queue?.annotators);
  const seededLabel = labels.find(
    (label) => String(label.label_id || label.id) === fixture.labelId,
  );
  assert(
    seededLabel,
    `Queue detail is missing seeded label: ${JSON.stringify(queue?.labels)}`,
  );
  assert(
    seededLabel.required === true,
    `Queue detail did not preserve required=true on seeded label: ${JSON.stringify(
      seededLabel,
    )}`,
  );
  const alt = annotators.find(
    (annotator) => String(annotator.user_id) === fixture.altUserId,
  );
  assert(
    alt &&
      asArray(alt.roles).includes("annotator") &&
      asArray(alt.roles).includes("reviewer"),
    `Queue detail is missing alternate annotator/reviewer roles: ${JSON.stringify(
      queue?.annotators,
    )}`,
  );
  if (afterSave) {
    assert(
      queue.instructions === fixture.updatedInstructions,
      `Queue PATCH did not persist instructions: ${JSON.stringify(queue)}`,
    );
    assert(
      queue.auto_assign === true,
      `Queue PATCH did not enable auto_assign: ${JSON.stringify(queue)}`,
    );
  }
}

async function collectSettingsVisualState(page, fixture) {
  return page.evaluate(
    ({ currentUserId, altUserId, labelName }) => {
      const hasVisibleText = (text) =>
        window
          .visibleElements()
          .some((element) =>
            window.normalizeText(element.textContent).includes(text),
          );
      const roleState = (userId) => {
        const row = document.querySelector(
          `[data-testid="annotator-row-${userId}"]`,
        );
        const state = {};
        for (const label of Array.from(row?.querySelectorAll("label") || [])) {
          const text = window.normalizeText(label.textContent);
          const input = label.querySelector('input[type="checkbox"]');
          if (!["Annotator", "Reviewer", "Manager"].includes(text) || !input) {
            continue;
          }
          state[text.toLowerCase()] = {
            checked: Boolean(input.checked),
            disabled: Boolean(input.disabled),
          };
        }
        return {
          present: Boolean(row),
          text: row ? window.normalizeText(row.textContent) : "",
          roles: state,
        };
      };
      const labelContexts = window
        .visibleElements()
        .filter((element) => {
          const text = window.stripZeroWidth(
            window.normalizeText(element.textContent),
          );
          return (
            text === labelName ||
            (text.includes(labelName) && text.length <= labelName.length + 60)
          );
        })
        .map((element) => {
          const context =
            element.closest(".MuiChip-root") ||
            element.closest("[role='option']") ||
            element.closest("label") ||
            element.parentElement?.parentElement ||
            element;
          const controls = Array.from(
            context.querySelectorAll("button,label,[role='checkbox'],input"),
          ).map((control) =>
            window.normalizeText(
              control.textContent ||
                control.getAttribute("aria-label") ||
                control.getAttribute("name") ||
                "",
            ),
          );
          return {
            text: window.normalizeText(context.textContent),
            controls,
          };
        });
      const requiredLabelVisual = {
        contexts: labelContexts.slice(0, 6),
        requiredTextVisible: labelContexts.some((context) =>
          /\brequired\b/i.test(context.text),
        ),
        requiredControlVisible: labelContexts.some((context) =>
          context.controls.some((text) => /\brequired\b/i.test(text)),
        ),
      };
      return {
        url: window.location.href,
        hasGeneral: hasVisibleText("General"),
        hasLabels: hasVisibleText("Labels"),
        hasMembers: hasVisibleText("Members"),
        hasWorkflow: hasVisibleText("Workflow"),
        hasDangerZone: hasVisibleText("Danger zone"),
        hasLabelName: hasVisibleText(labelName),
        requiredLabelVisual,
        creator: roleState(currentUserId),
        alternate: roleState(altUserId),
      };
    },
    {
      currentUserId: fixture.currentUserId,
      altUserId: fixture.altUserId,
      labelName: fixture.labelName,
    },
  );
}

function assertSettingsVisualState(state, fixture) {
  assert(
    state.hasGeneral &&
      state.hasLabels &&
      state.hasMembers &&
      state.hasWorkflow &&
      state.hasDangerZone &&
      state.hasLabelName,
    `Settings tab sections are missing: ${JSON.stringify(state)}`,
  );
  assert(
    state.creator.present &&
      state.creator.roles.manager?.checked &&
      state.creator.roles.manager?.disabled &&
      state.creator.roles.reviewer?.checked &&
      state.creator.roles.annotator?.checked,
    `Creator role checkboxes are wrong: ${JSON.stringify(state.creator)}`,
  );
  assert(
    state.alternate.present &&
      state.alternate.text.includes(fixture.altEmail) &&
      state.alternate.roles.annotator?.checked &&
      state.alternate.roles.reviewer?.checked &&
      !state.alternate.roles.manager?.checked,
    `Alternate role checkboxes are wrong: ${JSON.stringify(state.alternate)}`,
  );
  assert(
    state.requiredLabelVisual?.requiredTextVisible === false &&
      state.requiredLabelVisual?.requiredControlVisible === false,
    `Settings UI unexpectedly exposes a required-label marker or control: ${JSON.stringify(
      state.requiredLabelVisual,
    )}`,
  );
}

async function loadSettingsFixtureAudit({
  organizationId,
  workspaceId,
  queueId,
  labelId,
  currentUserId,
  altUserId,
}) {
  const sql = `
WITH target_queue AS (
  SELECT id, name, instructions, auto_assign, requires_review,
         annotations_required, reservation_timeout_minutes, status
  FROM model_hub_annotationqueue
  WHERE id = ${sqlUuid(queueId)}
    AND organization_id = ${sqlUuid(organizationId)}
    AND workspace_id = ${sqlUuid(workspaceId)}
    AND deleted = false
),
members AS (
  SELECT user_id, role, roles
  FROM model_hub_annotationqueueannotator
  WHERE queue_id IN (SELECT id FROM target_queue)
    AND deleted = false
)
SELECT json_build_object(
  'queue_id', (SELECT id::text FROM target_queue),
  'queue_name', (SELECT name FROM target_queue),
  'instructions', (SELECT instructions FROM target_queue),
  'auto_assign', (SELECT auto_assign FROM target_queue),
  'requires_review', (SELECT requires_review FROM target_queue),
  'annotations_required', (SELECT annotations_required FROM target_queue),
  'reservation_timeout_minutes', (SELECT reservation_timeout_minutes FROM target_queue),
  'status', (SELECT status FROM target_queue),
  'label_binding_count', (
    SELECT count(*)
    FROM model_hub_annotationqueuelabel
    WHERE queue_id IN (SELECT id FROM target_queue)
      AND label_id = ${sqlUuid(labelId)}
      AND deleted = false
  ),
  'required_label_binding_count', (
    SELECT count(*)
    FROM model_hub_annotationqueuelabel
    WHERE queue_id IN (SELECT id FROM target_queue)
      AND label_id = ${sqlUuid(labelId)}
      AND deleted = false
      AND required = true
  ),
  'optional_label_binding_count', (
    SELECT count(*)
    FROM model_hub_annotationqueuelabel
    WHERE queue_id IN (SELECT id FROM target_queue)
      AND label_id = ${sqlUuid(labelId)}
      AND deleted = false
      AND required = false
  ),
  'member_count', (SELECT count(*) FROM members),
  'current_roles', (
    SELECT roles
    FROM members
    WHERE user_id = ${sqlUuid(currentUserId)}
    LIMIT 1
  ),
  'alternate_roles', (
    SELECT roles
    FROM members
    WHERE user_id = ${sqlUuid(altUserId)}
    LIMIT 1
  ),
  'temp_workspace_member_count', (
    SELECT count(*)
    FROM accounts_workspacemembership
    WHERE workspace_id = ${sqlUuid(workspaceId)}
      AND user_id = ${sqlUuid(altUserId)}
      AND deleted = false
      AND is_active = true
  ),
  'temp_org_member_count', (
    SELECT count(*)
    FROM accounts_organization_membership
    WHERE organization_id = ${sqlUuid(organizationId)}
      AND user_id = ${sqlUuid(altUserId)}
      AND deleted = false
      AND is_active = true
  )
)::text;
`;
  return runPostgresJson(sql);
}

function assertSettingsDbAudit(audit, fixture) {
  assert(
    audit.queue_id === fixture.queueId,
    "Settings DB audit queue missing.",
  );
  assert(
    audit.instructions === fixture.updatedInstructions &&
      audit.auto_assign === true &&
      audit.requires_review === false &&
      Number(audit.annotations_required) === 1 &&
      Number(audit.reservation_timeout_minutes) === 60 &&
      audit.status === "active",
    `Settings DB audit fields mismatch: ${JSON.stringify(audit)}`,
  );
  assert(
    Number(audit.label_binding_count) === 1 &&
      Number(audit.required_label_binding_count) === 1 &&
      Number(audit.optional_label_binding_count) === 0 &&
      Number(audit.member_count) === 2 &&
      Number(audit.temp_workspace_member_count) === 1 &&
      Number(audit.temp_org_member_count) === 1,
    `Settings DB audit relation counts mismatch: ${JSON.stringify(audit)}`,
  );
  const currentRoles = asArray(audit.current_roles);
  const alternateRoles = asArray(audit.alternate_roles);
  assert(
    currentRoles.includes("manager") &&
      currentRoles.includes("reviewer") &&
      currentRoles.includes("annotator") &&
      alternateRoles.includes("annotator") &&
      alternateRoles.includes("reviewer") &&
      !alternateRoles.includes("manager"),
    `Settings DB role audit mismatch: ${JSON.stringify(audit)}`,
  );
}

async function hardDeleteSettingsFixturesByPrefix({
  organizationId,
  evidence = [],
}) {
  const sql = `
WITH target_queues AS (
  SELECT id, workspace_id
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
target_queue_members AS (
  SELECT user_id
  FROM model_hub_annotationqueueannotator
  WHERE queue_id IN (SELECT id FROM target_queues)
),
target_workspace_memberships AS (
  SELECT wm.id, wm.organization_membership_id
  FROM accounts_workspacemembership wm
  WHERE wm.workspace_id IN (SELECT workspace_id FROM target_queues)
    AND wm.user_id IN (SELECT user_id FROM target_queue_members)
    AND wm.granted_by_id IS NOT NULL
    AND wm.invited_by_id IS NOT NULL
    AND wm.organization_membership_id IN (
      SELECT id
      FROM accounts_organization_membership
      WHERE organization_id = ${sqlUuid(organizationId)}
        AND user_id IN (SELECT user_id FROM target_queue_members)
        AND invited_by_id IS NOT NULL
    )
),
target_org_memberships AS (
  SELECT id
  FROM accounts_organization_membership
  WHERE id IN (
    SELECT organization_membership_id
    FROM target_workspace_memberships
    WHERE organization_membership_id IS NOT NULL
  )
),
deleted_members AS (
  DELETE FROM model_hub_annotationqueueannotator
  WHERE queue_id IN (SELECT id FROM target_queues)
  RETURNING id, user_id
),
deleted_queue_labels AS (
  DELETE FROM model_hub_annotationqueuelabel
  WHERE queue_id IN (SELECT id FROM target_queues)
     OR label_id IN (SELECT id FROM target_labels)
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
),
deleted_workspace_memberships AS (
  DELETE FROM accounts_workspacemembership
  WHERE id IN (SELECT id FROM target_workspace_memberships)
  RETURNING id, organization_membership_id
),
deleted_org_memberships AS (
  DELETE FROM accounts_organization_membership
  WHERE id IN (SELECT id FROM target_org_memberships)
  RETURNING id
)
SELECT json_build_object(
  'deleted_queue_count', (SELECT count(*) FROM deleted_queues),
  'deleted_label_count', (SELECT count(*) FROM deleted_labels),
  'deleted_member_count', (SELECT count(*) FROM deleted_members),
  'deleted_queue_label_count', (SELECT count(*) FROM deleted_queue_labels),
  'deleted_temp_workspace_membership_count', (SELECT count(*) FROM deleted_workspace_memberships),
  'deleted_temp_org_membership_count', (SELECT count(*) FROM deleted_org_memberships),
  'remaining_queue_count', (
    SELECT count(*)
    FROM target_queues
    WHERE id NOT IN (SELECT id FROM deleted_queues)
  ),
  'remaining_label_count', (
    SELECT count(*)
    FROM target_labels
    WHERE id NOT IN (SELECT id FROM deleted_labels)
  ),
  'remaining_member_count', (
    SELECT count(*)
    FROM model_hub_annotationqueueannotator
    WHERE queue_id IN (SELECT id FROM target_queues)
      AND id NOT IN (SELECT id FROM deleted_members)
  ),
  'remaining_temp_workspace_membership_count', (
    SELECT count(*)
    FROM target_workspace_memberships
    WHERE id NOT IN (SELECT id FROM deleted_workspace_memberships)
  ),
  'remaining_temp_org_membership_count', (
    SELECT count(*)
    FROM target_org_memberships
    WHERE id NOT IN (SELECT id FROM deleted_org_memberships)
  )
)::text;
`;
  const result = await runPostgresJson(sql);
  if (
    Number(result.deleted_queue_count) > 0 ||
    Number(result.deleted_label_count) > 0 ||
    Number(result.deleted_temp_workspace_membership_count) > 0 ||
    Number(result.remaining_queue_count) > 0
  ) {
    evidence.push({
      cleanup: "hard delete annotation queue settings fixtures",
      status:
        Number(result.remaining_queue_count) === 0 &&
        Number(result.remaining_label_count) === 0 &&
        Number(result.remaining_member_count) === 0 &&
        Number(result.remaining_temp_workspace_membership_count) === 0 &&
        Number(result.remaining_temp_org_membership_count) === 0
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
      window.stripZeroWidth = (value) =>
        String(value || "").replace(/[\u200B-\u200D\uFEFF]/g, "");
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
      page.waitForResponse(predicate, { timeout: 60_000 }),
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

async function clickButtonByText(page, text) {
  await waitForVisibleText(page, text, { exact: true });
  const clicked = await page.evaluate((expectedText) => {
    const button = window
      .visibleElements("button, [role='tab']")
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedText &&
          !candidate.disabled &&
          candidate.getAttribute("aria-disabled") !== "true",
      );
    if (!button) return false;
    button.click();
    return true;
  }, text);
  assert(clicked, `Could not click button/tab: ${text}`);
}

async function setFieldValue(page, selector, value) {
  await page.waitForSelector(selector, { timeout: 30_000 });
  await page.evaluate(
    ({ selector: targetSelector, value: nextValue }) => {
      const input = document.querySelector(targetSelector);
      if (!input) throw new Error(`Missing field for ${targetSelector}`);
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

async function clickCheckboxByLabel(page, labelText) {
  const clicked = await page.evaluate((expectedText) => {
    const label = window
      .visibleElements("label")
      .find((candidate) =>
        window.normalizeText(candidate.textContent).includes(expectedText),
      );
    const input = label?.querySelector('input[type="checkbox"]');
    if (!input || input.disabled) return false;
    input.click();
    return true;
  }, labelText);
  assert(clicked, `Could not click checkbox label: ${labelText}`);
}

function isQueueDetailResponse(response, queueId) {
  if (response.request().method() !== "GET" || response.status() >= 400) {
    return false;
  }
  const url = new URL(response.url());
  return (
    isAnnotationQueueApiUrl(response.url()) &&
    url.pathname === `/model-hub/annotation-queues/${queueId}/`
  );
}

function isQueuePatchResponse(response, queueId) {
  if (response.request().method() !== "PATCH" || response.status() >= 400) {
    return false;
  }
  const url = new URL(response.url());
  return (
    isAnnotationQueueApiUrl(response.url()) &&
    url.pathname === `/model-hub/annotation-queues/${queueId}/`
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

function isJourneyApiUrl(rawUrl) {
  const url = new URL(rawUrl);
  const apiOrigin = new URL(process.env.API_BASE || "http://localhost:8003")
    .origin;
  if (url.origin !== apiOrigin) return false;
  if (url.pathname.startsWith("/model-hub/annotation-queues/")) return true;
  if (url.pathname.startsWith("/model-hub/organizations/")) return true;
  if (url.pathname.startsWith("/model-hub/annotations-labels/")) return true;
  return false;
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

function summarizePatch(queue) {
  return {
    id: queue?.id,
    instructions: queue?.instructions,
    auto_assign: queue?.auto_assign,
    annotator_count: asArray(queue?.annotators).length,
    label_count: asArray(queue?.labels).length,
    labels: asArray(queue?.labels).map((label) => ({
      id: label?.id,
      label_id: label?.label_id,
      name: label?.name,
      required: label?.required,
    })),
  };
}

function maskRequest(rawRequest) {
  const parts = String(rawRequest || "").split(" ");
  if (parts.length < 2) return rawRequest;
  const [method, rawUrl] = parts;
  try {
    const url = new URL(rawUrl);
    return `${method} ${url.pathname}${url.search}`;
  } catch {
    return rawRequest;
  }
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
  assert(text, "Postgres annotation settings query returned no JSON output.");
  return JSON.parse(text);
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlText(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function sqlJson(value) {
  return `${sqlText(JSON.stringify(value ?? null))}::jsonb`;
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
