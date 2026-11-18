/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
  isUuid,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const FIXTURE_PREFIX = "ui_composite_create_";
const SCREENSHOT_PATH = "/tmp/composite-eval-create-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/composite-eval-create-smoke-failure.png";
const CODE_EVAL_CODE = [
  "def evaluate(output=None, expected=None, **kwargs):",
  "    return True",
].join("\n");
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

let expectedApiOrigin = new URL(process.env.API_BASE || "http://localhost:8003")
  .origin;

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  expectedApiOrigin = new URL(auth.apiBase).origin;
  const suffix = shortRunId(auth.runId);
  const childOneName = `${FIXTURE_PREFIX}${suffix}_child_a`;
  const childTwoName = `${FIXTURE_PREFIX}${suffix}_child_b`;
  const compositeName = `${FIXTURE_PREFIX}${suffix}_parent`;
  const compositeDescription = "Composite create form smoke.";

  const evidence = {
    composite_name: compositeName,
    cleanup: [],
    browser_mutations: [],
  };
  const apiFailures = [];
  const apiOriginFailures = [];
  const pageErrors = [];
  const allowedPageErrors = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let childOneId = null;
  let childTwoId = null;
  let draftId = null;
  let compositeId = null;
  let caughtError = null;
  let cleanupComplete = false;

  await hardDeleteEvalFixturesByPrefix(FIXTURE_PREFIX, evidence.cleanup);

  try {
    const childOne = await createCodeEval(auth.client, childOneName);
    childOneId = childOne.id;
    const childTwo = await createCodeEval(auth.client, childTwoName);
    childTwoId = childTwo.id;
    evidence.child_eval_ids = [childOneId, childTwoId];

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
        const mutation = {
          method: request.method(),
          url: maskUrl(url),
          body: parseJsonBody(request.postData()),
        };
        if (
          isCompositeCreateUrl(url) ||
          isEvalCreateDraftUrl(url) ||
          isEvalDraftUpdateUrl(url)
        ) {
          evidence.browser_mutations.push(mutation);
        }
        if (!isAllowedMutation(request.method(), url)) {
          unexpectedMutations.push(`${request.method()} ${maskUrl(url)}`);
        }
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (
        isExpectedApiOriginUrl(url) &&
        response.status() >= 400 &&
        !isAllowedApiOriginFailure(response.status(), url)
      ) {
        apiOriginFailures.push(`${response.status()} ${maskUrl(url)}`);
      }
      if (
        url.includes("/model-hub/eval-templates/") &&
        response.status() >= 400
      ) {
        apiFailures.push(`${response.status()} ${maskUrl(url)}`);
      }
    });
    page.on("pageerror", (error) => {
      const message = error.stack || error.message;
      if (isAllowedPageError(message)) {
        allowedPageErrors.push(firstLine(message));
        return;
      }
      pageErrors.push(message);
    });

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
    await hideReactQueryDevtools(page);
    await waitForPath(page, "/dashboard/evaluations");
    await waitForVisibleText(page, "Create evals", { exact: true });

    const [, draftResponse] = await waitForResponsesDuring(
      page,
      "create eval draft",
      [
        (response) =>
          isEvalCreateDraftResponse(response) &&
          response.request().method() === "POST" &&
          response.status() < 400,
      ],
      () => clickEnabledButtonByText(page, "Create evals"),
    );
    const draft = await responseJson(draftResponse);
    draftId = draft?.result?.id;
    assert(isUuid(draftId), "Create eval draft did not return a UUID id.");
    evidence.draft_id = draftId;
    await waitForPath(page, `/dashboard/evaluations/create/${draftId}`);

    await clickTabByText(page, "Composite");
    await waitForVisibleText(page, "Composite Configuration", {
      exact: true,
    });
    await setCompositeName(page, compositeName);
    await setInputByPlaceholder(
      page,
      "Describe what this composite evaluates",
      compositeDescription,
    );
    await waitForVisibleText(page, "Child evaluation type", { exact: true });
    await waitForVisibleText(page, "Pass / Fail", { exact: true });
    await waitForVisibleText(page, "Children (0)", { exact: true });

    await addChildFromPicker(page, childOneName);
    await waitForVisibleText(page, "Children (1)", { exact: true });
    await waitForVisibleText(page, childOneName);

    await addChildFromPicker(page, childTwoName);
    await waitForVisibleText(page, "Children (2)", { exact: true });
    await waitForVisibleText(page, childTwoName);
    await setChildWeightByName(page, childTwoName, "2");

    const [, compositeResponse] = await waitForResponsesDuring(
      page,
      "save composite eval from create form",
      [
        (response) =>
          isCompositeCreateResponse(response) &&
          response.request().method() === "POST" &&
          response.status() < 400,
      ],
      () => clickEnabledButtonByText(page, "Save Evaluation"),
    );
    const createdComposite = await responseJson(compositeResponse);
    compositeId = createdComposite?.result?.id;
    assert(
      isUuid(compositeId),
      "Composite create form did not return a UUID id.",
    );
    evidence.composite_id = compositeId;

    const createRequest = requestBody(compositeResponse);
    assertCompositeCreateRequest(createRequest, {
      name: compositeName,
      childIds: [childOneId, childTwoId],
      childTwoId,
    });

    await waitForPath(page, `/dashboard/evaluations/${compositeId}`);
    await waitForVisibleText(page, compositeName, { exact: true });
    await waitForVisibleText(page, "Composite Configuration", {
      exact: true,
    });
    await waitForVisibleText(page, "Children (2)", { exact: true });
    await waitForVisibleText(page, childOneName);
    await waitForVisibleText(page, childTwoName);
    await waitForVisibleText(page, "Weighted Average", { exact: true });
    await waitForVisibleText(page, "Run Composite", { exact: true });
    await waitForVisibleText(page, "Save Changes", { exact: true });
    await waitForNoVisibleExactText(page, "Invalid Date");
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    const detail = await auth.client.get(
      apiPath("/model-hub/eval-templates/{template_id}/composite/", {
        template_id: compositeId,
      }),
    );
    assert(
      Array.isArray(detail?.children) && detail.children.length === 2,
      `Composite detail did not return two children: ${JSON.stringify(detail)}`,
    );
    assert(
      detail.children.map((child) => child.child_id).join(",") ===
        [childOneId, childTwoId].join(","),
      `Composite detail child order mismatch: ${JSON.stringify(detail)}`,
    );
    assert(
      Number(
        detail.children.find((child) => child.child_id === childTwoId)?.weight,
      ) === 2,
      `Composite detail did not persist child two weight=2: ${JSON.stringify(
        detail,
      )}`,
    );
    evidence.detail_children = detail.children.map((child) => ({
      child_id: child.child_id,
      child_name: child.child_name,
      weight: child.weight,
      pinned_version_id: child.pinned_version_id || null,
    }));

    const activeAudit = await loadCompositeCreateAudit({
      compositeId,
      childIds: [childOneId, childTwoId],
      draftId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assertCompositeCreateAudit(activeAudit, {
      expectedDeleted: false,
      expectedChildCount: 2,
    });
    evidence.db_audit_active = activeAudit;
    if (allowedPageErrors.length > 0) {
      evidence.allowed_page_errors = allowedPageErrors;
    }

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(
      apiOriginFailures.length === 0,
      `API origin failures: ${apiOriginFailures.join("; ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected browser mutations: ${unexpectedMutations.join("; ")}`,
    );

    await publicDeleteEvalTemplates(
      auth.client,
      [compositeId, draftId, childOneId, childTwoId],
      evidence.cleanup,
    );
    const deletedAudit = await loadCompositeCreateAudit({
      compositeId,
      childIds: [childOneId, childTwoId],
      draftId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assertCompositeCreateAudit(deletedAudit, {
      expectedDeleted: true,
      expectedChildCount: 2,
    });
    evidence.db_audit_after_public_delete = deletedAudit;

    const hardCleanup = await hardDeleteEvalFixturesByIds(
      [compositeId, draftId, childOneId, childTwoId],
      evidence.cleanup,
    );
    assert(
      Number(hardCleanup.remaining_template_count) === 0,
      `Hard cleanup left eval templates behind: ${JSON.stringify(hardCleanup)}`,
    );
    cleanupComplete = true;

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    caughtError = error;
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
          api_origin_failures: apiOriginFailures,
          page_errors: pageErrors,
          unexpected_mutations: unexpectedMutations,
        },
        null,
        2,
      ),
    );
    if (page) {
      await page
        .screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
    }
  } finally {
    if (browser) await browser.close();
    if (!cleanupComplete) {
      await publicDeleteEvalTemplates(
        auth.client,
        [compositeId, draftId, childTwoId, childOneId],
        evidence.cleanup,
      ).catch((error) => {
        caughtError = appendCleanupError(caughtError, error);
      });
      await hardDeleteEvalFixturesByIds(
        [compositeId, draftId, childTwoId, childOneId],
        evidence.cleanup,
      ).catch((error) => {
        caughtError = appendCleanupError(caughtError, error);
      });
      await hardDeleteEvalFixturesByPrefix(
        FIXTURE_PREFIX,
        evidence.cleanup,
      ).catch((error) => {
        caughtError = appendCleanupError(caughtError, error);
      });
    }
  }

  if (caughtError) throw caughtError;
}

