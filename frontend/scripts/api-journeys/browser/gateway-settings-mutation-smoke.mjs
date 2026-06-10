/* eslint-disable no-console */
import { execFile as execFileCallback } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFile = promisify(execFileCallback);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const EMAIL_ALERT_CREATE_SCREENSHOT_PATH =
  "/tmp/gateway-settings-email-alert-create-smoke.png";
const EMAIL_ALERT_EDIT_SCREENSHOT_PATH =
  "/tmp/gateway-settings-email-alert-edit-smoke.png";
const EMAIL_ALERT_DELETE_SCREENSHOT_PATH =
  "/tmp/gateway-settings-email-alert-delete-smoke.png";
const HEALTH_RELOAD_SCREENSHOT_PATH =
  "/tmp/gateway-settings-health-reload-smoke.png";
const ORG_CONFIG_SCREENSHOT_PATH =
  "/tmp/gateway-settings-org-config-save-smoke.png";
const BATCH_SCREENSHOT_PATH = "/tmp/gateway-settings-batch-submit-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/gateway-settings-mutation-smoke-failure.png";
const EMAIL_ALERT_PREFIX = "ui_settings_email_alert_";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();

  const auth = await createAuthenticatedContext();
  const apiFailures = [];
  const pageErrors = [];
  const gatewayRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  const expectedApiFailures = [];
  let browser = null;
  let page = null;
  let caughtError = null;
  let cleanupError = null;
  let cleanup = null;
  let evidence = {};

  try {
    const baseline = await prepareOrgConfigRestorer(auth.client);
    cleanup = baseline.cleanup;
    evidence = await preflightSettings(auth.client, baseline, auth.runId);
    evidence.email_alert_initial_cleanup =
      await hardDeleteEmailAlertFixturesByPrefix({
        organizationId: auth.organizationId,
      });

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 980 },
      args: ["--no-sandbox"],
    });
    page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installBrowserState(page, auth);

    page.on("request", (request) => {
      const url = request.url();
      if (!isGatewayApiUrl(url)) return;
      gatewayRequests.push(`${request.method()} ${url}`);
      if (MUTATION_METHODS.has(request.method())) {
        const mutation = `${request.method()} ${url}`;
        browserMutations.push(mutation);
        if (!isAllowedSettingsMutation(request.method(), url)) {
          unexpectedMutations.push(mutation);
        }
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isExpectedSettingsFailure(response)) {
        expectedApiFailures.push(
          `${response.status()} ${response.request().method()} ${url}`,
        );
        return;
      }
      if (isGatewayApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponsesDuring(
      page,
      "initial Gateway Settings mutation load",
      [
        gatewayListResponse(),
        gatewayConfigResponse(evidence.gateway_id),
        emailAlertsResponse(),
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/settings`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/settings");

    for (const label of [
      "Settings",
      "Gateway configuration, health checks, and administration",
      "Health Check",
      "Reload Config",
      "Org Config",
      "Batch Jobs",
      "Full Config (Read-Only)",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    evidence.email_alert_lifecycle = await exerciseEmailAlertMutationFlow({
      page,
      auth,
    });

    const [healthResponse] = await waitForResponsesDuring(
      page,
      "run Gateway Settings health check",
      [healthCheckResponse(evidence.gateway_id)],
      () => clickVisibleText(page, "Health Check", { exact: true }),
    );
    evidence.health_check = await responseResult(healthResponse);
    assert(
      evidence.health_check?.status === "healthy",
      `Health check did not return healthy: ${JSON.stringify(
        evidence.health_check,
      )}`,
    );
    await waitForVisibleText(page, "Health check complete");

    const [reloadResponse] = await waitForResponsesDuring(
      page,
      "run Gateway Settings reload",
      [reloadConfigResponse(evidence.gateway_id), gatewayConfigResponse()],
      () => clickVisibleText(page, "Reload Config", { exact: true }),
    );
    evidence.reload_config = await responseResult(reloadResponse);
    assert(
      evidence.reload_config?.status === "ok",
      `Reload config did not return ok: ${JSON.stringify(
        evidence.reload_config,
      )}`,
    );
    await waitForVisibleText(page, "Configuration reloaded");
    await page.screenshot({
      path: HEALTH_RELOAD_SCREENSHOT_PATH,
      fullPage: true,
    });
    evidence.health_reload_screenshot = HEALTH_RELOAD_SCREENSHOT_PATH;

    await clickVisibleText(page, "Org Config", { exact: true });
    await waitForPath(page, "/dashboard/gateway/settings/org-config");
    await waitForVisibleText(
      page,
      `Version ${evidence.original_org_config_version}`,
      { exact: true },
    );
    await clickVisibleText(page, "Edit Config", { exact: true });
    await waitForVisibleText(page, "Edit Organization Config", {
      exact: true,
    });
    await typeIntoPlaceholder(
      page,
      "Change description (optional)",
      evidence.change_description,
    );

    const [orgConfigResponse] = await waitForResponsesDuring(
      page,
      "save Gateway Settings org config",
      [orgConfigCreateResponse(), orgConfigActiveResponse()],
      () => clickDialogButton(page, "Save & Activate"),
    );
    evidence.saved_org_config = await responseResult(orgConfigResponse);
    await waitForTextGone(page, "Edit Organization Config");
    await waitForVisibleText(page, "Config saved and activated");
    assert(
      evidence.saved_org_config?.id &&
        evidence.saved_org_config?.version >
          evidence.original_org_config_version,
      `Org config save did not return a new version: ${JSON.stringify(
        evidence.saved_org_config,
      )}`,
    );

    const activeAfterSave = await auth.client.get(
      apiPath("/agentcc/org-configs/active/"),
    );
    assert(
      activeAfterSave?.id === evidence.saved_org_config.id &&
        activeAfterSave?.change_description === evidence.change_description,
      `Saved org config is not active with the browser change description: ${JSON.stringify(
        {
          active_id: activeAfterSave?.id,
          saved_id: evidence.saved_org_config?.id,
          change_description: activeAfterSave?.change_description,
        },
      )}`,
    );
    evidence.active_org_config_after_save = {
      id: activeAfterSave.id,
      version: activeAfterSave.version,
      change_description: activeAfterSave.change_description,
    };
    await waitForVisibleText(page, `Version ${activeAfterSave.version}`, {
      exact: true,
    });
    await waitForVisibleText(page, evidence.change_description);
    await page.screenshot({ path: ORG_CONFIG_SCREENSHOT_PATH, fullPage: true });
    evidence.org_config_screenshot = ORG_CONFIG_SCREENSHOT_PATH;

    await clickVisibleText(page, "Batch Jobs", { exact: true });
    await waitForPath(page, "/dashboard/gateway/settings/batch-jobs");
    await waitForVisibleText(page, "Submit Batch Job", { exact: true });
    await clickVisibleText(page, "Submit Batch Job", { exact: true });
    await waitForVisibleText(page, "Submit Batch Job", { exact: true });
    await waitForVisibleText(
      page,
      "Requests (JSON array of chat completion request objects)",
    );

    const [submitBatchResponse] = await waitForResponsesDuring(
      page,
      "submit Gateway Settings batch job",
      [submitBatchResponseFor(evidence.gateway_id)],
      () => clickDialogButton(page, "Submit"),
    );
    evidence.batch_submit = await responseResult(submitBatchResponse);
    evidence.batch_id =
      evidence.batch_submit?.batch_id || evidence.batch_submit?.id || null;
    assert(
      evidence.batch_id,
      `Batch submit did not return a batch id: ${JSON.stringify(
        evidence.batch_submit,
      )}`,
    );
    await waitForTextGone(
      page,
      "Requests (JSON array of chat completion request objects)",
    );
    await waitForVisibleText(page, evidence.batch_id.slice(0, 12));

    const terminalBatch = await pollBatchTerminal(
      auth.client,
      evidence.gateway_id,
      evidence.batch_id,
    );
    evidence.batch_terminal = {
      batch_id: terminalBatch.batch_id,
      status: terminalBatch.status,
      total: terminalBatch.total,
      summary: terminalBatch.summary,
      result_statuses: asArray(terminalBatch.results).map(
        (item) => item.status,
      ),
    };
    assert(
      terminalBatch.status === "completed" &&
        terminalBatch.summary?.completed === 1,
      `Batch job did not complete successfully: ${JSON.stringify(
        evidence.batch_terminal,
      )}`,
    );
    await waitForVisibleText(page, "completed", {
      exact: true,
      timeout: 60000,
    });
    await clickRowAction(page, evidence.batch_id.slice(0, 12), 0);
    await waitForVisibleText(page, "Batch Details", { exact: true });
    for (const label of [
      "Total",
      "Completed",
      "Failed",
      "Total Cost",
      "Tokens",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    await waitForVisibleText(page, "success", { exact: true });
    await page.screenshot({ path: BATCH_SCREENSHOT_PATH, fullPage: true });
    evidence.batch_screenshot = BATCH_SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected Gateway Settings browser mutations: ${unexpectedMutations.join(
        "; ",
      )}`,
    );
    assert(
      browserMutations.length === 8,
      `Expected eight Gateway Settings browser mutations, saw ${browserMutations.length}: ${browserMutations.join(
        "; ",
      )}`,
    );
    evidence.browser_mutations = browserMutations;
  } catch (error) {
    caughtError = error;
    await page
      ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
  } finally {
    if (browser) await browser.close();
    try {
      evidence.email_alert_cleanup = await hardDeleteEmailAlertFixturesByPrefix(
        {
          organizationId: auth.organizationId,
        },
      );
    } catch (error) {
      cleanupError ||= error;
      evidence.email_alert_cleanup = { status: "failed", error: error.message };
    }
    if (cleanup) {
      try {
        evidence.cleanup = await cleanup();
      } catch (error) {
        cleanupError = error;
        evidence.cleanup = { status: "failed", error: error.message };
      }
    }
  }

  if (caughtError || cleanupError) {
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
          gateway_requests: gatewayRequests,
          browser_mutations: browserMutations,
          unexpected_mutations: unexpectedMutations,
          expected_api_failures: expectedApiFailures,
          failure_screenshot: FAILURE_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
    throw caughtError || cleanupError;
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
        gateway_request_count: gatewayRequests.length,
        browser_mutations: browserMutations,
        expected_api_failures: expectedApiFailures,
      },
      null,
      2,
    ),
  );
}

