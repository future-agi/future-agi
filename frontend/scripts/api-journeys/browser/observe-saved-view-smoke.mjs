/* eslint-disable no-console */
import { execFile as execFileCallback } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFile = promisify(execFileCallback);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/observe-saved-view-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/observe-saved-view-smoke-failure.png";
const PROJECT_PREFIX = "ui_observe_saved_view_";

async function main() {
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const projectName = `${PROJECT_PREFIX}${suffix}`;
  const viewName = `TH-4812 saved view ${suffix}`;
  const renamedViewName = `TH-4812 saved view renamed ${suffix}`;
  const duplicatedViewName = `${renamedViewName} (Copy)`;

  await hardDeleteObserveSavedViewFixturesByPrefix({
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    prefix: PROJECT_PREFIX,
  });

  const fixture = await createObserveProject(auth.client, projectName);
  let cleanupDone = false;
  let browser = null;
  let page = null;
  const savedViewIds = [];
  const apiFailures = [];
  const pageErrors = [];
  const savedViewRequests = [];
  const evidence = {
    project_id: fixture.project_id,
    project_name: fixture.project_name,
    view_name: viewName,
    renamed_view_name: renamedViewName,
    duplicated_view_name: duplicatedViewName,
  };

  try {
    evidence.seed_audit = await loadFixtureDbAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      projectId: fixture.project_id,
    });
    assertSeededFixture(evidence.seed_audit, {
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      projectId: fixture.project_id,
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
    await installAuthenticatedState(page, auth);
    monitorPage(page, { apiFailures, pageErrors, savedViewRequests });

    await page.goto(
      `${APP_BASE}/dashboard/observe/${fixture.project_id}/llm-tracing`,
      { waitUntil: "domcontentloaded" },
    );
    await waitForPath(
      page,
      `/dashboard/observe/${fixture.project_id}/llm-tracing`,
    );
    await waitForVisibleText(page, fixture.project_name);
    await waitForVisibleText(page, "Filter", { exact: true });
    await waitForCreateViewButton(page);

    const createResponse = await waitForResponseDuring(
      page,
      "Observe saved-view create",
      savedViewCreateResponse(),
      () => createSavedViewFromToolbar(page, viewName),
    );
    const createBody = await readResponseJson(createResponse);
    const createdView = createBody?.result || createBody;
    assertCreatedSavedView(createdView, {
      projectId: fixture.project_id,
      name: viewName,
    });
    savedViewIds.push(createdView.id);
    evidence.created_saved_view_id = createdView.id;
    evidence.created_config_keys = Object.keys(createdView.config || {}).sort();

    await waitForTabUrl(page, createdView.id);
    await waitForSavedViewTab(page, viewName);

    await openViewContextMenu(page, viewName);
    await clickMenuItem(page, "Rename");
    await waitForRenameInput(page, viewName);
    await setRenameInputValue(page, viewName, renamedViewName);
    const renameResponse = await waitForResponseDuring(
      page,
      "Observe saved-view rename",
      savedViewUpdateResponse(createdView.id, "PUT"),
      () => page.keyboard.press("Enter"),
    );
    const renameBody = await readResponseJson(renameResponse);
    const renamedView = renameBody?.result || renameBody;
    assert(
      renamedView?.name === renamedViewName,
      `Saved-view rename returned wrong name: ${JSON.stringify(renameBody)}`,
    );
    await waitForVisibleText(page, renamedViewName, { exact: true });
    await waitForNoVisibleText(page, viewName, { exact: true });

    await openViewContextMenu(page, renamedViewName);
    const duplicateResponse = await waitForResponseDuring(
      page,
      "Observe saved-view duplicate",
      savedViewDuplicateResponse(createdView.id),
      () => clickMenuItem(page, "Duplicate"),
    );
    const duplicateBody = await readResponseJson(duplicateResponse);
    const duplicatedView = duplicateBody?.result || duplicateBody;
    assertDuplicatedSavedView(duplicatedView, {
      projectId: fixture.project_id,
      name: duplicatedViewName,
      original: renamedView,
    });
    savedViewIds.push(duplicatedView.id);
    evidence.duplicated_saved_view_id = duplicatedView.id;

    await waitForTabUrl(page, duplicatedView.id);
    await waitForVisibleText(page, duplicatedViewName, { exact: true });

    await openViewContextMenu(page, duplicatedViewName);
    const shareResponse = await waitForResponseDuring(
      page,
      "Observe saved-view share with team",
      savedViewUpdateResponse(duplicatedView.id, "PUT"),
      () => clickMenuItem(page, "Share with team"),
    );
    const shareBody = await readResponseJson(shareResponse);
    const sharedDuplicate = shareBody?.result || shareBody;
    assert(
      sharedDuplicate?.visibility === "project",
      `Saved-view share returned wrong visibility: ${JSON.stringify(shareBody)}`,
    );

    evidence.active_audit = await loadSavedViewDbAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      projectId: fixture.project_id,
      savedViewIds,
    });
    assertActiveSavedViewAudit(evidence.active_audit, {
      projectId: fixture.project_id,
      workspaceId: auth.workspaceId,
      userId: auth.user.id,
      createdId: createdView.id,
      duplicatedId: duplicatedView.id,
      renamedViewName,
      duplicatedViewName,
    });

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    await openViewContextMenu(page, duplicatedViewName);
    await clickMenuItem(page, "Delete");
    await waitForDeleteDialog(page, duplicatedViewName);
    const deleteDuplicateResponse = await waitForResponseDuring(
      page,
      "Observe duplicated saved-view delete",
      savedViewDeleteResponse(duplicatedView.id),
      () => clickDialogButton(page, "Delete"),
    );
    assert(
      deleteDuplicateResponse.status() < 400,
      `Duplicated saved-view delete returned HTTP ${deleteDuplicateResponse.status()}`,
    );
    await waitForNoVisibleText(page, duplicatedViewName);

    await openViewContextMenu(page, renamedViewName);
    await clickMenuItem(page, "Delete");
    await waitForDeleteDialog(page, renamedViewName);
    const deleteOriginalResponse = await waitForResponseDuring(
      page,
      "Observe original saved-view delete",
      savedViewDeleteResponse(createdView.id),
      () => clickDialogButton(page, "Delete"),
    );
    assert(
      deleteOriginalResponse.status() < 400,
      `Original saved-view delete returned HTTP ${deleteOriginalResponse.status()}`,
    );
    await waitForNoVisibleText(page, renamedViewName);
    await waitForVisibleText(page, "Trace", { exact: true });

    evidence.deleted_audit = await loadSavedViewDbAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      projectId: fixture.project_id,
      savedViewIds,
    });
    assertDeletedSavedViewAudit(evidence.deleted_audit, {
      createdId: createdView.id,
      duplicatedId: duplicatedView.id,
    });

    const cleanupAudit = await cleanupObserveSavedViewFixture({
      projectId: fixture.project_id,
      savedViewIds,
    });
    cleanupDone = true;
    evidence.cleanup = cleanupAudit;
    assertCleanupAudit(cleanupAudit);

    assert(
      apiFailures.length === 0,
      `Unexpected Observe API failures: ${apiFailures.join("; ")}`,
    );
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
          saved_view_request_count: savedViewRequests.length,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await page
      ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
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
          saved_view_requests: savedViewRequests,
          failure_screenshot: FAILURE_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    if (browser) await browser.close();
    if (!cleanupDone) {
      await cleanupObserveSavedViewFixture({
        projectId: fixture.project_id,
        savedViewIds,
      }).catch((error) => {
        console.error(`Cleanup failed: ${error.message}`);
      });
    }
  }
}

