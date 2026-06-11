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
const SHARE_DIALOG_SCREENSHOT_PATH =
  "/tmp/observe-shared-link-dialog-smoke.png";
const SHARED_PAGE_SCREENSHOT_PATH = "/tmp/observe-shared-link-page-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/observe-shared-link-smoke-failure.png";
const PROJECT_PREFIX = "ui_observe_shared_link_";

async function main() {
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  await hardDeleteObserveSharedLinkFixturesByPrefix({
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    prefix: PROJECT_PREFIX,
  });
  const fixture = await createObserveProject(
    auth.client,
    `${PROJECT_PREFIX}${suffix}`,
  );
  let cleanupDone = false;
  let browser = null;
  const apiFailures = [];
  const pageErrors = [];
  const sharedLinkRequests = [];
  const evidence = {
    project_id: fixture.project_id,
    project_name: fixture.project_name,
    invite_email: `shared-link-${suffix}@futureagi.localtest.dev`,
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

    const page = await browser.newPage();
    await installRuntimeConfig(page, auth);
    await installAuthenticatedState(page, auth);
    monitorPage(page, { apiFailures, pageErrors, sharedLinkRequests });

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

    const createResponse = await waitForResponseDuring(
      page,
      "Observe project share-link create",
      sharedLinkCreateResponse(fixture.project_id),
      () => clickProjectShareButton(page),
    );
    const createBody = await readResponseJson(createResponse);
    const createdLink = createBody?.result || createBody;
    assert(
      isUuid(createdLink?.id) && createdLink?.token,
      `Share link create returned unexpected payload: ${JSON.stringify(createBody)}`,
    );
    evidence.shared_link_id = createdLink.id;
    evidence.shared_token_prefix = String(createdLink.token).slice(0, 8);

    await waitForVisibleText(page, "Share", { exact: true });
    await waitForVisibleText(page, "/shared/");
    await waitForSharedLinkDetailWithAccess(page, createdLink.id, {
      expectedEmail: null,
    });

    const publicResponse = await waitForResponseDuring(
      page,
      "Observe project share-link public access update",
      sharedLinkUpdateResponse(createdLink.id),
      () => clickDialogAccessOption(page, "Anyone with the link"),
    );
    const publicBody = await readResponseJson(publicResponse);
    const publicLink = publicBody?.result || publicBody;
    assert(
      publicLink?.access_type === "public",
      `Share link PATCH did not return public access: ${JSON.stringify(publicBody)}`,
    );

    const addAccessResponse = await waitForResponseDuring(
      page,
      "Observe project share-link invite",
      sharedLinkAddAccessResponse(createdLink.id),
      () => inviteEmail(page, evidence.invite_email),
    );
    const addAccessBody = await readResponseJson(addAccessResponse);
    const accessRows = addAccessBody?.result || addAccessBody;
    const accessRow = Array.isArray(accessRows) ? accessRows[0] : null;
    assert(
      isUuid(accessRow?.id),
      `Share-link invite returned no access row id: ${JSON.stringify(addAccessBody)}`,
    );
    evidence.access_id = accessRow.id;
    await waitForVisibleText(page, evidence.invite_email);
    await waitForSharedLinkDetailWithAccess(page, createdLink.id, {
      expectedEmail: evidence.invite_email,
    });

    const removeResponse = await waitForResponseDuring(
      page,
      "Observe project share-link remove access",
      sharedLinkRemoveAccessResponse(createdLink.id, accessRow.id),
      () => removeEmailAccess(page, evidence.invite_email),
    );
    assert(
      removeResponse.status() < 400,
      `Share-link remove access returned HTTP ${removeResponse.status()}`,
    );
    await waitForNoVisibleText(page, evidence.invite_email);
    await page.screenshot({
      path: SHARE_DIALOG_SCREENSHOT_PATH,
      fullPage: true,
    });
    evidence.dialog_screenshot = SHARE_DIALOG_SCREENSHOT_PATH;

    const publicResolve = await fetch(
      `${auth.apiBase}${apiPath("/tracer/shared/{token}/", {
        token: createdLink.token,
      })}`,
    );
    const publicResolveBody = await publicResolve.json();
    assert(
      publicResolve.ok,
      `Public shared-link resolve failed: ${JSON.stringify(publicResolveBody)}`,
    );
    assert(
      publicResolveBody?.resource_type === "project" &&
        publicResolveBody?.resource_id === fixture.project_id,
      "Public shared-link resolve returned the wrong project.",
    );
    assert(
      publicResolveBody?.data?.name === fixture.project_name,
      "Public shared-link resolve omitted the project name.",
    );
    evidence.public_resolve = {
      resource_type: publicResolveBody.resource_type,
      resource_id: publicResolveBody.resource_id,
      access_type: publicResolveBody.access_type,
    };

    const sharedPage = await browser.newPage();
    await installRuntimeConfig(sharedPage, auth);
    await sharedPage.evaluateOnNewDocument(() => {
      localStorage.clear();
      sessionStorage.clear();
    });
    monitorPage(sharedPage, { apiFailures, pageErrors, sharedLinkRequests });
    await sharedPage.goto(`${APP_BASE}/shared/${createdLink.token}`, {
      waitUntil: "domcontentloaded",
    });
    await waitForVisibleText(sharedPage, "Shared project", { exact: true });
    await waitForVisibleText(sharedPage, fixture.project_name);
    await waitForVisibleText(sharedPage, "Project details");
    await waitForVisibleText(sharedPage, fixture.project_id);
    await sharedPage.screenshot({
      path: SHARED_PAGE_SCREENSHOT_PATH,
      fullPage: true,
    });
    evidence.shared_page_screenshot = SHARED_PAGE_SCREENSHOT_PATH;

    evidence.shared_link_audit = await loadSharedLinkDbAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      projectId: fixture.project_id,
      sharedLinkId: createdLink.id,
      accessId: accessRow.id,
      email: evidence.invite_email,
    });
    assertSharedLinkAudit(evidence.shared_link_audit, {
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      projectId: fixture.project_id,
      sharedLinkId: createdLink.id,
      accessId: accessRow.id,
      email: evidence.invite_email,
    });

    const cleanupAudit = await cleanupObserveSharedLinkFixture({
      projectId: fixture.project_id,
      sharedLinkId: createdLink.id,
    });
    cleanupDone = true;
    evidence.cleanup = cleanupAudit;
    assertCleanupAudit(cleanupAudit);

    assert(
      apiFailures.length === 0,
      `Unexpected API failures: ${apiFailures.join("; ")}`,
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
          shared_link_request_count: sharedLinkRequests.length,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    if (browser) {
      const pages = await browser.pages();
      await Promise.all(
        pages.map((page, index) =>
          page
            .screenshot({
              path:
                index === 0
                  ? FAILURE_SCREENSHOT_PATH
                  : `/tmp/observe-shared-link-smoke-failure-page-${index + 1}.png`,
              fullPage: true,
            })
            .catch(() => null),
        ),
      );
    }
    console.error(`failure_screenshot=${FAILURE_SCREENSHOT_PATH}`);
    throw error;
  } finally {
    if (!cleanupDone) {
      await cleanupObserveSharedLinkFixture({
        projectId: fixture.project_id,
        sharedLinkId: evidence.shared_link_id,
      }).catch((error) => {
        console.error(`Cleanup failed: ${error.message}`);
      });
    }
    if (browser) await browser.close();
  }
}