async function createCodeEval(client, name) {
  const created = await client.post(
    apiPath("/model-hub/eval-templates/create-v2/"),
    {
      name,
      eval_type: "code",
      code: CODE_EVAL_CODE,
      code_language: "python",
      output_type: "pass_fail",
      pass_threshold: 0.5,
      description: "Composite create form smoke child.",
      tags: ["api-journey", "composite-create-child"],
    },
  );
  assert(isUuid(created?.id), "Code eval create did not return a UUID id.");
  return created;
}

async function addChildFromPicker(page, childName) {
  await clickEnabledButtonByText(page, "Add evaluation");
  await setInputByPlaceholder(page, "Search evaluations...", childName);
  await waitForVisibleText(page, childName);
  await clickRowButtonByText(page, childName, "Add");
  await dispatchEnabledButtonByText(page, "Add to Composite");
  await waitForNoVisibleExactText(page, "Add to Composite");
  await waitForVisibleText(page, childName);
}

function assertCompositeCreateRequest(request, { name, childIds, childTwoId }) {
  assert(request?.name === name, `Composite request name mismatch: ${name}`);
  assert(
    JSON.stringify(request?.child_template_ids) === JSON.stringify(childIds),
    `Composite request child ids mismatch: ${JSON.stringify(request)}`,
  );
  assert(
    request?.aggregation_enabled === true &&
      request?.aggregation_function === "weighted_avg" &&
      request?.composite_child_axis === "pass_fail",
    `Composite request aggregation shape mismatch: ${JSON.stringify(request)}`,
  );
  assert(
    Number(request?.child_weights?.[childTwoId]) === 2,
    `Composite request did not include child two weight=2: ${JSON.stringify(
      request,
    )}`,
  );
  assert(
    request?.child_configs &&
      typeof request.child_configs === "object" &&
      !Array.isArray(request.child_configs),
    `Composite request missing child_configs object: ${JSON.stringify(request)}`,
  );
}