async function createObserveProject(client, name) {
  const created = await client.post(apiPath("/tracer/project/"), {
    name,
    model_type: "GenerativeLLM",
    trace_type: "observe",
    metadata: { source: "browser-smoke", marker: "saved-view" },
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

function assertCreatedSavedView(view, { projectId, name }) {
  assert(
    isUuid(view?.id),
    `Saved-view create returned no id: ${JSON.stringify(view)}`,
  );
  assert(view?.name === name, "Saved-view create returned wrong name.");
  assert(
    view?.project === projectId,
    "Saved-view create returned wrong project.",
  );
  assert(view?.tab_type === "traces", "Saved-view create used wrong tab_type.");
  assert(
    view?.visibility === "personal",
    "Saved-view create should default to personal visibility.",
  );
  assert(
    view?.config && typeof view.config === "object",
    "Saved-view create omitted persisted config.",
  );
  assert(
    view.config.display && typeof view.config.display === "object",
    "Saved-view create did not persist display config.",
  );
  assert(
    Array.isArray(view.config.filters),
    "Saved-view create did not persist filter config list.",
  );
}

function assertDuplicatedSavedView(view, { projectId, name, original }) {
  assert(
    isUuid(view?.id),
    `Saved-view duplicate returned no id: ${JSON.stringify(view)}`,
  );
  assert(view.id !== original?.id, "Saved-view duplicate reused original id.");
  assert(view?.name === name, "Saved-view duplicate returned wrong name.");
  assert(
    view?.project === projectId,
    "Saved-view duplicate returned wrong project.",
  );
  assert(
    view?.tab_type === original?.tab_type,
    "Saved-view duplicate did not preserve tab_type.",
  );
  assert(
    view?.visibility === "personal",
    "Saved-view duplicate should start personal.",
  );
  assert(
    JSON.stringify(view?.config || {}) ===
      JSON.stringify(original?.config || {}),
    "Saved-view duplicate did not preserve config.",
  );
}

async function loadFixtureDbAudit({ organizationId, workspaceId, projectId }) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(projectId)} AS project_id
)
SELECT COALESCE((
  SELECT jsonb_build_object(
    'project_id', project.id::text,
    'project_name', project.name,
    'trace_type', project.trace_type,
    'organization_id', project.organization_id::text,
    'workspace_id', project.workspace_id::text,
    'deleted', project.deleted
  )
  FROM requested
  JOIN tracer_project project
    ON project.id = requested.project_id
   AND project.organization_id = requested.organization_id
   AND project.workspace_id = requested.workspace_id
), jsonb_build_object('project_id', null))::text;
`;
  return runPostgresJson(sql);
}

function assertSeededFixture(
  audit,
  { organizationId, workspaceId, projectId },
) {
  assert(audit?.project_id === projectId, "Seed audit project id mismatch.");
  assert(
    audit?.organization_id === organizationId,
    "Seed audit organization mismatch.",
  );
  assert(audit?.workspace_id === workspaceId, "Seed audit workspace mismatch.");
  assert(audit?.trace_type === "observe", "Seed audit trace_type mismatch.");
  assert(audit?.deleted === false, "Seed audit project is deleted.");
}

async function loadSavedViewDbAudit({
  organizationId,
  workspaceId,
  projectId,
  savedViewIds,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuidArray(savedViewIds)} AS saved_view_ids
),
view_rows AS (
  SELECT
    saved_view.id::text AS id,
    saved_view.project_id::text AS project_id,
    saved_view.workspace_id::text AS workspace_id,
    saved_view.created_by_id::text AS created_by_id,
    saved_view.updated_by_id::text AS updated_by_id,
    saved_view.name,
    saved_view.tab_type,
    saved_view.visibility,
    saved_view.position,
    saved_view.icon,
    saved_view.config,
    saved_view.deleted,
    saved_view.deleted_at IS NOT NULL AS deleted_at_set,
    project.organization_id::text AS project_organization_id,
    project.workspace_id::text AS project_workspace_id
  FROM tracer_saved_view saved_view
  JOIN tracer_project project ON project.id = saved_view.project_id
  JOIN requested r ON saved_view.id = ANY(r.saved_view_ids)
  ORDER BY saved_view.position ASC, saved_view.created_at ASC
)
SELECT jsonb_build_object(
  'views', COALESCE((SELECT jsonb_agg(to_jsonb(view_rows)) FROM view_rows), '[]'::jsonb),
  'requested_count', (SELECT cardinality(saved_view_ids) FROM requested),
  'active_count', (
    SELECT count(*)
    FROM view_rows
    WHERE deleted = false
  ),
  'deleted_count', (
    SELECT count(*)
    FROM view_rows
    WHERE deleted = true
  ),
  'wrong_scope_count', (
    SELECT count(*)
    FROM view_rows, requested r
    WHERE view_rows.project_id != r.project_id::text
       OR view_rows.workspace_id != r.workspace_id::text
       OR view_rows.project_workspace_id != r.workspace_id::text
       OR view_rows.project_organization_id != r.organization_id::text
  )
)::text
FROM requested;
`;
  return runPostgresJson(sql);
}