async function preflightSettings(client, baseline, runId) {
  const gateways = asArray(await client.get(apiPath("/agentcc/gateways/")));
  assert(gateways.length > 0, "Gateway list returned no configured gateway.");
  const gatewayId =
    gateways.find((gateway) => gateway.id === "default")?.id ||
    gateways[0].id ||
    "default";
  const suffix = String(runId || Date.now())
    .replace(/[^a-z0-9]/gi, "_")
    .toLowerCase();

  return {
    gateway_id: gatewayId,
    original_org_config_id: baseline.originalActiveConfig.id,
    original_org_config_version: baseline.originalActiveConfig.version,
    change_description: `Browser Settings mutation ${suffix}`,
  };
}

async function exerciseEmailAlertMutationFlow({ page, auth }) {
  const suffix = String(auth.runId || Date.now())
    .replace(/[^a-z0-9]/gi, "_")
    .toLowerCase();
  const alertName = `${EMAIL_ALERT_PREFIX}${suffix}`;
  const editedAlertName = `${alertName}_edited`;
  const recipient = `ui-email-alert-${suffix}@example.com`;
  const fromEmail = `ui-email-alert-from-${suffix}@example.com`;
  const editedFromEmail = `ui-email-alert-edited-${suffix}@example.com`;

  await clickVisibleText(page, "Add Alert", { exact: true });
  await waitForVisibleText(page, "New Email Alert", { exact: true });
  await typeIntoPlaceholder(page, "e.g. Production Error Alerts", alertName);
  await typeAutocompleteValue(page, "Type email and press Enter", recipient);
  await clickVisibleText(page, "Budget Exceeded", { exact: true });
  await clickVisibleText(page, "Error Occurred", { exact: true });
  await typeIntoPlaceholder(page, "alerts@yourdomain.com", fromEmail);
  await setInputByLabel(page, "Cooldown (minutes)", "7");

  const [createResponse] = await waitForResponsesDuring(
    page,
    "create Gateway Settings email alert",
    [emailAlertCreateResponse(), emailAlertsResponse()],
    () => clickDialogButton(page, "Create"),
  );
  const created = await responseResult(createResponse);
  assert(
    created?.id,
    `Email alert browser create did not return an id: ${JSON.stringify(
      created,
    )}`,
  );
  await waitForTextGone(page, "New Email Alert");
  await waitForVisibleText(page, alertName, { exact: true });
  await waitForVisibleText(page, recipient, { exact: true });
  await waitForVisibleText(page, "2 events", { exact: true });
  await waitForVisibleText(page, "sendgrid", { exact: true });

  const createdReadback = await auth.client.get(
    apiPath("/agentcc/email-alerts/{id}/", { id: created.id }),
  );
  assert(
    createdReadback?.name === alertName &&
      createdReadback?.provider === "sendgrid" &&
      createdReadback?.cooldown_minutes === 7 &&
      createdReadback?.provider_config?.from_email === fromEmail &&
      !createdReadback?.provider_config?.api_key,
    `Email alert API readback after browser create was unexpected: ${JSON.stringify(
      createdReadback,
    )}`,
  );
  const createdDbAudit = await loadAgentccEmailAlertDbAudit({
    alertId: created.id,
    organizationId: auth.organizationId,
    rawSecrets: [fromEmail],
  });
  assert(
    createdDbAudit?.id === created.id &&
      createdDbAudit?.organization_id === auth.organizationId &&
      createdDbAudit?.deleted === false &&
      createdDbAudit?.provider === "sendgrid" &&
      createdDbAudit?.is_active === true &&
      createdDbAudit?.cooldown_minutes === 7 &&
      Number(createdDbAudit?.encrypted_config_bytes) > 0 &&
      createdDbAudit?.raw_secret_present_in_ciphertext === false,
    `Email alert DB audit after create was unexpected: ${JSON.stringify(
      createdDbAudit,
    )}`,
  );
  await page.screenshot({
    path: EMAIL_ALERT_CREATE_SCREENSHOT_PATH,
    fullPage: true,
  });

  await clickRowAction(page, alertName, 0);
  await waitForVisibleText(page, "Edit Email Alert", { exact: true });
  await waitForVisibleValue(page, alertName);
  await waitForVisibleValue(page, fromEmail);

  const [testResponse] = await waitForResponsesDuring(
    page,
    "validation-only Gateway Settings email alert test",
    [emailAlertTestFailureResponse(created.id)],
    () => clickDialogButton(page, "Send Test"),
  );
  const testResult = await responseResult(testResponse);
  await waitForVisibleText(page, "SendGrid API key not configured");

  await setInputValueByCurrentValue(page, alertName, editedAlertName);
  await setInputValueByCurrentValue(page, fromEmail, editedFromEmail);
  await setInputByLabel(page, "Cooldown (minutes)", "11");
  await setCheckboxByLabel(page, "Alert is active", false);

  const [updateResponse] = await waitForResponsesDuring(
    page,
    "update Gateway Settings email alert",
    [emailAlertUpdateResponse(created.id), emailAlertsResponse()],
    () => clickDialogButton(page, "Update"),
  );
  const updated = await responseResult(updateResponse);
  assert(
    updated?.id === created.id &&
      updated?.name === editedAlertName &&
      updated?.cooldown_minutes === 11 &&
      updated?.is_active === false &&
      updated?.provider_config?.from_email === editedFromEmail,
    `Email alert browser update response was unexpected: ${JSON.stringify(
      updated,
    )}`,
  );
  await waitForTextGone(page, "Edit Email Alert");
  await waitForVisibleText(page, editedAlertName, { exact: true });
  await waitForVisibleText(page, recipient, { exact: true });

  const updatedDbAudit = await loadAgentccEmailAlertDbAudit({
    alertId: created.id,
    organizationId: auth.organizationId,
    rawSecrets: [editedFromEmail],
  });
  assert(
    updatedDbAudit?.deleted === false &&
      updatedDbAudit?.name === editedAlertName &&
      updatedDbAudit?.is_active === false &&
      updatedDbAudit?.cooldown_minutes === 11 &&
      updatedDbAudit?.raw_secret_present_in_ciphertext === false,
    `Email alert DB audit after update was unexpected: ${JSON.stringify(
      updatedDbAudit,
    )}`,
  );
  await page.screenshot({
    path: EMAIL_ALERT_EDIT_SCREENSHOT_PATH,
    fullPage: true,
  });

  const [deleteResponse] = await waitForResponsesDuring(
    page,
    "delete Gateway Settings email alert",
    [emailAlertDeleteResponse(created.id), emailAlertsResponse()],
    () => dispatchLastRowAction(page, editedAlertName),
  );
  const deleted = await responseResult(deleteResponse);
  assert(
    deleted?.deleted === true,
    `Email alert browser delete response was unexpected: ${JSON.stringify(
      deleted,
    )}`,
  );
  await waitForTextGone(page, editedAlertName);
  const afterDelete = asArray(
    await auth.client.get(apiPath("/agentcc/email-alerts/")),
  );
  assert(
    !afterDelete.some((alert) => alert.id === created.id),
    "Deleted email alert is still visible in API list.",
  );
  const deletedDbAudit = await loadAgentccEmailAlertDbAudit({
    alertId: created.id,
    organizationId: auth.organizationId,
    rawSecrets: [editedFromEmail],
  });
  assert(
    deletedDbAudit?.deleted === true && deletedDbAudit?.deleted_at_set === true,
    `Email alert DB audit after delete was unexpected: ${JSON.stringify(
      deletedDbAudit,
    )}`,
  );
  await page.screenshot({
    path: EMAIL_ALERT_DELETE_SCREENSHOT_PATH,
    fullPage: true,
  });

  return {
    alert_id: created.id,
    alert_name: alertName,
    edited_alert_name: editedAlertName,
    recipient,
    provider: "sendgrid",
    test_status: testResponse.status(),
    test_result: testResult,
    created_db_audit: createdDbAudit,
    updated_db_audit: updatedDbAudit,
    deleted_db_audit: deletedDbAudit,
    screenshots: [
      EMAIL_ALERT_CREATE_SCREENSHOT_PATH,
      EMAIL_ALERT_EDIT_SCREENSHOT_PATH,
      EMAIL_ALERT_DELETE_SCREENSHOT_PATH,
    ],
  };
}

