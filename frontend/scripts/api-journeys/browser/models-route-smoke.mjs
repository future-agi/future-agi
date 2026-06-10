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
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const ENABLE_MUTATING_SEED = process.env.API_JOURNEY_MUTATIONS === "1";
const SCREENSHOT_PATH = "/tmp/th4812-models-route-smoke.png";
const DETAIL_SCREENSHOT_PATH = "/tmp/th4812-models-route-detail-smoke.png";
const CUSTOM_METRICS_SCREENSHOT_PATH =
  "/tmp/th4812-models-route-custom-metrics-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/th4812-models-route-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const marker = `th4812_model_route_${normalizeRunId(auth.runId)}`;
  let seededFixture = null;
  let fixtureCleaned = false;
  if (ENABLE_MUTATING_SEED) {
    const userId = auth.user?.id;
    assert(
      isUuid(userId),
      "Models route seed requires an authenticated user id.",
    );
    await hardDeleteModelsRouteFixture({
      marker,
      organizationId: auth.organizationId,
    });
    seededFixture = await seedModelsRouteFixture({
      marker,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      userId,
    });
  }

  const modelsPayload = await auth.client.get(
    apiPath("/model-hub/custom-models/"),
    {
      query: seededFixture
        ? { search_query: seededFixture.active_name, page_size: 20 }
        : undefined,
      unwrap: false,
    },
  );
  const models = asArray(modelsPayload?.results || modelsPayload);
  const firstModel = seededFixture
    ? models.find((model) => model?.id === seededFixture.active_model_id) ||
      null
    : models.find((model) => isUuid(model?.id)) || null;
  const firstModelName = modelDisplayName(firstModel);
  if (seededFixture) {
    assert(
      firstModel,
      `Seeded Models route fixture was not returned by the list API: ${JSON.stringify(models)}`,
    );
    const hiddenPayload = await auth.client.get(
      apiPath("/model-hub/custom-models/"),
      {
        query: { search_query: seededFixture.hidden_name, page_size: 20 },
        unwrap: false,
      },
    );
    const hiddenModels = asArray(hiddenPayload?.results || hiddenPayload);
    assert(
      !hiddenModels.some(
        (model) => model?.id === seededFixture.hidden_model_id,
      ),
      "Models route list leaked a same-org other-workspace custom model.",
    );
  }
  const apiFailures = [];
  const pageErrors = [];
  const evidence = {
    model_count: models.length,
    first_model_id: firstModel?.id || null,
    first_model_name: firstModelName || null,
    seeded_fixture: Boolean(seededFixture),
    hidden_model_id: seededFixture?.hidden_model_id || null,
  };

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });

  const page = await browser.newPage();
  await page.setBypassServiceWorker(true);
  await installRuntimeConfig(page, auth);
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      if (organizationId) {
        sessionStorage.setItem("organizationId", organizationId);
      }
      if (workspaceId) {
        sessionStorage.setItem("workspaceId", workspaceId);
      }
      if (user?.id) {
        sessionStorage.setItem("futureagi-current-user-id", user.id);
      }
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );

  page.on("response", (response) => {
    const path = safePathname(response.url());
    if (
      (path?.startsWith("/model-hub/custom-models/") ||
        path?.startsWith("/model-hub/custom-metric/") ||
        path?.startsWith("/model-hub/performance/options/")) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${response.url()}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await waitForResponseDuring(
      page,
      "models list",
      (response) =>
        response.url().includes("/model-hub/custom-models/") &&
        !response.url().includes("/model-hub/custom-models/list/") &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/models`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/models",
      { timeout: 30000 },
    );
    evidence.route_heading = await waitForAnyVisibleText(page, [
      "Models",
      "Model",
    ]);
    await waitForVisibleText(page, "Add Model", { exact: true });

    if (firstModel) {
      if (seededFixture) {
        await waitForResponseDuring(
          page,
          "models list search",
          (response) =>
            response.url().includes("/model-hub/custom-models/") &&
            response
              .url()
              .includes(
                `search_query=${encodeURIComponent(seededFixture.active_name)}`,
              ) &&
            response.request().method() === "GET" &&
            response.status() < 400,
          () =>
            setInputByPlaceholder(page, "Search", seededFixture.active_name),
        );
        await waitForNoVisibleText(page, seededFixture.hidden_name, {
          exact: true,
        });
      }
      await waitForVisibleText(page, firstModelName);
      await waitForResponseDuring(
        page,
        "model detail",
        (response) =>
          response
            .url()
            .includes(`/model-hub/custom-models/${firstModel.id}/`) &&
          response.request().method() === "GET" &&
          response.status() < 400,
        () => clickVisibleText(page, firstModelName),
      );
      await page.waitForFunction(
        (modelId) =>
          window.location.pathname ===
          `/dashboard/models/${modelId}/performance`,
        { timeout: 30000 },
        firstModel.id,
      );
      await waitForVisibleText(page, "Performance", { exact: true });
      await waitForVisibleText(page, "Custom Metrics", { exact: true });
      await waitForVisibleText(page, "Datasets", { exact: true });
      if (seededFixture) {
        await waitForVisibleText(page, seededFixture.active_name, {
          exact: true,
        });
        await waitForNoVisibleText(page, "New Model", { exact: true });
      }
      await page.screenshot({ path: DETAIL_SCREENSHOT_PATH, fullPage: true });
      evidence.detail_screenshot = DETAIL_SCREENSHOT_PATH;

      if (seededFixture) {
        await waitForResponseDuring(
          page,
          "custom metrics list",
          (response) =>
            response
              .url()
              .includes(
                `/model-hub/custom-metric/${seededFixture.active_model_id}/`,
              ) &&
            response.request().method() === "GET" &&
            response.status() < 400,
          () => clickTabByLabel(page, "Custom Metrics"),
        );
        await page.waitForFunction(
          (modelId) =>
            window.location.pathname ===
            `/dashboard/models/${modelId}/custom-metrics`,
          { timeout: 30000 },
          seededFixture.active_model_id,
        );
        await waitForVisibleText(page, seededFixture.metric_name);
        await waitForNoVisibleText(page, seededFixture.hidden_name, {
          exact: true,
        });
        await page.screenshot({
          path: CUSTOM_METRICS_SCREENSHOT_PATH,
          fullPage: true,
        });
        evidence.custom_metrics_screenshot = CUSTOM_METRICS_SCREENSHOT_PATH;
      }
    } else {
      await waitForVisibleText(page, "You need to create a Model.", {
        exact: true,
      });
      evidence.detail_skipped = "No model rows returned by the real list API.";
    }

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    if (seededFixture) {
      const cleanupAudit = await hardDeleteModelsRouteFixture({
        marker,
        organizationId: auth.organizationId,
        customModelIds: [
          seededFixture.active_model_id,
          seededFixture.hidden_model_id,
        ],
        aiModelIds: [seededFixture.active_model_id],
        metricIds: [seededFixture.metric_id],
        workspaceIds: [seededFixture.hidden_workspace_id],
      });
      fixtureCleaned = true;
      assert(
        Number(cleanupAudit.remaining_custom_model_count) === 0 &&
          Number(cleanupAudit.remaining_ai_model_count) === 0 &&
          Number(cleanupAudit.remaining_metric_count) === 0 &&
          Number(cleanupAudit.remaining_workspace_count) === 0,
        `Models route cleanup left residue: ${JSON.stringify(cleanupAudit)}`,
      );
      evidence.cleanup = cleanupAudit;
    }

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
    await page.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true });
    console.error(
      JSON.stringify(
        {
          status: "failed",
          error: error.message,
          debug: await collectDebugState(page),
          error_screenshot: FAILURE_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    if (seededFixture && !fixtureCleaned) {
      await hardDeleteModelsRouteFixture({
        marker,
        organizationId: auth.organizationId,
        customModelIds: [
          seededFixture.active_model_id,
          seededFixture.hidden_model_id,
        ],
        aiModelIds: [seededFixture.active_model_id],
        metricIds: [seededFixture.metric_id],
        workspaceIds: [seededFixture.hidden_workspace_id],
      });
    }
    await browser.close();
  }
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

async function waitForResponseDuring(page, label, predicate, action) {
  const responsePromise = page.waitForResponse(predicate, { timeout: 60000 });
  await action();
  const response = await responsePromise;
  assert(
    response.status() >= 200 && response.status() < 400,
    `${label} response failed with HTTP ${response.status()}.`,
  );
  return response;
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
      const isElementVisible = (element) => {
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
        if (!isElementVisible(element)) return false;
        const textContent = normalized(element.textContent);
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function waitForAnyVisibleText(
  page,
  texts,
  { exact = true, timeout = 30000 } = {},
) {
  const handle = await page.waitForFunction(
    ({ candidates, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
      const isElementVisible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      };
      return (
        candidates.find((candidate) =>
          Array.from(document.querySelectorAll("body *")).some((element) => {
            if (!isElementVisible(element)) return false;
            const textContent = normalized(element.textContent);
            return exactMatch
              ? textContent === candidate
              : textContent.includes(candidate);
          }),
        ) || null
      );
    },
    { timeout },
    { candidates: texts, exact },
  );
  return handle.jsonValue();
}

async function clickVisibleText(page, text) {
  const handle = await page.waitForFunction(
    (expectedText) => {
      const isElementVisible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      };
      return (
        Array.from(document.querySelectorAll("body *")).find((element) => {
          if (!isElementVisible(element)) return false;
          return String(element.textContent || "").trim() === expectedText;
        }) || null
      );
    },
    { timeout: 30000 },
    text,
  );
  const element = handle.asElement();
  assert(element, `Could not resolve visible text "${text}".`);
  const box = await element.boundingBox();
  assert(box, `Could not resolve visible text box "${text}".`);
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

async function clickTabByLabel(page, label) {
  const clicked = await page.evaluate((expectedLabel) => {
    const normalized = (value) => String(value || "").trim();
    const isElementVisible = (element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    };
    const tab = Array.from(document.querySelectorAll('[role="tab"]')).find(
      (element) =>
        isElementVisible(element) &&
        normalized(element.textContent) === expectedLabel,
    );
    if (!tab) return false;
    tab.click();
    return true;
  }, label);
  assert(clicked, `Could not click tab "${label}".`);
}

async function setInputByPlaceholder(page, placeholder, value) {
  await page.waitForSelector(`input[placeholder="${placeholder}"]`, {
    visible: true,
    timeout: 30000,
  });
  await page.click(`input[placeholder="${placeholder}"]`, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.keyboard.type(value);
}

async function waitForNoVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
      const isElementVisible = (element) => {
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
          if (!isElementVisible(element)) return false;
          const textContent = normalized(element.textContent);
          if (exactMatch) return textContent === expectedText;
          return textContent.includes(expectedText);
        },
      );
    },
    { timeout },
    { text, exact },
  );
}

async function collectDebugState(page) {
  return page.evaluate(() => ({
    path: window.location.pathname,
    visibleText: String(document.body?.innerText || "").slice(0, 3000),
  }));
}

function modelDisplayName(model) {
  return String(model?.user_model_id || model?.name || model?.id || "").trim();
}

function safePathname(url) {
  try {
    return new URL(url).pathname;
  } catch {
    return "";
  }
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

async function seedModelsRouteFixture({
  marker,
  organizationId,
  workspaceId,
  userId,
}) {
  assert(isUuid(organizationId), "Models route seed requires organization id.");
  assert(isUuid(workspaceId), "Models route seed requires workspace id.");
  assert(isUuid(userId), "Models route seed requires user id.");

  const hiddenWorkspaceId = randomUUID();
  const activeModelId = randomUUID();
  const hiddenModelId = randomUUID();
  const metricId = randomUUID();
  const activeName = `${marker}_active_model`;
  const hiddenName = `${marker}_hidden_model`;
  const hiddenWorkspaceName = `${marker}_hidden_workspace`;
  const metricName = `${marker}_quality_metric`;

  const seeded = await runPostgresJson(`
WITH inserted_workspace AS (
  INSERT INTO accounts_workspace (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    display_name,
    description,
    is_active,
    is_default,
    created_by_id,
    organization_id
  )
  VALUES (
    NOW(),
    NOW(),
    false,
    NULL,
    ${sqlUuid(hiddenWorkspaceId)},
    ${sqlTextLiteral(hiddenWorkspaceName)},
    ${sqlTextLiteral(hiddenWorkspaceName)},
    ${sqlTextLiteral("Temporary workspace for TH-4812 Models route smoke.")},
    true,
    false,
    ${sqlUuid(userId)},
    ${sqlUuid(organizationId)}
  )
  RETURNING id
),
inserted_custom_models AS (
  INSERT INTO model_hub_customaimodel (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    user_model_id,
    key_config,
    provider,
    input_token_cost,
    output_token_cost,
    user_id,
    organization_id,
    workspace_id,
    baseline_model_environment,
    baseline_model_version,
    default_metric_id
  )
  VALUES
    (
      NOW(),
      NOW(),
      false,
      NULL,
      ${sqlUuid(activeModelId)},
      ${sqlTextLiteral(activeName)},
      NULL,
      'openai',
      0.011,
      0.021,
      ${sqlUuid(userId)},
      ${sqlUuid(organizationId)},
      ${sqlUuid(workspaceId)},
      NULL,
      NULL,
      NULL
    ),
    (
      NOW(),
      NOW(),
      false,
      NULL,
      ${sqlUuid(hiddenModelId)},
      ${sqlTextLiteral(hiddenName)},
      NULL,
      'openai',
      0.012,
      0.022,
      ${sqlUuid(userId)},
      ${sqlUuid(organizationId)},
      ${sqlUuid(hiddenWorkspaceId)},
      NULL,
      NULL,
      NULL
    )
  RETURNING id
),
inserted_ai_model AS (
  INSERT INTO model_hub_aimodel (
    id,
    created_at,
    user_model_id,
    deleted,
    model_type,
    baseline_model_environment,
    baseline_model_version,
    organization_id,
    workspace_id,
    default_metric_id
  )
  VALUES (
    ${sqlUuid(activeModelId)},
    NOW(),
    ${sqlTextLiteral(activeName)},
    false,
    'GenerativeLLM',
    NULL,
    NULL,
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)},
    NULL
  )
  RETURNING id
),
inserted_metric AS (
  INSERT INTO model_hub_metric (
    id,
    name,
    created_at,
    updated_at,
    text_prompt,
    criteria_breakdown,
    model_id,
    develop_id,
    metric_type,
    used_in,
    evaluation_type,
    datasets,
    eval_rag_context,
    eval_rag_output,
    eval_prompt_template,
    tags
  )
  VALUES (
    ${sqlUuid(metricId)},
    ${sqlTextLiteral(metricName)},
    NOW(),
    NOW(),
    ${sqlTextLiteral("Score the TH-4812 model route output.")},
    ARRAY[]::varchar[],
    ${sqlUuid(activeModelId)},
    NULL,
    'WholeUserOutput',
    'model',
    'EvalOutput',
    '[]'::jsonb,
    false,
    false,
    false,
    ARRAY['quality:good', 'quality:bad']::varchar[]
  )
  RETURNING id
)
SELECT json_build_object(
  'active_model_id', ${sqlTextLiteral(activeModelId)},
  'hidden_model_id', ${sqlTextLiteral(hiddenModelId)},
  'hidden_workspace_id', ${sqlTextLiteral(hiddenWorkspaceId)},
  'metric_id', ${sqlTextLiteral(metricId)},
  'active_name', ${sqlTextLiteral(activeName)},
  'hidden_name', ${sqlTextLiteral(hiddenName)},
  'metric_name', ${sqlTextLiteral(metricName)},
  'inserted_workspace_count', (SELECT count(*) FROM inserted_workspace),
  'inserted_custom_model_count', (SELECT count(*) FROM inserted_custom_models),
  'inserted_ai_model_count', (SELECT count(*) FROM inserted_ai_model),
  'inserted_metric_count', (SELECT count(*) FROM inserted_metric)
);
`);

  assert(
    Number(seeded.inserted_workspace_count) === 1 &&
      Number(seeded.inserted_custom_model_count) === 2 &&
      Number(seeded.inserted_ai_model_count) === 1 &&
      Number(seeded.inserted_metric_count) === 1,
    `Models route seed failed: ${JSON.stringify(seeded)}`,
  );
  return seeded;
}

async function hardDeleteModelsRouteFixture({
  marker,
  organizationId,
  customModelIds = [],
  aiModelIds = [],
  metricIds = [],
  workspaceIds = [],
}) {
  assert(
    isUuid(organizationId),
    "Models route cleanup requires organization id.",
  );
  const customIdPredicate = customModelIds.length
    ? `OR custom.id = ANY(${sqlUuidArray(customModelIds)})`
    : "";
  const aiModelIdPredicate = aiModelIds.length
    ? `OR model.id = ANY(${sqlUuidArray(aiModelIds)})`
    : "";
  const metricIdPredicate = metricIds.length
    ? `OR metric.id = ANY(${sqlUuidArray(metricIds)})`
    : "";
  const workspaceIdPredicate = workspaceIds.length
    ? `OR workspace.id = ANY(${sqlUuidArray(workspaceIds)})`
    : "";

  const deleted = await runPostgresJson(`
WITH target_custom AS (
  SELECT custom.id
  FROM model_hub_customaimodel custom
  WHERE custom.organization_id = ${sqlUuid(organizationId)}
    AND (
      custom.user_model_id LIKE ${sqlTextLiteral(`${marker}%`)}
      ${customIdPredicate}
    )
),
target_ai_models AS (
  SELECT model.id
  FROM model_hub_aimodel model
  WHERE model.organization_id = ${sqlUuid(organizationId)}
    AND (
      model.user_model_id LIKE ${sqlTextLiteral(`${marker}%`)}
      ${aiModelIdPredicate}
    )
),
target_metrics AS (
  SELECT metric.id
  FROM model_hub_metric metric
  LEFT JOIN target_ai_models model ON model.id = metric.model_id
  WHERE model.id IS NOT NULL
     OR metric.name LIKE ${sqlTextLiteral(`${marker}%`)}
     ${metricIdPredicate}
),
target_workspaces AS (
  SELECT workspace.id
  FROM accounts_workspace workspace
  WHERE workspace.organization_id = ${sqlUuid(organizationId)}
    AND (
      workspace.name LIKE ${sqlTextLiteral(`${marker}%`)}
      ${workspaceIdPredicate}
    )
),
deleted_custom AS (
  DELETE FROM model_hub_customaimodel custom
  USING target_custom target
  WHERE custom.id = target.id
  RETURNING custom.id
),
deleted_metrics AS (
  DELETE FROM model_hub_metric metric
  USING target_metrics target
  WHERE metric.id = target.id
  RETURNING metric.id
),
deleted_ai_models AS (
  DELETE FROM model_hub_aimodel model
  USING target_ai_models target
  WHERE model.id = target.id
  RETURNING model.id
),
deleted_workspaces AS (
  DELETE FROM accounts_workspace workspace
  USING target_workspaces target
  WHERE workspace.id = target.id
  RETURNING workspace.id
)
SELECT json_build_object(
  'deleted_custom_model_count', (SELECT count(*) FROM deleted_custom),
  'deleted_metric_count', (SELECT count(*) FROM deleted_metrics),
  'deleted_ai_model_count', (SELECT count(*) FROM deleted_ai_models),
  'deleted_workspace_count', (SELECT count(*) FROM deleted_workspaces)
);
`);

  const remaining = await runPostgresJson(`
SELECT json_build_object(
  'remaining_custom_model_count', (
    SELECT count(*)::int
    FROM model_hub_customaimodel custom
    WHERE custom.organization_id = ${sqlUuid(organizationId)}
      AND (
        custom.user_model_id LIKE ${sqlTextLiteral(`${marker}%`)}
        ${customIdPredicate}
      )
  ),
  'remaining_metric_count', (
    SELECT count(*)::int
    FROM model_hub_metric metric
    WHERE metric.name LIKE ${sqlTextLiteral(`${marker}%`)}
       ${metricIdPredicate}
  ),
  'remaining_ai_model_count', (
    SELECT count(*)::int
    FROM model_hub_aimodel model
    WHERE model.organization_id = ${sqlUuid(organizationId)}
      AND (
        model.user_model_id LIKE ${sqlTextLiteral(`${marker}%`)}
        ${aiModelIdPredicate}
      )
  ),
  'remaining_workspace_count', (
    SELECT count(*)::int
    FROM accounts_workspace workspace
    WHERE workspace.organization_id = ${sqlUuid(organizationId)}
      AND (
        workspace.name LIKE ${sqlTextLiteral(`${marker}%`)}
        ${workspaceIdPredicate}
      )
  )
);
`);

  return { ...deleted, ...remaining };
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
  assert(text, "Postgres Models route helper returned no JSON output.");
  return JSON.parse(text);
}

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID SQL value, got ${value}`);
  return `'${value}'::uuid`;
}

function sqlUuidArray(values) {
  const filtered = values.filter(Boolean);
  if (filtered.length === 0) return "ARRAY[]::uuid[]";
  return `ARRAY[${filtered.map(sqlUuid).join(", ")}]`;
}

function sqlTextLiteral(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function normalizeRunId(value) {
  return String(value || Date.now().toString(36))
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .slice(0, 48);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