function assertActiveSavedViewAudit(
  audit,
  {
    projectId,
    workspaceId,
    userId,
    createdId,
    duplicatedId,
    renamedViewName,
    duplicatedViewName,
  },
) {
  const views = arrayFromAudit(audit?.views);
  assert(
    views.length === 2,
    `Expected two active saved-view rows, got ${JSON.stringify(audit)}.`,
  );
  assert(
    Number(audit?.wrong_scope_count) === 0,
    `Saved-view DB audit found wrong-scope rows: ${JSON.stringify(audit)}.`,
  );
  assert(
    Number(audit?.active_count) === 2,
    `Saved-view DB audit expected two active rows: ${JSON.stringify(audit)}.`,
  );

  const original = views.find((view) => view.id === createdId);
  const duplicate = views.find((view) => view.id === duplicatedId);
  assert(original, "Saved-view DB audit missed the original view.");
  assert(duplicate, "Saved-view DB audit missed the duplicated view.");

  for (const view of [original, duplicate]) {
    assert(
      view.project_id === projectId && view.workspace_id === workspaceId,
      `Saved-view persisted to wrong project/workspace: ${JSON.stringify(view)}.`,
    );
    assert(
      view.created_by_id === userId,
      `Saved-view persisted wrong creator: ${JSON.stringify(view)}.`,
    );
    assert(view.tab_type === "traces", "Saved-view tab_type should be traces.");
    assert(
      view.deleted === false,
      "Saved-view should be active before delete.",
    );
    assert(
      view.config?.display && typeof view.config.display === "object",
      `Saved-view missing display config: ${JSON.stringify(view)}.`,
    );
    assert(
      Array.isArray(view.config?.filters),
      `Saved-view missing filters list: ${JSON.stringify(view)}.`,
    );
  }

  assert(
    original.name === renamedViewName,
    "Original saved-view name mismatch.",
  );
  assert(
    original.visibility === "personal",
    "Original saved-view should remain personal.",
  );
  assert(duplicate.name === duplicatedViewName, "Duplicate name mismatch.");
  assert(
    duplicate.visibility === "project",
    "Duplicate should have been shared with the project.",
  );
  assert(
    Number(original.position) < Number(duplicate.position),
    "Saved-view duplicate should be positioned after original.",
  );
}