function assertCompositeCreateAudit(
  audit,
  { expectedDeleted, expectedChildCount },
) {
  assert(audit && audit.composite_id, "Composite DB audit returned no row.");
  assert(
    audit.template_type === "composite" &&
      audit.owner === "user" &&
      audit.visible_ui === true,
    `Composite DB audit found unexpected parent shape: ${JSON.stringify(audit)}`,
  );
  assert(
    audit.organization_id_match === true &&
      audit.workspace_id_match === true &&
      Number(audit.workspace_id_null_count) === 0,
    `Composite DB audit found scope mismatch: ${JSON.stringify(audit)}`,
  );
  assert(
    audit.aggregation_enabled === true &&
      audit.aggregation_function === "weighted_avg" &&
      audit.composite_child_axis === "pass_fail",
    `Composite DB audit found aggregation mismatch: ${JSON.stringify(audit)}`,
  );
  assert(
    Number(audit.active_child_count) === expectedChildCount,
    `Composite DB audit expected ${expectedChildCount} active children: ${JSON.stringify(
      audit,
    )}`,
  );
  assert(
    JSON.stringify(audit.child_weights || {}) !== "{}" &&
      Number(audit.child_two_weight) === 2,
    `Composite DB audit did not persist child two weight=2: ${JSON.stringify(
      audit,
    )}`,
  );
  assert(
    Number(audit.version_count) >= 1,
    `Composite DB audit did not find a composite version row: ${JSON.stringify(
      audit,
    )}`,
  );
  assert(
    Number(audit.draft_count) === 1,
    `Composite DB audit did not retain the create-page draft for cleanup: ${JSON.stringify(
      audit,
    )}`,
  );
  assert(
    Boolean(audit.composite_deleted) === expectedDeleted &&
      Boolean(audit.child_one_deleted) === expectedDeleted &&
      Boolean(audit.child_two_deleted) === expectedDeleted &&
      Boolean(audit.draft_deleted) === expectedDeleted,
    `Composite DB audit did not match delete expectation: ${JSON.stringify(
      audit,
    )}`,
  );
}