async function prepareOrgConfigRestorer(client) {
  const originalActiveConfig = await client.get(
    apiPath("/agentcc/org-configs/active/"),
  );
  assert(
    originalActiveConfig?.id && originalActiveConfig?.is_active === true,
    "AgentCC active org config endpoint did not return an active baseline.",
  );
  const beforeConfigIds = new Set(
    collectionRows(await client.get(apiPath("/agentcc/org-configs/")))
      .map((config) => config?.id)
      .filter(Boolean),
  );

  return {
    originalActiveConfig,
    beforeConfigIds,
    cleanup: createOrgConfigRestorer({
      client,
      beforeConfigIds,
      originalActiveConfigId: originalActiveConfig.id,
    }),
  };
}

function collectionRows(value) {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.results)) return value.results;
  if (Array.isArray(value?.data)) return value.data;
  return asArray(value);
}

function createOrgConfigRestorer({
  client,
  beforeConfigIds,
  originalActiveConfigId,
}) {
  let completed = false;

  return async () => {
    if (completed) return { status: "already-cleaned" };
    const restoreEvidence = {
      status: "passed",
      original_config_id: originalActiveConfigId,
      activated_original: false,
      deleted_config_ids: [],
      deleted_config_versions: [],
    };

    const activeConfig = await client.get(
      apiPath("/agentcc/org-configs/active/"),
    );
    if (activeConfig?.id !== originalActiveConfigId) {
      await client.post(
        apiPath("/agentcc/org-configs/{id}/activate/", {
          id: originalActiveConfigId,
        }),
        {},
      );
      restoreEvidence.activated_original = true;
    }

    const configs = collectionRows(
      await client.get(apiPath("/agentcc/org-configs/")),
    );
    const disposableConfigs = configs.filter(
      (config) =>
        config?.id &&
        config.id !== originalActiveConfigId &&
        !beforeConfigIds.has(config.id),
    );

    for (const config of disposableConfigs) {
      await ignoreNotFound(() =>
        client.delete(apiPath("/agentcc/org-configs/{id}/", { id: config.id })),
      );
      restoreEvidence.deleted_config_ids.push(config.id);
      restoreEvidence.deleted_config_versions.push(config.version);
    }

    const restoredActive = await client.get(
      apiPath("/agentcc/org-configs/active/"),
    );
    assert(
      restoredActive?.id === originalActiveConfigId,
      "AgentCC org config cleanup did not restore the original active config.",
    );

    completed = true;
    return restoreEvidence;
  };
}

