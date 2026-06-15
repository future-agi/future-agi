/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
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
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const EVAL_PREFIX = "ui_eval_list_";
const TAG_VALUE = "output_validation";
const TAG_LABEL = "Output Validation";
const SCREENSHOT_PATH = "/tmp/eval-list-create-delete-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/eval-list-create-delete-smoke-failure.png";
const CODE_EVAL_CODE = [
  "def evaluate(output=None, expected=None, **kwargs):",
  "    return True",
].join("\n");
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);
const READ_POST_PATHS = new Set([
  "/model-hub/eval-templates/list/",
  "/model-hub/eval-templates/list-charts/",
]);
let expectedApiOrigin = new URL(process.env.API_BASE || "http://localhost:8003")
  .origin;

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  expectedApiOrigin = new URL(auth.apiBase).origin;
  const suffix = shortRunId(auth.runId);
  const searchText = `${EVAL_PREFIX}${suffix}`;
  const directEval = {
    name: `${searchText}_alpha`,
    tags: [],
  };
  const browserEval = {
    name: `${searchText}_zulu`,
    tags: [TAG_VALUE],
  };

  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const apiOriginFailures = [];
  const apiOriginMutations = [];
  const evalApiRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let createdTemplateIds = [];
  let uiDeletedTemplateIds = [];

  await hardDeleteEvalFixturesByPrefix(EVAL_PREFIX, cleanupEvidence);

  try {
    const createdDirectEval = await createCodeEval(auth.client, directEval);
    directEval.id = createdDirectEval.id;
    createdTemplateIds.push(directEval.id);

    await assertApiListReadback(auth.client, [directEval], searchText);

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 1050 },
      args: ["--no-sandbox"],
    });
    page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installBrowserState(page, auth);

    page.on("request", (request) => {
      const url = request.url();
      if (
        isExpectedApiOriginUrl(url) &&
        MUTATION_METHODS.has(request.method())
      ) {
        apiOriginMutations.push({
          method: request.method(),
          url,
          body: parseJsonBody(request.postData()),
        });
      }
      if (!isEvalTemplatesApiUrl(url)) return;
      const mutation = {
        method: request.method(),
        url,
        body: parseJsonBody(request.postData()),
      };
      evalApiRequests.push(mutation);
      if (isBrowserMutation(request.method(), url)) {
        browserMutations.push(mutation);
        if (!isAllowedMutation(request.method(), url)) {
          unexpectedMutations.push(`${request.method()} ${maskUrl(url)}`);
        }
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isExpectedApiOriginUrl(url) && response.status() >= 400) {
        apiOriginFailures.push(`${response.status()} ${maskUrl(url)}`);
      }
      if (isEvalTemplatesApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${maskUrl(url)}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponsesDuring(
      page,
      "initial eval list load",
      [
        (response) =>
          isEvalListResponse(response) &&
          response.request().method() === "POST" &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/evaluations`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/evaluations");
    await waitForVisibleText(page, "Create evals", { exact: true });

    const [, createResponse] = await waitForResponsesDuring(
      page,
      "create eval draft",
      [
        (response) =>
          isCreateEvalResponse(response) &&
          response.request().method() === "POST" &&
          response.status() < 400,
      ],
      () => clickVisibleText(page, "Create evals", { exact: true }),
    );
    const draft = await responseJson(createResponse);
    const draftId = draft?.result?.id;
    assert(isUuid(draftId), "Create eval draft did not return a UUID id.");
    browserEval.id = draftId;
    createdTemplateIds.push(draftId);
    await waitForPath(page, `/dashboard/evaluations/create/${draftId}`);

    await clickVisibleText(page, "Code", { exact: true });
    await waitForVisibleText(page, "Scoring", { exact: true });
    await setInputByPlaceholder(
      page,
      "Eg: Hallucination detector",
      browserEval.name,
    );
    await clickVisibleText(page, "Advanced", { exact: true });
    await clickChipByLabel(page, TAG_LABEL);
    await dismissSnackbars(page);

    const [, publishResponse] = await waitForResponsesDuring(
      page,
      "publish browser-created eval",
      [
        (response) =>
          isPublishUpdateResponse(response, {
            templateId: draftId,
            name: browserEval.name,
          }),
      ],
      () => clickEnabledButtonByText(page, "Save Evaluation"),
    );
    await responseJson(publishResponse);
    await waitForPath(page, `/dashboard/evaluations/${draftId}`);

    const created = [directEval, browserEval];
    await assertApiListReadback(auth.client, created, searchText);

    await waitForResponsesDuring(
      page,
      "return to eval list",
      [
        (response) =>
          isEvalListResponse(response) &&
          response.request().method() === "POST" &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/evaluations`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/evaluations");

    await setSearchInput(page, searchText);
    await waitForEvalRows(
      page,
      created.map((template) => template.name),
    );
    await waitForVisibleText(page, "Code", { exact: true });
    await waitForVisibleText(page, "Pass/fail", { exact: true });

    await sortEvalList(page, "Evaluation Name", {
      sortBy: "name",
      sortOrder: "asc",
      search: searchText,
    });
    await waitForEvalRowOrder(page, [directEval.name, browserEval.name]);

    await sortEvalList(page, "Evaluation Name", {
      sortBy: "name",
      sortOrder: "desc",
      search: searchText,
    });
    await waitForEvalRowOrder(page, [browserEval.name, directEval.name]);

    await setQuickTagFilter(page, TAG_LABEL, TAG_VALUE, {
      search: searchText,
      enabled: true,
    });
    await waitForEvalRows(page, [browserEval.name]);
    await waitForNoVisibleExactText(page, directEval.name);

    await setQuickTagFilter(page, TAG_LABEL, TAG_VALUE, {
      search: searchText,
      enabled: false,
    });
    await waitForEvalRows(
      page,
      created.map((template) => template.name),
    );

    for (const template of created) {
      await selectEvalRowByName(page, template.name);
    }
    await waitForVisibleText(page, "2 Selected", { exact: true });
    await clickVisibleText(page, "Delete", { exact: true });
    await waitForVisibleText(page, "Delete 2 evaluations?", { exact: true });

    const [, deleteResponse] = await waitForResponsesDuring(
      page,
      "bulk eval delete",
      [
        (response) =>
          isBulkDeleteResponse(response) &&
          response.request().method() === "POST" &&
          response.status() < 400,
        (response) =>
          isEvalListResponse(response) &&
          response.request().method() === "POST" &&
          response.status() < 400 &&
          requestBody(response)?.search === searchText,
      ],
      () => clickDialogButton(page, "Delete"),
    );
    await responseJson(deleteResponse);
    uiDeletedTemplateIds = created.map((template) => template.id);
    createdTemplateIds = createdTemplateIds.filter(
      (templateId) => !uiDeletedTemplateIds.includes(templateId),
    );

    const deleteMutation = browserMutations.find(
      (mutation) =>
        mutation.method === "POST" &&
        new URL(mutation.url).pathname ===
          "/model-hub/eval-templates/bulk-delete/",
    );
    assert(deleteMutation, "Browser did not issue eval bulk-delete.");
    assertSameMembers(
      asArray(deleteMutation.body?.template_ids),
      uiDeletedTemplateIds,
      "Bulk delete request did not contain the selected eval template ids.",
    );

    const listAfterDelete = await listEvalTemplates(auth.client, {
      search: searchText,
    });
    assert(
      listAfterDelete.items.length === 0,
      `Post-delete API list still returned fixtures: ${JSON.stringify(
        listAfterDelete.items,
      )}`,
    );
    await waitForNoVisibleExactText(page, directEval.name);
    await waitForNoVisibleExactText(page, browserEval.name);

    const dbAudit = await loadEvalListAudit({
      prefix: searchText,
      deletedIds: uiDeletedTemplateIds,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assertEvalListAudit(dbAudit, {
      expectedTotal: created.length,
      expectedDeleted: uiDeletedTemplateIds.length,
      expectedWorkspaceMatches: auth.workspaceId ? created.length : null,
    });

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      unexpectedMutations.length === 0,
      `Unexpected eval-template mutations: ${unexpectedMutations.join(", ")}`,
    );
    assert(
      apiFailures.length === 0,
      `Unexpected eval-template API failures: ${apiFailures.join(", ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    const hardCleanup = await hardDeleteEvalFixturesByPrefix(
      EVAL_PREFIX,
      cleanupEvidence,
    );
    assert(
      Number(hardCleanup.remaining_template_count) === 0,
      `Hard cleanup left eval-list fixtures: ${JSON.stringify(hardCleanup)}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          eval_templates: created.map(({ id, name, tags }) => ({
            id,
            name,
            tags,
          })),
          deleted_template_ids: uiDeletedTemplateIds,
          publish_fallback_used: false,
          db_audit: dbAudit,
          browser_mutations: browserMutations.map(sanitizeMutation),
          eval_api_request_count: evalApiRequests.length,
          screenshot: SCREENSHOT_PATH,
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
    console.error(
      JSON.stringify(
        {
          status: "failed",
          api_failures: apiFailures,
          api_origin_failures: apiOriginFailures,
          api_origin_mutations: apiOriginMutations.map(sanitizeMutation),
          page_errors: pageErrors,
          browser_mutations: browserMutations.map(sanitizeMutation),
          unexpected_mutations: unexpectedMutations,
          eval_api_request_count: evalApiRequests.length,
          form_debug: page
            ? await readEvalCreateDebug(page).catch(() => null)
            : null,
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    if (createdTemplateIds.length) {
      await bulkDeleteEvalTemplates(
        auth.client,
        createdTemplateIds,
        cleanupEvidence,
      ).catch((error) => {
        cleanupEvidence.push({
          cleanup: "public delete eval list fixtures after failure",
          status: "failed",
          error: error.message,
        });
      });
      await hardDeleteEvalFixturesByIds(
        createdTemplateIds,
        cleanupEvidence,
      ).catch((error) => {
        cleanupEvidence.push({
          cleanup: "hard delete eval list fixtures by id after failure",
          status: "failed",
          error: error.message,
        });
      });
    }
    await hardDeleteEvalFixturesByPrefix(EVAL_PREFIX, cleanupEvidence).catch(
      (error) => {
        cleanupEvidence.push({
          cleanup: "hard delete eval list fixtures after failure",
          status: "failed",
          error: error.message,
        });
      },
    );
    if (browser) await browser.close();
  }
}

async function createCodeEval(client, { name, tags }) {
  const created = await client.post(
    apiPath("/model-hub/eval-templates/create-v2/"),
    {
      name,
      eval_type: "code",
      code: CODE_EVAL_CODE,
      code_language: "python",
      output_type: "pass_fail",
      pass_threshold: 0.5,
      description: "Eval list browser smoke fixture.",
      tags,
    },
  );
  assert(isUuid(created?.id), "Code eval create did not return a UUID id.");
  return created;
}

async function assertApiListReadback(client, created, searchText) {
  const byNameAsc = await listEvalTemplates(client, {
    search: searchText,
    sortBy: "name",
    sortOrder: "asc",
  });
  for (const template of created) {
    assert(
      byNameAsc.items.some(
        (candidate) =>
          candidate?.id === template.id && candidate?.name === template.name,
      ),
      `Eval template ${template.name} was missing from API search readback.`,
    );
  }

  const fixtureNames = byNameAsc.items
    .filter((item) => created.some((template) => template.id === item.id))
    .map((item) => item.name);
  assert(
    fixtureNames.join(",") ===
      created
        .map((template) => template.name)
        .sort()
        .join(","),
    `API name sort returned unexpected order: ${fixtureNames.join(",")}`,
  );

  const tagged = await listEvalTemplates(client, {
    search: searchText,
    filters: { tags: [TAG_VALUE] },
    sortBy: "name",
    sortOrder: "asc",
  });
  const taggedIds = tagged.items.map((item) => item.id);
  const expectedTaggedIds = created
    .filter((template) => template.tags.includes(TAG_VALUE))
    .map((template) => template.id);
  assertSameMembers(
    taggedIds,
    expectedTaggedIds,
    "API tag filter did not return the expected eval templates.",
  );
}

async function listEvalTemplates(
  client,
  { search, filters = null, sortBy = "updated_at", sortOrder = "desc" } = {},
) {
  const result = await client.post(apiPath("/model-hub/eval-templates/list/"), {
    page: 0,
    page_size: 25,
    search: search || null,
    owner_filter: "all",
    filters,
    sort_by: sortBy,
    sort_order: sortOrder,
  });
  return {
    ...result,
    items: asArray(result?.items),
  };
}

async function bulkDeleteEvalTemplates(client, templateIds, evidence) {
  const ids = templateIds.filter(Boolean);
  if (!ids.length) return;
  await client.post(
    apiPath("/model-hub/eval-templates/bulk-delete/"),
    { template_ids: ids },
    { okStatuses: [200, 404] },
  );
  evidence.push({
    cleanup: "public delete eval list fixtures",
    status: "passed",
    template_ids: ids,
  });
}

async function loadEvalListAudit({
  prefix,
  deletedIds,
  organizationId,
  workspaceId,
}) {
  const workspaceCheck = workspaceId
    ? `'workspace_id_match_count', count(*) FILTER (WHERE workspace_id = ${sqlUuid(
        workspaceId,
      )})`
    : `'workspace_id_match_count', 0`;
  const sql = `
WITH fixture_templates AS (
  SELECT id, name, deleted, deleted_at, organization_id, workspace_id,
         owner, eval_type, output_type_normalized, eval_tags, visible_ui
  FROM model_hub_evaltemplate
  WHERE name LIKE ${sqlTextLiteral(`${prefix}%`)}
)
SELECT json_build_object(
  'template_count', count(*),
  'organization_id_match_count', count(*) FILTER (
    WHERE organization_id = ${sqlUuid(organizationId)}
  ),
  ${workspaceCheck},
  'workspace_id_null_count', count(*) FILTER (WHERE workspace_id IS NULL),
  'visible_ui_count', count(*) FILTER (WHERE visible_ui = true),
  'user_owner_count', count(*) FILTER (WHERE owner = 'user'),
  'code_eval_count', count(*) FILTER (WHERE eval_type = 'code'),
  'pass_fail_count', count(*) FILTER (WHERE output_type_normalized = 'pass_fail'),
  'tagged_count', count(*) FILTER (
    WHERE eval_tags @> ARRAY[${sqlTextLiteral(TAG_VALUE)}]::varchar[]
  ),
  'deleted_count', count(*) FILTER (WHERE deleted = true),
  'deleted_at_count', count(*) FILTER (WHERE deleted_at IS NOT NULL),
  'selected_deleted_count', count(*) FILTER (
    WHERE id IN (${sqlUuidList(deletedIds)}) AND deleted = true
  ),
  'active_count', count(*) FILTER (WHERE deleted = false),
  'version_count', (
    SELECT count(*)
    FROM model_hub_eval_template_version
    WHERE eval_template_id IN (SELECT id FROM fixture_templates)
  )
)
FROM fixture_templates;
`;
  return runPostgresJson(sql);
}

function assertEvalListAudit(
  audit,
  { expectedTotal, expectedDeleted, expectedWorkspaceMatches },
) {
  assert(
    Number(audit.template_count) === expectedTotal,
    `DB audit expected ${expectedTotal} eval fixtures, got ${audit.template_count}.`,
  );
  assert(
    Number(audit.organization_id_match_count) === expectedTotal,
    "DB audit found an eval fixture outside the active organization.",
  );
  if (expectedWorkspaceMatches !== null) {
    assert(
      Number(audit.workspace_id_match_count) === expectedWorkspaceMatches,
      `DB audit found eval fixtures outside the active workspace: ${JSON.stringify(
        audit,
      )}`,
    );
  }
  assert(
    Number(audit.workspace_id_null_count) === 0,
    `DB audit found eval fixtures without workspace_id: ${JSON.stringify(
      audit,
    )}`,
  );
  assert(
    Number(audit.user_owner_count) === expectedTotal &&
      Number(audit.code_eval_count) === expectedTotal &&
      Number(audit.pass_fail_count) === expectedTotal,
    `DB audit found unexpected eval fixture shape: ${JSON.stringify(audit)}`,
  );
  assert(
    Number(audit.tagged_count) === 1,
    `DB audit expected one tagged fixture: ${JSON.stringify(audit)}`,
  );
  assert(
    Number(audit.selected_deleted_count) === expectedDeleted &&
      Number(audit.deleted_count) === expectedDeleted &&
      Number(audit.deleted_at_count) === expectedDeleted &&
      Number(audit.active_count) === 0,
    `DB audit did not confirm selected bulk delete: ${JSON.stringify(audit)}`,
  );
  assert(
    Number(audit.version_count) >= expectedTotal,
    `DB audit did not find version rows for fixtures: ${JSON.stringify(audit)}`,
  );
}

async function hardDeleteEvalFixturesByPrefix(prefix, evidence) {
  const sql = buildHardDeleteEvalFixturesSql(
    `name LIKE ${sqlTextLiteral(`${prefix}%`)}`,
  );
  const result = await runPostgresJson(sql);
  if (
    Number(result.deleted_template_count) > 0 ||
    Number(result.remaining_template_count) > 0
  ) {
    evidence.push({
      cleanup: "hard delete eval list fixtures",
      status:
        Number(result.remaining_template_count) === 0 ? "passed" : "failed",
      audit: result,
    });
  }
  return result;
}

async function hardDeleteEvalFixturesByIds(templateIds, evidence) {
  const ids = templateIds.filter(Boolean);
  if (!ids.length) {
    return { deleted_template_count: 0, remaining_template_count: 0 };
  }
  const sql = buildHardDeleteEvalFixturesSql(`id IN (${sqlUuidList(ids)})`);
  const result = await runPostgresJson(sql);
  evidence.push({
    cleanup: "hard delete eval list fixtures by id",
    status: Number(result.remaining_template_count) === 0 ? "passed" : "failed",
    audit: result,
  });
  return result;
}

function buildHardDeleteEvalFixturesSql(whereClause) {
  return `
WITH fixture_templates AS (
  SELECT id
  FROM model_hub_evaltemplate
  WHERE ${whereClause}
),
deleted_eval_settings AS (
  DELETE FROM eval_settings
  WHERE eval_id IN (SELECT id FROM fixture_templates)
  RETURNING id
),
deleted_versions AS (
  DELETE FROM model_hub_eval_template_version
  WHERE eval_template_id IN (SELECT id FROM fixture_templates)
  RETURNING id
),
deleted_evaluators AS (
  DELETE FROM model_hub_evaluator
  WHERE eval_template_id IN (SELECT id FROM fixture_templates)
  RETURNING id
),
deleted_templates AS (
  DELETE FROM model_hub_evaltemplate
  WHERE id IN (SELECT id FROM fixture_templates)
  RETURNING id
)
SELECT json_build_object(
  'deleted_eval_setting_count', (SELECT count(*) FROM deleted_eval_settings),
  'deleted_version_count', (SELECT count(*) FROM deleted_versions),
  'deleted_evaluator_count', (SELECT count(*) FROM deleted_evaluators),
  'deleted_template_count', (SELECT count(*) FROM deleted_templates),
  'remaining_template_count', (
    SELECT count(*)
    FROM model_hub_evaltemplate
    WHERE ${whereClause}
      AND id NOT IN (SELECT id FROM deleted_templates)
  )
);
`;
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
    window.setNativeValue = (element, value) => {
      const prototype =
        element instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
      descriptor.set.call(element, value);
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
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

async function waitForResponsesDuring(
  page,
  label,
  predicates,
  action,
  { timeout = 60000 } = {},
) {
  try {
    const responsePromises = predicates.map((predicate) =>
      page.waitForResponse(predicate, { timeout }),
    );
    const actionResult = await action();
    const responses = await Promise.all(responsePromises);
    return [actionResult, ...responses];
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`, { cause: error });
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
        elements.find((candidate) => candidate.closest("a,[role='button']")) ||
        elements.find(
          (candidate) => candidate.getAttribute("role") === "tab",
        ) ||
        elements[0];
      const clickable =
        element?.closest("button,a,[role='button'],[role='menuitem'],tr") ||
        element;
      if (!clickable || clickable.disabled) return false;
      clickable.scrollIntoView({ block: "center", inline: "center" });
      window.dispatchClick(clickable);
      return true;
    },
    { text, exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

async function clickEnabledButtonByText(page, text, timeout = 30000) {
  await page.waitForFunction(
    (expectedText) =>
      window
        .visibleElements("button")
        .some(
          (candidate) =>
            window.normalizeText(candidate.textContent) === expectedText &&
            !candidate.disabled,
        ),
    { timeout },
    text,
  );
  await dismissSnackbars(page);
  const clickBox = await page.evaluate((expectedText) => {
    const button = window
      .visibleElements("button")
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedText &&
          !candidate.disabled,
      );
    if (!button) return null;
    button.scrollIntoView({ block: "center", inline: "center" });
    button.focus();
    const rect = button.getBoundingClientRect();
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
  }, text);
  assert(clickBox, `Could not click enabled button: ${text}`);
  await page.mouse.click(clickBox.x, clickBox.y);
}

async function dismissSnackbars(page) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const closeBox = await page.evaluate(() => {
      const snackbar = window.visibleElements(
        ".SnackbarContent-root, [role='alert']",
      )[0];
      const button = snackbar?.querySelector("button");
      if (!button) return null;
      const rect = button.getBoundingClientRect();
      return {
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
      };
    });
    if (!closeBox) break;
    await page.mouse.click(closeBox.x, closeBox.y);
    await page
      .waitForFunction(
        () =>
          window.visibleElements(".SnackbarContent-root, [role='alert']")
            .length === 0,
        { timeout: 3000 },
      )
      .catch(() => null);
  }
  await page.evaluate(() => {
    window
      .visibleElements(".SnackbarContent-root, [role='alert']")
      .forEach((snackbar) => {
        const root =
          snackbar.closest(".notistack-Snackbar, .SnackbarItem-root") ||
          snackbar;
        root.remove();
      });
  });
}

async function readEvalCreateDebug(page) {
  return page.evaluate(() => {
    const buttons = window.visibleElements("button").map((button) => ({
      text: window.normalizeText(button.textContent),
      disabled: Boolean(button.disabled),
      ariaDisabled: button.getAttribute("aria-disabled"),
      className: button.className,
    }));
    const inputs = window.visibleElements("input,textarea").map((input) => ({
      placeholder: input.placeholder,
      value: input.value,
      disabled: Boolean(input.disabled),
    }));
    const saveButton = window
      .visibleElements("button")
      .find(
        (button) =>
          window.normalizeText(button.textContent) === "Save Evaluation",
      );
    let saveHitTarget = null;
    const savePropChain = [];
    if (saveButton) {
      const rect = saveButton.getBoundingClientRect();
      const hit = document.elementFromPoint(
        rect.left + rect.width / 2,
        rect.top + rect.height / 2,
      );
      saveHitTarget = hit
        ? {
            tagName: hit.tagName,
            text: window.normalizeText(hit.textContent),
            className: hit.className,
          }
        : null;
      let node = saveButton;
      while (node && node !== document.body && savePropChain.length < 6) {
        const reactEntries = Object.keys(node)
          .filter((key) => key.startsWith("__react"))
          .map((key) => ({
            key,
            propKeys:
              node[key] && typeof node[key] === "object"
                ? Object.keys(node[key]).slice(0, 20)
                : [],
            hasOnClick: typeof node[key]?.onClick === "function",
          }));
        savePropChain.push({
          tagName: node.tagName,
          text: window.normalizeText(node.textContent),
          className: node.className,
          reactEntries,
        });
        node = node.parentElement;
      }
    }
    return {
      pathname: window.location.pathname,
      buttons,
      inputs,
      snackbars: window
        .visibleElements(".SnackbarContent-root, [role='alert']")
        .map((element) => window.normalizeText(element.textContent)),
      saveHitTarget,
      savePropChain,
    };
  });
}

async function clickDialogButton(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const clicked = await page.evaluate((expectedLabel) => {
    const dialogs = window.visibleElements('[role="dialog"], .MuiDialog-root');
    const buttons = dialogs.flatMap((dialog) =>
      Array.from(dialog.querySelectorAll("button")),
    );
    const button = buttons.find(
      (candidate) =>
        window.normalizeText(candidate.textContent) === expectedLabel &&
        !candidate.disabled,
    );
    if (!button) return false;
    window.dispatchClick(button);
    return true;
  }, label);
  assert(clicked, `Could not click dialog button: ${label}`);
}

async function clickChipByLabel(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const clicked = await page.evaluate((expectedLabel) => {
    const chip = window
      .visibleElements('[role="button"], .MuiChip-root')
      .find((candidate) => {
        if (window.normalizeText(candidate.textContent) !== expectedLabel) {
          return false;
        }
        return (
          candidate.getAttribute("role") === "button" ||
          candidate.tabIndex >= 0 ||
          Boolean(candidate.onclick)
        );
      });
    if (!chip) return false;
    chip.scrollIntoView({ block: "center", inline: "center" });
    window.dispatchClick(chip);
    return true;
  }, label);
  assert(clicked, `Could not click chip: ${label}`);
}

async function setInputByPlaceholder(
  page,
  placeholder,
  value,
  timeout = 30000,
) {
  await page.waitForFunction(
    (expectedPlaceholder) =>
      window
        .visibleElements("input,textarea")
        .some((element) => element.placeholder === expectedPlaceholder),
    { timeout },
    placeholder,
  );
  const updated = await page.evaluate(
    ({ placeholder: expectedPlaceholder, value: nextValue }) => {
      const input = window
        .visibleElements("input,textarea")
        .find((element) => element.placeholder === expectedPlaceholder);
      if (!input || input.disabled) return false;
      input.focus();
      window.setNativeValue(input, nextValue);
      input.blur();
      return true;
    },
    { placeholder, value },
  );
  assert(updated, `Could not set input placeholder: ${placeholder}`);
}

async function setSearchInput(page, value) {
  await waitForResponsesDuring(
    page,
    `eval list search ${value}`,
    [
      (response) =>
        isEvalListResponse(response) &&
        response.status() < 400 &&
        requestBody(response)?.search === value,
    ],
    () => setInputByPlaceholder(page, "Search", value),
  );
}

async function sortEvalList(page, header, { sortBy, sortOrder, search }) {
  await waitForResponsesDuring(
    page,
    `eval list sort ${sortBy} ${sortOrder}`,
    [
      (response) => {
        if (!isEvalListResponse(response) || response.status() >= 400) {
          return false;
        }
        const body = requestBody(response);
        return (
          body?.search === search &&
          body?.sort_by === sortBy &&
          body?.sort_order === sortOrder
        );
      },
    ],
    () => clickColumnHeader(page, header),
  );
}

async function setQuickTagFilter(page, label, tagValue, { search, enabled }) {
  if (!enabled) {
    await clickChipByLabel(page, label);
    return;
  }

  await waitForResponsesDuring(
    page,
    `eval list enable tag filter ${tagValue}`,
    [
      (response) => {
        if (!isEvalListResponse(response) || response.status() >= 400) {
          return false;
        }
        const body = requestBody(response);
        const tags = asArray(body?.filters?.tags);
        return body?.search === search && tags.includes(tagValue);
      },
    ],
    () => clickChipByLabel(page, label),
  );
}

async function clickColumnHeader(page, header, timeout = 30000) {
  await waitForVisibleText(page, header, { exact: true, timeout });
  const clicked = await page.evaluate((expectedHeader) => {
    const headerElement = window
      .visibleElements('[role="columnheader"]')
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedHeader,
      );
    if (!headerElement) return false;
    window.dispatchClick(headerElement);
    return true;
  }, header);
  assert(clicked, `Could not click column header: ${header}`);
}

async function waitForEvalRows(page, names, timeout = 30000) {
  await page.waitForFunction(
    (expectedNames) => {
      const visibleNames = window
        .visibleElements(".MuiDataGrid-row")
        .map((row) => window.normalizeText(row.textContent));
      return expectedNames.every((name) =>
        visibleNames.some((rowText) => rowText.includes(name)),
      );
    },
    { timeout },
    names,
  );
}

async function waitForEvalRowOrder(page, names, timeout = 30000) {
  await page.waitForFunction(
    (expectedNames) => {
      const rowTexts = window
        .visibleElements(".MuiDataGrid-row")
        .map((row) => window.normalizeText(row.textContent));
      const indexes = expectedNames.map((name) =>
        rowTexts.findIndex((rowText) => rowText.includes(name)),
      );
      if (indexes.some((index) => index < 0)) return false;
      return indexes.every((index, i) => i === 0 || indexes[i - 1] < index);
    },
    { timeout },
    names,
  );
}

async function selectEvalRowByName(page, evalName) {
  await waitForVisibleText(page, evalName, { exact: true });
  const checkboxBox = await page.evaluate((expectedName) => {
    const rows = window.visibleElements(".MuiDataGrid-row");
    const row = rows.find((candidate) =>
      window.normalizeText(candidate.textContent).includes(expectedName),
    );
    const checkbox = row?.querySelector('input[type="checkbox"]');
    if (!checkbox || checkbox.disabled) return null;
    const rect = checkbox.getBoundingClientRect();
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
  }, evalName);
  assert(checkboxBox, `Could not find eval row checkbox: ${evalName}`);
  await page.mouse.click(checkboxBox.x, checkboxBox.y);
  await page.waitForFunction(
    (expectedName) => {
      const rows = window.visibleElements(".MuiDataGrid-row");
      const row = rows.find((candidate) =>
        window.normalizeText(candidate.textContent).includes(expectedName),
      );
      return Boolean(row?.querySelector('input[type="checkbox"]')?.checked);
    },
    { timeout: 30000 },
    evalName,
  );
}

async function responseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function requestBody(response) {
  return parseJsonBody(response.request().postData());
}

function isEvalListResponse(response) {
  const url = new URL(response.url());
  return (
    isEvalTemplatesApiUrl(response.url()) &&
    url.pathname === "/model-hub/eval-templates/list/"
  );
}

function isCreateEvalResponse(response) {
  const url = new URL(response.url());
  return (
    isEvalTemplatesApiUrl(response.url()) &&
    url.pathname === "/model-hub/eval-templates/create-v2/"
  );
}

function isPublishUpdateResponse(response, { templateId, name }) {
  const url = new URL(response.url());
  if (
    !isEvalTemplatesApiUrl(response.url()) ||
    response.request().method() !== "PUT" ||
    response.status() >= 400 ||
    url.pathname !== `/model-hub/eval-templates/${templateId}/update/`
  ) {
    return false;
  }
  const body = requestBody(response);
  return body?.publish === true && body?.name === name;
}

function isBulkDeleteResponse(response) {
  const url = new URL(response.url());
  return (
    isEvalTemplatesApiUrl(response.url()) &&
    url.pathname === "/model-hub/eval-templates/bulk-delete/"
  );
}

function isEvalTemplatesApiUrl(rawUrl) {
  const url = new URL(rawUrl);
  return (
    isExpectedApiOriginUrl(rawUrl) &&
    url.pathname.startsWith("/model-hub/eval-templates/")
  );
}

function isExpectedApiOriginUrl(rawUrl) {
  const url = new URL(rawUrl);
  return url.origin === expectedApiOrigin;
}

function isBrowserMutation(method, rawUrl) {
  if (!MUTATION_METHODS.has(method)) return false;
  const path = new URL(rawUrl).pathname;
  return !(method === "POST" && READ_POST_PATHS.has(path));
}

function isAllowedMutation(method, rawUrl) {
  const path = new URL(rawUrl).pathname;
  return (
    (method === "POST" &&
      (path === "/model-hub/eval-templates/create-v2/" ||
        path === "/model-hub/eval-templates/bulk-delete/")) ||
    (method === "PUT" &&
      /^\/model-hub\/eval-templates\/[^/]+\/update\/$/.test(path))
  );
}

function parseJsonBody(rawBody) {
  if (!rawBody) return null;
  try {
    return JSON.parse(rawBody);
  } catch {
    return rawBody;
  }
}

function assertSameMembers(actual, expected, message) {
  const actualSorted = actual.map(String).sort();
  const expectedSorted = expected.map(String).sort();
  assert(
    actualSorted.length === expectedSorted.length &&
      actualSorted.every((value, index) => value === expectedSorted[index]),
    message,
  );
}

function sanitizeMutation(mutation) {
  return {
    method: mutation.method,
    path: maskUrl(mutation.url),
    body: mutation.body,
  };
}

function maskUrl(rawUrl) {
  const url = new URL(rawUrl);
  return `${url.pathname}${url.search ? "?<query>" : ""}`;
}

function sqlUuid(value) {
  return `${sqlTextLiteral(value)}::uuid`;
}

function sqlUuidList(values) {
  if (!values.length) return "NULL::uuid";
  return values.map(sqlUuid).join(", ");
}

function sqlTextLiteral(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

function shortRunId(runId) {
  return String(runId || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .slice(-8);
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
  process.exit(1);
});