async function publicDeleteEvalTemplates(client, templateIds, evidence) {
  const ids = [...new Set(templateIds.filter(Boolean))];
  if (!ids.length) return;
  await client.post(
    apiPath("/model-hub/eval-templates/bulk-delete/"),
    { template_ids: ids },
    { okStatuses: [200, 404] },
  );
  evidence.push({
    cleanup: "public delete composite create fixtures",
    status: "passed",
    template_ids: ids,
  });
}

async function loadCompositeCreateAudit({
  compositeId,
  childIds,
  draftId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(compositeId), "compositeId must be a UUID for DB audit.");
  assert(isUuid(draftId), "draftId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const [childOneId, childTwoId] = childIds;
  const sql = `
WITH ids AS (
  SELECT
    ${sqlUuid(compositeId)}::uuid AS composite_id,
    ${sqlUuid(draftId)}::uuid AS draft_id,
    ${sqlUuid(childOneId)}::uuid AS child_one_id,
    ${sqlUuid(childTwoId)}::uuid AS child_two_id,
    ${sqlUuid(organizationId)}::uuid AS organization_id,
    ${sqlUuid(workspaceId)}::uuid AS workspace_id
),
templates AS (
  SELECT et.*
  FROM model_hub_evaltemplate et
  WHERE et.id IN (
    (SELECT composite_id FROM ids),
    (SELECT draft_id FROM ids),
    (SELECT child_one_id FROM ids),
    (SELECT child_two_id FROM ids)
  )
),
composite AS (
  SELECT et.*
  FROM templates et, ids
  WHERE et.id = ids.composite_id
)
SELECT json_build_object(
  'composite_id', (SELECT id::text FROM composite),
  'name', (SELECT name FROM composite),
  'owner', (SELECT owner FROM composite),
  'visible_ui', (SELECT visible_ui FROM composite),
  'template_type', (SELECT template_type FROM composite),
  'organization_id_match', (
    SELECT organization_id = (SELECT organization_id FROM ids) FROM composite
  ),
  'workspace_id_match', (
    SELECT workspace_id = (SELECT workspace_id FROM ids) FROM composite
  ),
  'workspace_id_null_count', (
    SELECT count(*) FROM templates WHERE workspace_id IS NULL
  ),
  'aggregation_enabled', (SELECT aggregation_enabled FROM composite),
  'aggregation_function', (SELECT aggregation_function FROM composite),
  'composite_child_axis', (SELECT composite_child_axis FROM composite),
  'active_child_count', (
    SELECT count(*)
    FROM model_hub_composite_eval_child cec, ids
    WHERE cec.parent_id = ids.composite_id AND cec.deleted = false
  ),
  'child_ids', (
    SELECT COALESCE(json_agg(cec.child_id::text ORDER BY cec.order), '[]'::json)
    FROM model_hub_composite_eval_child cec, ids
    WHERE cec.parent_id = ids.composite_id AND cec.deleted = false
  ),
  'child_weights', (
    SELECT COALESCE(json_object_agg(cec.child_id::text, cec.weight), '{}'::json)
    FROM model_hub_composite_eval_child cec, ids
    WHERE cec.parent_id = ids.composite_id AND cec.deleted = false
  ),
  'child_two_weight', (
    SELECT cec.weight
    FROM model_hub_composite_eval_child cec, ids
    WHERE cec.parent_id = ids.composite_id
      AND cec.child_id = ids.child_two_id
      AND cec.deleted = false
  ),
  'version_count', (
    SELECT count(*)
    FROM model_hub_eval_template_version v, ids
    WHERE v.eval_template_id = ids.composite_id
  ),
  'draft_count', (
    SELECT count(*)
    FROM templates, ids
    WHERE templates.id = ids.draft_id
  ),
  'composite_deleted', (
    SELECT deleted FROM templates, ids WHERE templates.id = ids.composite_id
  ),
  'child_one_deleted', (
    SELECT deleted FROM templates, ids WHERE templates.id = ids.child_one_id
  ),
  'child_two_deleted', (
    SELECT deleted FROM templates, ids WHERE templates.id = ids.child_two_id
  ),
  'draft_deleted', (
    SELECT deleted FROM templates, ids WHERE templates.id = ids.draft_id
  )
);
`;
  return runPostgresJson(sql);
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
      cleanup: "hard delete composite fixtures by prefix",
      status:
        Number(result.remaining_template_count) === 0 ? "passed" : "failed",
      audit: result,
    });
  }
  return result;
}