function assertDeletedSavedViewAudit(audit, { createdId, duplicatedId }) {
  const views = arrayFromAudit(audit?.views);
  assert(
    views.length === 2,
    `Expected two soft-deleted saved-view rows, got ${JSON.stringify(audit)}.`,
  );
  assert(
    Number(audit?.deleted_count) === 2,
    `Saved-view delete did not soft-delete both rows: ${JSON.stringify(audit)}.`,
  );
  assert(
    Number(audit?.active_count) === 0,
    `Saved-view delete left active rows: ${JSON.stringify(audit)}.`,
  );
  const ids = new Set(views.map((view) => view.id));
  assert(ids.has(createdId), "Deleted audit missed original saved-view id.");
  assert(
    ids.has(duplicatedId),
    "Deleted audit missed duplicate saved-view id.",
  );
  for (const view of views) {
    assert(view.deleted === true, "Saved-view row is not marked deleted.");
    assert(
      view.deleted_at_set === true,
      "Saved-view row did not set deleted_at.",
    );
  }
}

async function cleanupObserveSavedViewFixture({ projectId, savedViewIds }) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuidArray(savedViewIds)} AS saved_view_ids
),
target_saved_views AS (
  SELECT saved_view.id
  FROM tracer_saved_view saved_view, requested r
  WHERE saved_view.project_id = r.project_id
     OR saved_view.id = ANY(r.saved_view_ids)
),
deleted_saved_views AS (
  DELETE FROM tracer_saved_view saved_view
  USING target_saved_views target
  WHERE saved_view.id = target.id
  RETURNING 1
),
deleted_trace_scan_config AS (
  DELETE FROM tracer_trace_scan_config scan
  USING requested r
  WHERE scan.project_id = r.project_id
  RETURNING 1
),
deleted_eval_tasks AS (
  DELETE FROM tracer_eval_task task
  USING requested r
  WHERE task.project_id = r.project_id
  RETURNING 1
),
deleted_monitors AS (
  DELETE FROM tracer_useralertmonitor monitor
  USING requested r
  WHERE monitor.project_id = r.project_id
  RETURNING 1
),
deleted_spans AS (
  DELETE FROM tracer_observation_span span
  USING requested r
  WHERE span.project_id = r.project_id
  RETURNING 1
),
deleted_traces AS (
  DELETE FROM tracer_trace trace
  USING requested r
  WHERE trace.project_id = r.project_id
  RETURNING 1
),
deleted_sessions AS (
  DELETE FROM trace_session session
  USING requested r
  WHERE session.project_id = r.project_id
  RETURNING 1
),
deleted_project_versions AS (
  DELETE FROM tracer_project_version version
  USING requested r
  WHERE version.project_id = r.project_id
  RETURNING 1
),
deleted_projects AS (
  DELETE FROM tracer_project project
  USING requested r
  WHERE project.id = r.project_id
  RETURNING 1
)
SELECT jsonb_build_object(
  'deleted_saved_view_count', (SELECT count(*) FROM deleted_saved_views),
  'deleted_span_count', (SELECT count(*) FROM deleted_spans),
  'deleted_trace_count', (SELECT count(*) FROM deleted_traces),
  'deleted_session_count', (SELECT count(*) FROM deleted_sessions),
  'deleted_project_version_count', (SELECT count(*) FROM deleted_project_versions),
  'deleted_project_count', (SELECT count(*) FROM deleted_projects),
  'remaining_saved_view_count', CASE
    WHEN (SELECT count(*) FROM deleted_saved_views) > 0 THEN 0
    ELSE (
      SELECT count(*)
      FROM tracer_saved_view saved_view, requested r
      WHERE saved_view.project_id = r.project_id
         OR saved_view.id = ANY(r.saved_view_ids)
    )
  END,
  'remaining_project_count', CASE
    WHEN (SELECT count(*) FROM deleted_projects) > 0 THEN 0
    ELSE (
      SELECT count(*)
      FROM tracer_project project, requested r
      WHERE project.id = r.project_id
    )
  END
)::text;
`;
  return runPostgresJson(sql);
}

function assertCleanupAudit(audit) {
  for (const key of ["remaining_saved_view_count", "remaining_project_count"]) {
    assert(Number(audit?.[key]) === 0, `Cleanup left ${key}: ${audit?.[key]}.`);
  }
}

async function hardDeleteObserveSavedViewFixturesByPrefix({
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
target_saved_views AS (
  SELECT saved_view.id
  FROM tracer_saved_view saved_view
  JOIN target_projects target ON saved_view.project_id = target.id
),
deleted_saved_views AS (
  DELETE FROM tracer_saved_view saved_view
  USING target_saved_views target
  WHERE saved_view.id = target.id
  RETURNING 1
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
  'deleted_saved_view_count', (SELECT count(*) FROM deleted_saved_views),
  'deleted_project_count', (SELECT count(*) FROM deleted_projects),
  'remaining_project_count', (
    SELECT count(*)
    FROM tracer_project project
    JOIN requested r
      ON project.organization_id = r.organization_id
     AND project.workspace_id = r.workspace_id
    WHERE project.name LIKE r.name_pattern
  )
)::text;
`;
  return runPostgresJson(sql);
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