async function ignoreNotFound(fn) {
  try {
    return await fn();
  } catch (error) {
    const message = String(error?.message || "").toLowerCase();
    if (
      error?.status === 404 ||
      message.includes("not found") ||
      message.includes("does not exist")
    ) {
      return null;
    }
    throw error;
  }
}

async function pollBatchTerminal(client, gatewayId, batchId) {
  let lastBatch = null;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const batch = await client.get(
      apiPath("/agentcc/gateways/{id}/get-batch/", { id: gatewayId }),
      { query: { batch_id: batchId } },
    );
    lastBatch = batch;
    if (["completed", "cancelled", "failed"].includes(batch?.status)) {
      return batch;
    }
    await sleep(1000);
  }
  throw new Error(
    `Batch ${batchId} did not reach a terminal status: ${JSON.stringify(
      lastBatch,
    )}`,
  );
}

async function loadAgentccEmailAlertDbAudit({
  alertId,
  organizationId,
  rawSecrets,
}) {
  const rawSecretChecks = asArray(rawSecrets)
    .map(
      (secret) =>
        `COALESCE(position(${sqlString(secret)} in encode(encrypted_config, 'escape')) > 0, false)`,
    )
    .join(" OR ");
  const sql = `
SELECT COALESCE((
  SELECT json_build_object(
    'id', id::text,
    'organization_id', organization_id::text,
    'name', name,
    'provider', provider,
    'recipients', recipients,
    'events', events,
    'thresholds', thresholds,
    'is_active', is_active,
    'cooldown_minutes', cooldown_minutes,
    'encrypted_config_bytes', octet_length(encrypted_config),
    'raw_secret_present_in_ciphertext', ${rawSecretChecks || "false"},
    'deleted', deleted,
    'deleted_at_set', deleted_at IS NOT NULL
  )
  FROM agentcc_email_alert
  WHERE id = ${sqlUuid(alertId)}
    AND organization_id = ${sqlUuid(organizationId)}
), '{}'::json);
`;
  return runPostgresJson(sql);
}