async function hardDeleteEvalFixturesByIds(templateIds, evidence) {
  const ids = [...new Set(templateIds.filter(Boolean))];
  if (!ids.length) {
    return { deleted_template_count: 0, remaining_template_count: 0 };
  }
  const sql = buildHardDeleteEvalFixturesSql(`id IN (${sqlUuidList(ids)})`);
  const result = await runPostgresJson(sql);
  evidence.push({
    cleanup: "hard delete composite fixtures by id",
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
deleted_composite_child_links AS (
  DELETE FROM model_hub_composite_eval_child
  WHERE parent_id IN (SELECT id FROM fixture_templates)
     OR child_id IN (SELECT id FROM fixture_templates)
  RETURNING id
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
  'deleted_composite_child_link_count', (
    SELECT count(*) FROM deleted_composite_child_links
  ),
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
  if (process.env.API_JOURNEY_DB_HOST) {
    return runPostgresJsonViaPython(sql);
  }
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

async function runPostgresJsonViaPython(sql) {
  const python = process.env.API_JOURNEY_DB_PYTHON || "python3";
  const code = [
    "import json, os",
    "import psycopg",
    "conn = psycopg.connect(",
    "    host=os.environ['API_JOURNEY_DB_HOST'],",
    "    port=int(os.environ.get('API_JOURNEY_DB_PORT', '5432')),",
    "    dbname=os.environ.get('API_JOURNEY_DB_NAME', 'tfc'),",
    "    user=os.environ.get('API_JOURNEY_DB_USER', 'user'),",
    "    password=os.environ.get('API_JOURNEY_DB_PASSWORD') or None,",
    ")",
    "conn.autocommit = True",
    "try:",
    "    with conn.cursor() as cur:",
    "        cur.execute(os.environ['API_JOURNEY_DB_SQL'])",
    "        row = cur.fetchone()",
    "        print(json.dumps(row[0] if row else None))",
    "finally:",
    "    conn.close()",
  ].join("\n");
  const { stdout } = await execFileAsync(python, ["-c", code], {
    env: { ...process.env, API_JOURNEY_DB_SQL: sql },
    maxBuffer: 10 * 1024 * 1024,
  });
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
      localStorage.setItem("TanstackQueryDevtools.open", "false");
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

async function hideReactQueryDevtools(page) {
  await page.addStyleTag({
    content: ".tsqd-parent-container { display: none !important; }",
  });
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

async function clickTabByText(page, text, timeout = 30000) {
  await page.waitForFunction(
    (expectedText) =>
      window
        .visibleElements('[role="tab"]')
        .some(
          (tab) =>
            window.normalizeText(tab.textContent) === expectedText &&
            tab.getAttribute("aria-disabled") !== "true",
        ),
    { timeout },
    text,
  );
  const clicked = await page.evaluate((expectedText) => {
    const tab = window
      .visibleElements('[role="tab"]')
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedText &&
          candidate.getAttribute("aria-disabled") !== "true",
      );
    if (!tab) return false;
    tab.scrollIntoView({ block: "center", inline: "center" });
    window.dispatchClick(tab);
    return true;
  }, text);
  assert(clicked, `Could not click tab: ${text}`);
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

async function dispatchEnabledButtonByText(page, text, timeout = 30000) {
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
  const clicked = await page.evaluate((expectedText) => {
    const button = window
      .visibleElements("button")
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedText &&
          !candidate.disabled,
      );
    if (!button) return false;
    button.scrollIntoView({ block: "center", inline: "center" });
    button.focus();
    window.dispatchClick(button);
    return true;
  }, text);
  assert(clicked, `Could not dispatch click for enabled button: ${text}`);
}

async function clickRowButtonByText(
  page,
  rowText,
  buttonText,
  timeout = 30000,
) {
  await page.waitForFunction(
    ({ rowText: expectedRowText, buttonText: expectedButtonText }) =>
      window.visibleElements("tr").some((row) => {
        if (!window.normalizeText(row.textContent).includes(expectedRowText)) {
          return false;
        }
        return Array.from(row.querySelectorAll("button")).some(
          (button) =>
            window.normalizeText(button.textContent) === expectedButtonText &&
            !button.disabled,
        );
      }),
    { timeout },
    { rowText, buttonText },
  );
  const clicked = await page.evaluate(
    ({ rowText: expectedRowText, buttonText: expectedButtonText }) => {
      const row = window
        .visibleElements("tr")
        .find((candidate) =>
          window.normalizeText(candidate.textContent).includes(expectedRowText),
        );
      const button = Array.from(row?.querySelectorAll("button") || []).find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedButtonText &&
          !candidate.disabled,
      );
      if (!button) return false;
      button.scrollIntoView({ block: "center", inline: "center" });
      button.focus();
      window.dispatchClick(button);
      return true;
    },
    { rowText, buttonText },
  );
  assert(clicked, `Could not click ${buttonText} for row ${rowText}`);
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
        .some((input) => input.placeholder === expectedPlaceholder),
    { timeout },
    placeholder,
  );
  const changed = await page.evaluate(
    ({ placeholder: expectedPlaceholder, value: nextValue }) => {
      const input = window
        .visibleElements("input,textarea")
        .find((candidate) => candidate.placeholder === expectedPlaceholder);
      if (!input) return false;
      input.focus();
      window.setNativeValue(input, nextValue);
      return true;
    },
    { placeholder, value },
  );
  assert(changed, `Could not set input placeholder ${placeholder}`);
}

async function setCompositeName(page, value, timeout = 30000) {
  await page.waitForFunction(
    () =>
      window
        .visibleElements("input")
        .some((input) => !input.disabled && input.placeholder === ""),
    { timeout },
  );
  const changed = await page.evaluate((nextValue) => {
    const labels = window.visibleElements().filter((element) => {
      const text = window.normalizeText(element.textContent);
      return text === "Name*" || text === "Name *" || text === "Name";
    });
    for (const label of labels) {
      const wrapper = label.closest("div");
      const input =
        wrapper?.parentElement?.querySelector("input") ||
        wrapper?.querySelector("input");
      if (input) {
        input.focus();
        window.setNativeValue(input, nextValue);
        return true;
      }
    }
    const input = window
      .visibleElements("input")
      .find((candidate) => !candidate.disabled && candidate.placeholder === "");
    if (!input) return false;
    input.focus();
    window.setNativeValue(input, nextValue);
    return true;
  }, value);
  assert(changed, "Could not set composite name.");
}

async function setChildWeightByName(page, childName, value, timeout = 30000) {
  await page.waitForFunction(
    (expectedChildName) =>
      window.visibleElements().some((element) => {
        if (window.normalizeText(element.textContent) !== expectedChildName) {
          return false;
        }
        const card = element.closest("div");
        return Boolean(
          card?.parentElement?.querySelector("input[type=number]"),
        );
      }),
    { timeout },
    childName,
  );
  const changed = await page.evaluate(
    ({ childName: expectedChildName, value: nextValue }) => {
      const nameElement = window
        .visibleElements()
        .find(
          (element) =>
            window.normalizeText(element.textContent) === expectedChildName,
        );
      let node = nameElement;
      while (node && node !== document.body) {
        const input = node.querySelector?.("input[type=number]");
        if (input) {
          input.focus();
          window.setNativeValue(input, nextValue);
          return true;
        }
        node = node.parentElement;
      }
      return false;
    },
    { childName, value },
  );
  assert(changed, `Could not set weight for child ${childName}`);
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

async function responseJson(response) {
  return response.json().catch(() => null);
}

function requestBody(response) {
  return parseJsonBody(response.request().postData());
}

function parseJsonBody(body) {
  if (!body) return null;
  try {
    return JSON.parse(body);
  } catch {
    return body;
  }
}

function isEvalListResponse(response) {
  return response.url().includes("/model-hub/eval-templates/list/");
}

function isEvalCreateDraftUrl(url) {
  return url.includes("/model-hub/eval-templates/create-v2/");
}

function isEvalCreateDraftResponse(response) {
  return isEvalCreateDraftUrl(response.url());
}

function isCompositeCreateUrl(url) {
  return url.includes("/model-hub/eval-templates/create-composite/");
}

function isCompositeCreateResponse(response) {
  return isCompositeCreateUrl(response.url());
}

function isExpectedApiOriginUrl(url) {
  return new URL(url).origin === expectedApiOrigin;
}

function isAllowedApiOriginFailure(status, url) {
  return (
    status === 402 && new URL(url).pathname === "/model-hub/knowledge-base/get/"
  );
}

function isAllowedPageError(message) {
  return (
    (message.includes("[MSW] Failed to register the Service Worker") &&
      message.includes("mockServiceWorker.js") &&
      message.includes("Operation has been aborted")) ||
    (message.includes("Failed to execute 'importScripts'") &&
      message.includes("cdn.jsdelivr.net/npm/monaco-editor"))
  );
}

function firstLine(value) {
  return String(value || "").split("\n")[0];
}

function isAllowedMutation(method, url) {
  if (
    method === "PUT" &&
    url.includes("/model-hub/eval-templates/") &&
    url.includes("/update/")
  ) {
    return true;
  }
  if (method !== "POST") return false;
  return (
    url.includes("/model-hub/get-eval-template-names") ||
    url.includes("/model-hub/eval-templates/list/") ||
    url.includes("/model-hub/eval-templates/list-charts/") ||
    url.includes("/model-hub/eval-templates/create-v2/") ||
    url.includes("/model-hub/eval-templates/create-composite/")
  );
}

function isEvalDraftUpdateUrl(url) {
  return url.includes("/model-hub/eval-templates/") && url.includes("/update/");
}

function maskUrl(url) {
  const parsed = new URL(url);
  return `${parsed.origin}${parsed.pathname}${parsed.search}`;
}

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID for SQL literal, got ${value}.`);
  return `'${String(value).replace(/'/g, "''")}'::uuid`;
}

function sqlUuidList(values) {
  const ids = values.filter(Boolean);
  if (!ids.length) return "NULL";
  return ids.map((value) => sqlUuid(value)).join(",");
}

function sqlTextLiteral(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

function appendCleanupError(caughtError, cleanupError) {
  if (!caughtError) return cleanupError;
  caughtError.message = `${caughtError.message}; cleanup failed: ${cleanupError.message}`;
  return caughtError;
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