async function installAuthenticatedState(page, auth) {
  await page.evaluateOnNewDocument(() => {
    window.normalizeText = (value) => String(value || "").trim();
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
}

function monitorPage(page, { apiFailures, pageErrors, savedViewRequests }) {
  page.on("request", (request) => {
    const url = request.url();
    if (url.includes("/tracer/saved-views/")) {
      savedViewRequests.push(`${request.method()} ${url}`);
    }
  });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/tracer/") && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${response.url()}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
}

async function waitForCreateViewButton(page) {
  await page.waitForSelector("[data-create-view-btn]", {
    visible: true,
    timeout: 30000,
  });
}

async function createSavedViewFromToolbar(page, name) {
  await page.click("[data-create-view-btn]");
  await page.waitForSelector('input[placeholder="Enter your view name"]', {
    visible: true,
    timeout: 30000,
  });
  await page.click('input[placeholder="Enter your view name"]');
  await page.type('input[placeholder="Enter your view name"]', name);
  await clickButtonByText(page, "Save view");
}

async function openViewContextMenu(page, viewName) {
  await waitForSavedViewTab(page, viewName);
  const tabBox = await page.evaluate((expectedName) => {
    const tab = window
      .visibleElements("button, [role='button']")
      .filter((element) =>
        window.normalizeText(element.textContent).includes(expectedName),
      )
      .sort(
        (a, b) =>
          window.normalizeText(a.textContent).length -
          window.normalizeText(b.textContent).length,
      )[0];
    if (!tab) return null;
    const rect = tab.getBoundingClientRect();
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
  }, viewName);
  assert(tabBox, `Could not locate saved-view tab ${viewName}.`);
  await page.mouse.click(tabBox.x, tabBox.y, { button: "right" });
  await waitForVisibleText(page, "Duplicate", { exact: true });
}

async function waitForSavedViewTab(page, viewName) {
  await page.waitForFunction(
    (expectedName) =>
      window
        .visibleElements("button, [role='button']")
        .some((element) =>
          window.normalizeText(element.textContent).includes(expectedName),
        ),
    { timeout: 30000 },
    viewName,
  );
}

async function clickMenuItem(page, label) {
  await page.waitForFunction(
    (expectedLabel) =>
      window
        .visibleElements('[role="menuitem"]')
        .some(
          (element) =>
            window.normalizeText(element.textContent) === expectedLabel,
        ),
    { timeout: 30000 },
    label,
  );
  const clicked = await page.evaluate((expectedLabel) => {
    const item = window
      .visibleElements('[role="menuitem"]')
      .find(
        (element) =>
          window.normalizeText(element.textContent) === expectedLabel,
      );
    if (!item) return false;
    window.dispatchClick(item);
    return true;
  }, label);
  assert(clicked, `Could not click context menu item ${label}.`);
}

async function clickButtonByText(page, label) {
  await page.waitForFunction(
    (expectedLabel) =>
      window
        .visibleElements("button")
        .some(
          (element) =>
            !element.disabled &&
            window.normalizeText(element.textContent) === expectedLabel,
        ),
    { timeout: 30000 },
    label,
  );
  const clicked = await page.evaluate((expectedLabel) => {
    const button = window
      .visibleElements("button")
      .find(
        (element) =>
          !element.disabled &&
          window.normalizeText(element.textContent) === expectedLabel,
      );
    if (!button) return false;
    window.dispatchClick(button);
    return true;
  }, label);
  assert(clicked, `Could not click button ${label}.`);
}

async function clickDialogButton(page, label) {
  await page.waitForFunction(
    (expectedLabel) => {
      const dialogs = window.visibleElements(
        '[role="dialog"], .MuiDialog-root',
      );
      return dialogs.some((dialog) =>
        Array.from(dialog.querySelectorAll("button")).some(
          (button) =>
            !button.disabled &&
            window.normalizeText(button.textContent) === expectedLabel,
        ),
      );
    },
    { timeout: 30000 },
    label,
  );
  const clicked = await page.evaluate((expectedLabel) => {
    const dialogs = window.visibleElements('[role="dialog"], .MuiDialog-root');
    for (const dialog of dialogs) {
      const button = Array.from(dialog.querySelectorAll("button")).find(
        (candidate) =>
          !candidate.disabled &&
          window.normalizeText(candidate.textContent) === expectedLabel,
      );
      if (button) {
        window.dispatchClick(button);
        return true;
      }
    }
    return false;
  }, label);
  assert(clicked, `Could not click dialog button ${label}.`);
}

async function waitForRenameInput(page, currentName) {
  await page.waitForFunction(
    (expectedName) =>
      window
        .visibleElements("input")
        .some((input) => input.value === expectedName),
    { timeout: 30000 },
    currentName,
  );
}

async function setRenameInputValue(page, currentName, nextName) {
  const changed = await page.evaluate(
    ({ currentName: oldName, nextName: newName }) => {
      const input = window
        .visibleElements("input")
        .find((candidate) => candidate.value === oldName);
      if (!input) return false;
      input.focus();
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set;
      setter.call(input, newName);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    },
    { currentName, nextName },
  );
  assert(changed, "Could not set saved-view rename input.");
}

async function waitForDeleteDialog(page, viewName) {
  await page.waitForFunction(
    (expectedName) =>
      window
        .visibleElements('[role="dialog"], .MuiDialog-root')
        .some((dialog) => dialog.textContent.includes(expectedName)),
    { timeout: 30000 },
    viewName,
  );
}

function savedViewCreateResponse() {
  return (response) =>
    isSavedViewResponse(response, "/tracer/saved-views/", "POST");
}

function savedViewUpdateResponse(id, method) {
  return (response) =>
    isSavedViewResponse(
      response,
      apiPath("/tracer/saved-views/{id}/", { id }),
      method,
    );
}

function savedViewDuplicateResponse(id) {
  return (response) =>
    isSavedViewResponse(
      response,
      apiPath("/tracer/saved-views/{id}/duplicate/", { id }),
      "POST",
    );
}

function savedViewDeleteResponse(id) {
  return (response) =>
    isSavedViewResponse(
      response,
      apiPath("/tracer/saved-views/{id}/", { id }),
      "DELETE",
    );
}

function isSavedViewResponse(response, pathname, method) {
  const url = new URL(response.url());
  return (
    url.pathname === pathname &&
    response.request().method() === method &&
    response.status() < 400
  );
}

async function waitForResponseDuring(page, label, predicate, action) {
  const responsePromise = page.waitForResponse(predicate, { timeout: 60000 });
  try {
    await action();
  } catch (error) {
    responsePromise.catch(() => null);
    throw error;
  }
  try {
    return await responsePromise;
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
}

async function readResponseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

async function waitForPath(page, pathname, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname === expectedPath,
    { timeout },
    pathname,
  );
}

async function waitForTabUrl(page, viewId, timeout = 30000) {
  await page.waitForFunction(
    (expectedViewId) => {
      const params = new URLSearchParams(window.location.search);
      return params.get("tab") === `view-${expectedViewId}`;
    },
    { timeout },
    viewId,
  );
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalizeText = (value) => String(value || "").trim();
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
        const textContent = normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
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
      const normalizeText = (value) => String(value || "").trim();
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
          const textContent = normalizeText(element.textContent);
          return exactMatch
            ? textContent === expectedText
            : textContent.includes(expectedText);
        },
      );
    },
    { timeout },
    { text, exact },
  );
}

function arrayFromAudit(value) {
  return Array.isArray(value) ? value : [];
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

function sqlUuidArray(values) {
  const uniqueValues = [...new Set(values || [])];
  if (!uniqueValues.length) return "ARRAY[]::uuid[]";
  return `ARRAY[${uniqueValues.map(sqlUuid).join(", ")}]::uuid[]`;
}

function sqlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
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