async function hardDeleteEmailAlertFixturesByPrefix({ organizationId }) {
  const deleteAudit = await runPostgresJson(`
WITH deleted_alerts AS (
  DELETE FROM agentcc_email_alert
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${EMAIL_ALERT_PREFIX}%`)}
  RETURNING id
)
SELECT json_build_object(
  'deleted_alert_count', (SELECT count(*) FROM deleted_alerts)
);
`);
  const remainingAudit = await runPostgresJson(`
SELECT json_build_object(
  'remaining_alert_count', count(*)
)
FROM agentcc_email_alert
WHERE organization_id = ${sqlUuid(organizationId)}
  AND name LIKE ${sqlString(`${EMAIL_ALERT_PREFIX}%`)};
`);
  return {
    status: "passed",
    deleted_alert_count: Number(deleteAudit.deleted_alert_count || 0),
    remaining_alert_count: Number(remainingAudit.remaining_alert_count || 0),
  };
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
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
    window.normalizeText = (value) => String(value || "").trim();
    window.dispatchClick = (element) => {
      element.click();
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

async function waitForResponsesDuring(page, label, predicates, action) {
  try {
    return await Promise.all([
      ...predicates.map((predicate) =>
        page.waitForResponse(predicate, { timeout: 60000 }),
      ),
      action(),
    ]);
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
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

async function waitForTextGone(page, text, { timeout = 30000 } = {}) {
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

async function waitForVisibleValue(page, value, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (expectedValue) =>
      window.visibleElements().some((element) => {
        if (
          element instanceof HTMLInputElement &&
          element.value === expectedValue
        ) {
          return true;
        }

        const input = element.querySelector?.("input");
        return input?.value === expectedValue;
      }),
    { timeout },
    value,
  );
}

async function typeIntoPlaceholder(page, placeholder, value) {
  await page.waitForFunction(
    (expectedPlaceholder) =>
      window
        .visibleElements("input,textarea")
        .some((element) => element.placeholder === expectedPlaceholder),
    { timeout: 30000 },
    placeholder,
  );
  const typed = await page.evaluate(
    ({ expectedPlaceholder, text }) => {
      const element = window
        .visibleElements("input,textarea")
        .find((input) => input.placeholder === expectedPlaceholder);
      if (!element || element.disabled) return false;
      element.focus();
      window.setNativeValue(element, text);
      return true;
    },
    { expectedPlaceholder: placeholder, text: value },
  );
  assert(typed, `Could not type into placeholder: ${placeholder}`);
}

async function clickVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await waitForVisibleText(page, text, { exact, timeout });
  const point = await page.evaluate(
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
        elements[0];
      const clickable =
        element?.closest("button,a,[role='button'],[role='menuitem'],tr") ||
        element;
      if (!clickable || clickable.disabled) return false;
      clickable.scrollIntoView({ block: "center", inline: "center" });
      const rect = clickable.getBoundingClientRect();
      return {
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
      };
    },
    { text, exact },
  );
  assert(point, `Could not click visible text: ${text}`);
  await page.mouse.click(point.x, point.y);
}

async function clickDialogButton(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const point = await page.evaluate((expectedLabel) => {
    const dialog = window.visibleElements("[role='dialog']").at(-1);
    if (!dialog) return false;
    const button = Array.from(dialog.querySelectorAll("button")).find(
      (candidate) =>
        window.normalizeText(candidate.textContent) === expectedLabel &&
        !candidate.disabled,
    );
    if (!button) return false;
    const rect = button.getBoundingClientRect();
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
  }, label);
  assert(point, `Could not click dialog button: ${label}`);
  await page.mouse.click(point.x, point.y);
}

async function clickRowAction(page, rowText, actionIndex) {
  await waitForVisibleText(page, rowText);
  const point = await page.evaluate(
    ({ expectedText, index }) => {
      const row = window
        .visibleElements("tr")
        .find((candidate) =>
          window.normalizeText(candidate.textContent).includes(expectedText),
        );
      if (!row) return false;
      const buttons = Array.from(row.querySelectorAll("button")).filter(
        (button) => !button.disabled,
      );
      const button = buttons[index];
      if (!button) return false;
      button.scrollIntoView({ block: "center", inline: "center" });
      const rect = button.getBoundingClientRect();
      return {
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
      };
    },
    { expectedText: rowText, index: actionIndex },
  );
  assert(point, `Could not click row action ${actionIndex} for ${rowText}`);
  await page.mouse.click(point.x, point.y);
}

async function dispatchLastRowAction(page, rowText) {
  await waitForVisibleText(page, rowText);
  const clicked = await page.evaluate((expectedText) => {
    const row = window
      .visibleElements("tr")
      .find((candidate) =>
        window.normalizeText(candidate.textContent).includes(expectedText),
      );
    if (!row) return false;
    const buttons = Array.from(row.querySelectorAll("button")).filter(
      (button) => !button.disabled,
    );
    const button = buttons.at(-1);
    if (!button) return false;
    button.scrollIntoView({ block: "center", inline: "center" });
    window.dispatchClick(button);
    return true;
  }, rowText);
  assert(clicked, `Could not dispatch last row action for ${rowText}`);
}

async function typeAutocompleteValue(page, placeholder, value) {
  const point = await page.evaluate((expectedPlaceholder) => {
    const input = window
      .visibleElements("input")
      .find((element) => element.placeholder === expectedPlaceholder);
    if (!input || input.disabled) return false;
    const rect = input.getBoundingClientRect();
    return {
      x: rect.left + Math.min(rect.width / 2, 80),
      y: rect.top + rect.height / 2,
    };
  }, placeholder);
  assert(point, `Could not focus autocomplete placeholder: ${placeholder}`);
  await page.mouse.click(point.x, point.y);
  await page.keyboard.type(value);
  await page.keyboard.press("Enter");
  await waitForVisibleText(page, value, { exact: true });
}

async function setInputByLabel(page, label, value) {
  const updated = await page.evaluate(
    ({ expectedLabel, text }) => {
      const labels = window.visibleElements("label").filter((element) => {
        const textContent = window.normalizeText(element.textContent);
        return textContent === expectedLabel;
      });
      for (const labelElement of labels) {
        const input = labelElement.htmlFor
          ? document.getElementById(labelElement.htmlFor)
          : labelElement
              .closest(".MuiFormControl-root")
              ?.querySelector("input,textarea");
        if (!input || input.disabled) continue;
        input.focus();
        window.setNativeValue(input, text);
        return true;
      }
      return false;
    },
    { expectedLabel: label, text: value },
  );
  assert(updated, `Could not set input by label: ${label}`);
}

async function setInputValueByCurrentValue(page, currentValue, nextValue) {
  const updated = await page.evaluate(
    ({ expectedValue, text }) => {
      const input = window
        .visibleElements("input,textarea")
        .find((element) => element.value === expectedValue);
      if (!input || input.disabled) return false;
      input.focus();
      window.setNativeValue(input, text);
      return true;
    },
    { expectedValue: currentValue, text: nextValue },
  );
  assert(updated, `Could not update input with value: ${currentValue}`);
}

async function setCheckboxByLabel(page, label, checked) {
  const updated = await page.evaluate(
    ({ expectedLabel, expectedChecked }) => {
      const labelElement = window
        .visibleElements("label")
        .find(
          (element) =>
            window.normalizeText(element.textContent) === expectedLabel,
        );
      const checkbox = labelElement?.querySelector("input[type='checkbox']");
      if (!checkbox || checkbox.disabled) return false;
      if (checkbox.checked !== expectedChecked) {
        checkbox.click();
      }
      return true;
    },
    { expectedLabel: label, expectedChecked: checked },
  );
  assert(updated, `Could not set checkbox by label: ${label}`);
}

async function responseResult(response) {
  const data = await response.json();
  return data?.result ?? data;
}

function gatewayListResponse() {
  return (response) =>
    response.url().includes("/agentcc/gateways/") &&
    !response.url().includes("/config/") &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function gatewayConfigResponse(gatewayId = "") {
  return (response) =>
    response.url().includes("/agentcc/gateways/") &&
    response.url().includes("/config/") &&
    (!gatewayId ||
      response.url().includes(`/agentcc/gateways/${gatewayId}/`)) &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function emailAlertsResponse() {
  return (response) =>
    response.url().includes("/agentcc/email-alerts/") &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function healthCheckResponse(gatewayId) {
  return (response) =>
    response.url().includes(`/agentcc/gateways/${gatewayId}/health_check/`) &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function reloadConfigResponse(gatewayId) {
  return (response) =>
    response.url().includes(`/agentcc/gateways/${gatewayId}/reload/`) &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function orgConfigCreateResponse() {
  return (response) =>
    response.url().includes("/agentcc/org-configs/") &&
    !response.url().includes("/active/") &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function orgConfigActiveResponse() {
  return (response) =>
    response.url().includes("/agentcc/org-configs/active/") &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function submitBatchResponseFor(gatewayId) {
  return (response) =>
    response.url().includes(`/agentcc/gateways/${gatewayId}/submit-batch/`) &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function emailAlertCreateResponse() {
  return (response) =>
    response.url().endsWith("/agentcc/email-alerts/") &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function emailAlertUpdateResponse(alertId) {
  return (response) =>
    response.url().includes(`/agentcc/email-alerts/${alertId}/`) &&
    response.request().method() === "PATCH" &&
    response.status() < 400;
}

function emailAlertDeleteResponse(alertId) {
  return (response) =>
    response.url().includes(`/agentcc/email-alerts/${alertId}/`) &&
    response.request().method() === "DELETE" &&
    response.status() < 400;
}

function emailAlertTestFailureResponse(alertId) {
  return (response) =>
    response.url().includes(`/agentcc/email-alerts/${alertId}/test/`) &&
    response.request().method() === "POST" &&
    response.status() === 400;
}

function isGatewayApiUrl(url) {
  return url.includes("/agentcc/");
}

function isExpectedSettingsFailure(response) {
  return (
    response.url().includes("/agentcc/email-alerts/") &&
    response.url().includes("/test/") &&
    response.request().method() === "POST" &&
    response.status() === 400
  );
}

function isAllowedSettingsMutation(method, url) {
  return (
    (method === "POST" &&
      (url.includes("/health_check/") ||
        url.includes("/reload/") ||
        (url.includes("/agentcc/org-configs/") &&
          !url.includes("/activate/")) ||
        url.includes("/submit-batch/") ||
        url.includes("/agentcc/email-alerts/"))) ||
    (url.includes("/agentcc/email-alerts/") &&
      (method === "PATCH" || method === "DELETE"))
  );
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const { stdout } = await execFile(
    "docker",
    ["exec", container, "psql", "-qAt", "-U", "user", "-d", "tfc", "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const output = stdout.trim();
  return output ? JSON.parse(output) : {};
}

function sqlUuid(value) {
  assert(value && /^[0-9a-f-]{36}$/i.test(value), `Invalid UUID: ${value}`);
  return `'${value}'::uuid`;
}

function sqlString(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
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