async function createObserveProject(client, name) {
  const created = await client.post(apiPath("/tracer/project/"), {
    name,
    model_type: "GenerativeLLM",
    trace_type: "observe",
    metadata: { source: "browser-smoke", marker: "shared-link" },
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

async function loadSharedLinkDbAudit({
  organizationId,
  workspaceId,
  projectId,
  sharedLinkId,
  accessId,
  email,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuid(sharedLinkId)} AS shared_link_id,
    ${sqlUuid(accessId)} AS access_id,
    ${sqlString(email)} AS email
)
SELECT jsonb_build_object(
  'shared_link_id', link.id::text,
  'resource_type', link.resource_type,
  'resource_id', link.resource_id,
  'organization_id', link.organization_id::text,
  'workspace_id', link.workspace_id::text,
  'access_type', link.access_type,
  'is_active', link.is_active,
  'deleted', link.deleted,
  'access_id', access.id::text,
  'access_email', access.email,
  'access_deleted', access.deleted,
  'active_access_count', (
    SELECT count(*)
    FROM tracer_sharedlinkaccess active_access, requested r
    WHERE active_access.shared_link_id = r.shared_link_id
      AND active_access.deleted = false
  ),
  'deleted_access_count', (
    SELECT count(*)
    FROM tracer_sharedlinkaccess deleted_access, requested r
    WHERE deleted_access.shared_link_id = r.shared_link_id
      AND deleted_access.deleted = true
  )
)::text
FROM requested
JOIN tracer_sharedlink link
  ON link.id = requested.shared_link_id
 AND link.organization_id = requested.organization_id
 AND link.workspace_id = requested.workspace_id
 AND link.resource_type = 'project'
 AND link.resource_id = requested.project_id::text
JOIN tracer_sharedlinkaccess access
  ON access.id = requested.access_id
 AND access.shared_link_id = requested.shared_link_id
 AND access.email = requested.email;
`;
  return runPostgresJson(sql);
}

function assertSharedLinkAudit(
  audit,
  { organizationId, workspaceId, projectId, sharedLinkId, accessId, email },
) {
  assert(
    audit?.shared_link_id === sharedLinkId,
    "Shared-link audit id mismatch.",
  );
  assert(audit?.resource_type === "project", "Shared-link resource mismatch.");
  assert(audit?.resource_id === projectId, "Shared-link project mismatch.");
  assert(
    audit?.organization_id === organizationId,
    "Shared-link organization mismatch.",
  );
  assert(
    audit?.workspace_id === workspaceId,
    "Shared-link workspace mismatch.",
  );
  assert(audit?.access_type === "public", "Shared-link was not made public.");
  assert(audit?.is_active === true, "Shared-link is not active.");
  assert(audit?.deleted === false, "Shared-link is deleted.");
  assert(audit?.access_id === accessId, "Access audit id mismatch.");
  assert(audit?.access_email === email, "Access audit email mismatch.");
  assert(audit?.access_deleted === true, "Access row was not soft-deleted.");
  assert(
    Number(audit?.active_access_count) === 0,
    `Expected no active access rows, got ${audit?.active_access_count}.`,
  );
  assert(
    Number(audit?.deleted_access_count) === 1,
    `Expected one deleted access row, got ${audit?.deleted_access_count}.`,
  );
}

async function cleanupObserveSharedLinkFixture({ projectId, sharedLinkId }) {
  const sharedLinkFilter = isUuid(sharedLinkId)
    ? `OR link.id = ${sqlUuid(sharedLinkId)}`
    : "";
  const sql = `
WITH requested AS (
  SELECT ${sqlUuid(projectId)} AS project_id
),
target_links AS (
  SELECT link.id
  FROM tracer_sharedlink link, requested r
  WHERE (
    link.resource_type = 'project'
    AND link.resource_id = r.project_id::text
  )
  ${sharedLinkFilter}
),
deleted_shared_link_access AS (
  DELETE FROM tracer_sharedlinkaccess access
  USING target_links target
  WHERE access.shared_link_id = target.id
  RETURNING 1
),
deleted_shared_links AS (
  DELETE FROM tracer_sharedlink link
  USING target_links target
  WHERE link.id = target.id
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
  'deleted_shared_link_access_count', (SELECT count(*) FROM deleted_shared_link_access),
  'deleted_shared_link_count', (SELECT count(*) FROM deleted_shared_links),
  'deleted_span_count', (SELECT count(*) FROM deleted_spans),
  'deleted_trace_count', (SELECT count(*) FROM deleted_traces),
  'deleted_session_count', (SELECT count(*) FROM deleted_sessions),
  'deleted_project_version_count', (SELECT count(*) FROM deleted_project_versions),
  'deleted_project_count', (SELECT count(*) FROM deleted_projects),
  'remaining_shared_link_count', CASE
    WHEN (SELECT count(*) FROM deleted_shared_links) > 0 THEN 0
    ELSE (
      SELECT count(*)
      FROM tracer_sharedlink link, requested r
      WHERE link.resource_type = 'project'
        AND link.resource_id = r.project_id::text
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
  for (const key of [
    "remaining_shared_link_count",
    "remaining_project_count",
  ]) {
    assert(Number(audit?.[key]) === 0, `Cleanup left ${key}: ${audit?.[key]}.`);
  }
}

async function hardDeleteObserveSharedLinkFixturesByPrefix({
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
target_links AS (
  SELECT link.id
  FROM tracer_sharedlink link
  JOIN target_projects project
    ON link.resource_type = 'project'
   AND link.resource_id = project.id::text
),
deleted_shared_link_access AS (
  DELETE FROM tracer_sharedlinkaccess access
  USING target_links target
  WHERE access.shared_link_id = target.id
  RETURNING 1
),
deleted_shared_links AS (
  DELETE FROM tracer_sharedlink link
  USING target_links target
  WHERE link.id = target.id
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
  'deleted_shared_link_access_count', (SELECT count(*) FROM deleted_shared_link_access),
  'deleted_shared_link_count', (SELECT count(*) FROM deleted_shared_links),
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
    window.findObserveProjectShareButton = () => {
      const buttons = window
        .visibleElements("button")
        .filter((button) => {
          const rect = button.getBoundingClientRect();
          const text = window.normalizeText(button.textContent);
          return (
            !button.disabled &&
            !text &&
            rect.top < 60 &&
            rect.left > window.innerWidth - 80 &&
            rect.width >= 24 &&
            rect.width <= 44 &&
            rect.height >= 24 &&
            rect.height <= 44
          );
        })
        .sort(
          (a, b) =>
            b.getBoundingClientRect().left - a.getBoundingClientRect().left,
        );
      return buttons[0] || null;
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

function monitorPage(page, { apiFailures, pageErrors, sharedLinkRequests }) {
  page.on("request", (request) => {
    const url = request.url();
    if (url.includes("/tracer/shared")) {
      sharedLinkRequests.push(`${request.method()} ${url}`);
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (url.includes("/tracer/") && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
}

async function clickProjectShareButton(page) {
  await page.waitForFunction(
    () =>
      window.visibleElements(".component-iconify").some((icon) => {
        const iconName =
          icon.getAttribute("icon") || icon.getAttribute("data-icon") || "";
        const button = icon.closest("button");
        return iconName === "basil:share-outline" && button && !button.disabled;
      }) || Boolean(window.findObserveProjectShareButton?.()),
    { timeout: 30000 },
  );
  const clicked = await page.evaluate(() => {
    const icon = window
      .visibleElements(".component-iconify")
      .find((candidate) => {
        const iconName =
          candidate.getAttribute("icon") ||
          candidate.getAttribute("data-icon") ||
          "";
        const button = candidate.closest("button");
        return iconName === "basil:share-outline" && button && !button.disabled;
      });
    const button =
      icon?.closest("button") || window.findObserveProjectShareButton?.();
    if (!button) return false;
    window.dispatchClick(button);
    return true;
  });
  assert(clicked, "Could not click Observe project Share button.");
}

async function clickDialogAccessOption(page, label) {
  await page.waitForFunction(
    (expectedLabel) =>
      window
        .visibleElements('[role="button"]')
        .some((element) => element.textContent.includes(expectedLabel)),
    { timeout: 30000 },
    label,
  );
  const clicked = await page.evaluate((expectedLabel) => {
    const button = window
      .visibleElements('[role="button"]')
      .find((element) => element.textContent.includes(expectedLabel));
    if (!button) return false;
    window.dispatchClick(button);
    return true;
  }, label);
  assert(clicked, `Could not click Share dialog access option: ${label}`);
}

async function inviteEmail(page, email) {
  await page.waitForSelector('input[placeholder="name@email.com"]', {
    visible: true,
    timeout: 30000,
  });
  await page.click('input[placeholder="name@email.com"]');
  await page.type('input[placeholder="name@email.com"]', email);
  const clicked = await page.evaluate(() => {
    const button = Array.from(document.querySelectorAll("button")).find(
      (candidate) =>
        !candidate.disabled &&
        String(candidate.textContent || "").trim() === "Invite",
    );
    if (!button) return false;
    window.dispatchClick(button);
    return true;
  });
  assert(clicked, "Could not click Share dialog Invite button.");
}

async function removeEmailAccess(page, email) {
  const selector = `button[aria-label="Remove access for ${email}"]`;
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector);
}

function sharedLinkCreateResponse(projectId) {
  return async (response) => {
    if (!isSharedLinkResponse(response, "/tracer/shared-links/", "POST")) {
      return false;
    }
    const body = await response.json().catch(() => null);
    const result = body?.result || body;
    return (
      result?.resource_type === "project" && result?.resource_id === projectId
    );
  };
}

function sharedLinkUpdateResponse(sharedLinkId) {
  return (response) =>
    isSharedLinkResponse(
      response,
      `/tracer/shared-links/${sharedLinkId}/`,
      "PATCH",
    );
}

function sharedLinkAddAccessResponse(sharedLinkId) {
  return (response) =>
    isSharedLinkResponse(
      response,
      `/tracer/shared-links/${sharedLinkId}/access/`,
      "POST",
    );
}

function sharedLinkRemoveAccessResponse(sharedLinkId, accessId) {
  return (response) =>
    isSharedLinkResponse(
      response,
      `/tracer/shared-links/${sharedLinkId}/access/${accessId}/`,
      "DELETE",
    );
}

function isSharedLinkResponse(response, pathname, method) {
  const url = new URL(response.url());
  return (
    url.pathname === pathname &&
    response.request().method() === method &&
    response.status() < 400
  );
}

async function waitForSharedLinkDetailWithAccess(
  page,
  sharedLinkId,
  { expectedEmail },
) {
  await page.waitForResponse(
    async (response) => {
      if (
        !isSharedLinkResponse(
          response,
          `/tracer/shared-links/${sharedLinkId}/`,
          "GET",
        )
      ) {
        return false;
      }
      if (!expectedEmail) return true;
      const body = await response.json().catch(() => null);
      const result = body?.result || body;
      const accessList = result?.access_list || result?.accessList || [];
      return accessList.some((entry) => entry?.email === expectedEmail);
    },
    { timeout: 60000 },
  );
  await delay(500);
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

async function waitForNoVisibleText(page, text, { timeout = 30000 } = {}) {
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
          String(element.textContent || "").includes(expectedText),
      );
    },
    { timeout },
    text,
  );
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

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
