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
  currentUserEmail,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/alerts-lifecycle-mutation-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/alerts-lifecycle-mutation-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const project = await selectObserveProject(auth);
  const alertName = `browser alert lifecycle ${auth.runId}`;
  const editedAlertName = `${alertName} edited`;
  const notificationEmail =
    currentUserEmail(auth.user) || "api-journey-alert@example.test";
  let alertId = "";
  let cleanupDone = false;

  const evidence = {
    project_id: project.id,
    project_name: project.name,
    alert_name: alertName,
    edited_alert_name: editedAlertName,
  };
  const apiFailures = [];
  const pageErrors = [];
  const mutations = [];

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

  page.on("request", (request) => {
    const url = request.url();
    if (
      isAlertApiUrl(url) &&
      ["POST", "PATCH", "DELETE"].includes(request.method())
    ) {
      mutations.push(`${request.method()} ${new URL(url).pathname}`);
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (isAlertApiUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) =>
    pageErrors.push(error.stack || error.message),
  );

  try {
    await waitForResponseDuring(
      page,
      "initial Alerts list",
      (response) =>
        response.url().includes("/tracer/user-alerts/list_monitors/") &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/alerts`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/alerts",
      { timeout: 30000 },
    );
    await expectVisibleText(page, "Alerts", { exact: true });
    await clickDataAttribute(page, "data-alert-action", "new");
    await expectVisibleText(page, "Choose a project", { exact: true });
    await selectSearchFieldOption(page, "project", project.name);
    await clickDataAttribute(page, "data-alert-project-action", "next");
    await waitForDialogToClose(page, "Choose a project");

    await expectVisibleText(page, "Create Alerts", { exact: true });
    evidence.alert_type = await waitForSelectedAlertType(page);
    await clickDataAttribute(page, "data-alert-type-action", "next");
    await expectVisibleText(page, "Manage alert settings", { exact: true });

    await fillDataField(page, "name", alertName);
    await fillDataField(page, "critical-threshold-value", "999999");
    await fillDataField(page, "warning-threshold-value", "999998");
    await fillEmailChip(page, notificationEmail);

    await installAlertSubmitProbe(page);
    await waitForResponseDuring(
      page,
      "create alert mutation",
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/tracer/user-alerts/" &&
        response.status() < 400,
      () => clickDataAttribute(page, "data-alert-form-submit", "create"),
      { diagnostics: () => collectAlertFormDiagnostics(page) },
    );

    const created = await waitForAlertByName(auth, alertName);
    alertId = created.id;
    evidence.created_alert_id = alertId;
    const createdAudit = await loadAlertAudit(alertId);
    evidence.created_audit = createdAudit;
    assert(
      createdAudit.alert_exists === true &&
        createdAudit.project_id === project.id &&
        createdAudit.workspace_id === auth.workspaceId,
      "Created alert DB audit did not match the selected project/workspace.",
    );

    await typeSearch(page, alertName);
    await waitForAlertRow(page, alertName);
    await waitForResponsesDuring(
      page,
      "created alert detail drawer",
      [
        (response) =>
          response.url().includes(`/tracer/user-alerts/${alertId}/details/`) &&
          response.status() < 400,
        (response) =>
          response.url().includes(`/tracer/user-alerts/${alertId}/graph/`) &&
          response.status() < 400,
      ],
      () => clickAlertRow(page, alertName),
    );
    await expectVisibleText(page, "Alert Rule Details", { exact: true });
    await expectVisibleText(page, alertName, { exact: true });

    await installClickProbe(page, '[data-alert-sheet-action="edit"]', "edit");
    await clickDataAttribute(page, "data-alert-sheet-action", "edit");
    await expectClickProbe(page, "edit");
    await expectEditDrawer(page);
    await fillDataField(page, "name", editedAlertName);
    await fillDataField(page, "critical-threshold-value", "888888");
    await fillDataField(page, "warning-threshold-value", "888887");
    await waitForSnackbarsToClear(page);
    await installAlertSubmitProbe(page);
    await waitForResponseDuring(
      page,
      "update alert mutation",
      (response) =>
        response.request().method() === "PATCH" &&
        new URL(response.url()).pathname ===
          `/tracer/user-alerts/${alertId}/` &&
        response.status() < 400,
      () => clickDataAttribute(page, "data-alert-form-submit", "update"),
      { diagnostics: () => collectAlertFormDiagnostics(page) },
    );

    const edited = await waitForAlertByName(auth, editedAlertName);
    assert(edited.id === alertId, "Edited alert list row id changed.");
    const editedAudit = await loadAlertAudit(alertId);
    evidence.edited_audit = editedAudit;
    assert(
      editedAudit.name === editedAlertName &&
        Number(editedAudit.critical_threshold_value) === 888888,
      "Edited alert DB audit did not persist name and threshold changes.",
    );

    await waitForSnackbarsToClear(page);
    await clickDataAttribute(page, "data-alert-sheet-action", "close");
    await typeSearch(page, editedAlertName);
    await waitForAlertRow(page, editedAlertName);
    await selectAlertRow(page, editedAlertName);
    await expectVisibleText(page, "1 Selected", { exact: true });
    await clickVisibleText(page, "Delete", { exact: true });
    await waitForResponseDuring(
      page,
      "delete alert mutation",
      (response) =>
        response.request().method() === "DELETE" &&
        new URL(response.url()).pathname === "/tracer/user-alerts/" &&
        response.status() < 400,
      () => clickDataAttribute(page, "data-alert-confirm-action", "delete"),
    );
    await waitForAlertRowToDisappear(page, editedAlertName);
    assert(
      !(await findAlertByName(auth, editedAlertName)),
      "Deleted alert remained visible in list_monitors.",
    );
    const deletedAudit = await loadAlertAudit(alertId);
    evidence.deleted_audit = deletedAudit;
    assert(
      deletedAudit.alert_deleted === true,
      "Alert delete did not persist.",
    );

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;
    evidence.mutations = mutations;

    const cleanup = await cleanupAlertArtifacts(alertId);
    cleanupDone = true;
    evidence.cleanup = cleanup;
    assert(
      Number(cleanup.remaining_alert_count) === 0 &&
        Number(cleanup.remaining_log_count) === 0,
      `Alert lifecycle cleanup left disposable rows: ${JSON.stringify(cleanup)}`,
    );
    assert(
      apiFailures.length === 0,
      `Alert lifecycle API failures: ${apiFailures.join("; ")}`,
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
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await page
      .screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
    throw error;
  } finally {
    await browser.close();
    if (alertId && !cleanupDone) {
      await cleanupAlertArtifacts(alertId).catch((error) => {
        console.error(`Cleanup failed: ${error.message}`);
      });
    }
  }
}

async function selectObserveProject(auth) {
  const response = await auth.client.get(
    apiPath("/tracer/project/list_project_ids/"),
    {
      query: { project_type: "observe" },
    },
  );
  const projects = asArray(response.projects || response);
  const project = projects.find((row) => isUuid(row?.id) && row?.name);
  assert(
    project,
    "No Observe project was available for alert lifecycle smoke.",
  );
  return project;
}

async function findAlertByName(auth, name) {
  const response = await auth.client.get(
    apiPath("/tracer/user-alerts/list_monitors/"),
    {
      query: {
        page_number: 0,
        page_size: 25,
        search_text: name,
      },
    },
  );
  return (
    asArray(response.table || response).find((row) => row?.name === name) ||
    null
  );
}

async function waitForAlertByName(auth, name) {
  const started = Date.now();
  while (Date.now() - started < 60000) {
    const alert = await findAlertByName(auth, name);
    if (alert?.id) return alert;
    await sleep(1000);
  }
  throw new Error(`Timed out waiting for alert list row ${name}.`);
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

async function waitForResponseDuring(
  page,
  label,
  predicate,
  action,
  { diagnostics } = {},
) {
  try {
    await Promise.all([
      page.waitForResponse(predicate, { timeout: 60000 }),
      action(),
    ]);
  } catch (error) {
    const diagnosticDetails = diagnostics
      ? await diagnostics().catch((diagnosticError) => ({
          diagnostics_error: diagnosticError.message,
        }))
      : null;
    const suffix = diagnosticDetails
      ? `\nDiagnostics: ${JSON.stringify(diagnosticDetails, null, 2)}`
      : "";
    throw new Error(`${label} failed: ${error.message}${suffix}`);
  }
}

async function waitForResponsesDuring(page, label, predicates, action) {
  try {
    await Promise.all([
      ...predicates.map((predicate) =>
        page.waitForResponse(predicate, { timeout: 60000 }),
      ),
      action(),
    ]);
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
}

async function clickDataAttribute(
  page,
  attribute,
  value,
  { domClick = false } = {},
) {
  const selector = `[${attribute}="${value}"]`;
  await page.waitForFunction(
    (targetSelector) => {
      const element = document.querySelector(targetSelector);
      if (!element) return false;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0 &&
        !element.disabled &&
        element.getAttribute("aria-disabled") !== "true"
      );
    },
    { timeout: 30000 },
    selector,
  );
  await page.$eval(selector, (element) =>
    element.scrollIntoView({ block: "center", inline: "center" }),
  );
  if (domClick) {
    await page.$eval(selector, (element) => element.click());
    return;
  }
  await page.click(selector);
}

async function installAlertSubmitProbe(page) {
  await page.evaluate(() => {
    window.__alertSubmitProbe = [];
    const record = (event) => {
      window.__alertSubmitProbe.push({
        type: event.type,
        target: event.target?.tagName?.toLowerCase() || "",
        submitter: event.submitter?.textContent?.trim() || "",
        default_prevented: event.defaultPrevented,
        time: Date.now(),
      });
    };
    document
      .querySelector('[data-alert-form-submit="create"]')
      ?.addEventListener("click", record);
    document
      .querySelector('[data-alert-form-submit="update"]')
      ?.addEventListener("click", record);
    document
      .querySelector('[data-alert-form-submit="create"]')
      ?.closest("form")
      ?.addEventListener("submit", record);
    document
      .querySelector('[data-alert-form-submit="update"]')
      ?.closest("form")
      ?.addEventListener("submit", record);
  });
}

async function installClickProbe(page, selector, name) {
  await page.evaluate(
    ({ selector: targetSelector, name: probeName }) => {
      window.__alertClickProbes ||= {};
      window.__alertClickProbes[probeName] = {
        clicks: 0,
        selector: targetSelector,
      };
      document.querySelector(targetSelector)?.addEventListener("click", () => {
        window.__alertClickProbes[probeName].clicks += 1;
        window.__alertClickProbes[probeName].time = Date.now();
      });
    },
    { selector, name },
  );
}

async function expectClickProbe(page, name) {
  try {
    await page.waitForFunction(
      (probeName) => window.__alertClickProbes?.[probeName]?.clicks > 0,
      { timeout: 5000 },
      name,
    );
  } catch (error) {
    const probe = await page.evaluate(
      (probeName) => window.__alertClickProbes?.[probeName] || null,
      name,
    );
    throw new Error(
      `Expected ${name} click probe to fire: ${JSON.stringify(probe)}`,
    );
  }
}

async function expectEditDrawer(page) {
  try {
    await expectVisibleText(page, "Edit Alerts", { exact: true });
  } catch (error) {
    const diagnostics = await collectEditDrawerDiagnostics(page).catch(
      (diagnosticError) => ({
        diagnostics_error: diagnosticError.message,
      }),
    );
    throw new Error(
      `Edit drawer did not open: ${error.message}\nDiagnostics: ${JSON.stringify(
        diagnostics,
        null,
        2,
      )}`,
    );
  }
}

async function collectEditDrawerDiagnostics(page) {
  return page.evaluate(() => {
    const isVisible = (element) => {
      if (!element) return false;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    };
    const describeElement = (element) => {
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return {
        tag: element.tagName.toLowerCase(),
        text: String(element.textContent || "").trim(),
        disabled: Boolean(element.disabled),
        aria_disabled: element.getAttribute("aria-disabled"),
        visible: isVisible(element),
        rect: {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        },
      };
    };

    return {
      path: window.location.pathname,
      edit_button: describeElement(
        document.querySelector('[data-alert-sheet-action="edit"]'),
      ),
      drawer_headings: Array.from(
        document.querySelectorAll(
          '[role="presentation"] h6, [role="dialog"] h6',
        ),
      )
        .filter(isVisible)
        .map((element) => String(element.textContent || "").trim())
        .filter(Boolean),
      visible_buttons: Array.from(document.querySelectorAll("button"))
        .filter(isVisible)
        .map((element) => String(element.textContent || "").trim())
        .filter(Boolean),
      click_probes: window.__alertClickProbes || {},
      body_has_edit_alerts: document.body.innerText.includes("Edit Alerts"),
      body_has_alert_rule_details:
        document.body.innerText.includes("Alert Rule Details"),
    };
  });
}

async function collectAlertFormDiagnostics(page) {
  return page.evaluate(() => {
    const isVisible = (element) => {
      if (!element) return false;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    };
    const describeElement = (element) => {
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return {
        tag: element.tagName.toLowerCase(),
        text: String(element.textContent || "")
          .trim()
          .slice(0, 200),
        value: "value" in element ? element.value : undefined,
        disabled: Boolean(element.disabled),
        aria_disabled: element.getAttribute("aria-disabled"),
        aria_invalid: element.getAttribute("aria-invalid"),
        visible: isVisible(element),
        rect: {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        },
      };
    };
    const fields = Array.from(
      document.querySelectorAll("[data-alert-field]"),
    ).map((element) => ({
      field: element.getAttribute("data-alert-field"),
      ...describeElement(element),
    }));
    const helpers = Array.from(
      document.querySelectorAll(
        ".MuiFormHelperText-root, [role='alert'], .notistack-Snackbar",
      ),
    )
      .filter(isVisible)
      .map((element) => String(element.textContent || "").trim())
      .filter(Boolean);
    const chips = Array.from(document.querySelectorAll(".MuiChip-label"))
      .filter(isVisible)
      .map((element) => String(element.textContent || "").trim())
      .filter(Boolean);

    return {
      path: window.location.pathname,
      submit_create: describeElement(
        document.querySelector('[data-alert-form-submit="create"]'),
      ),
      submit_create_form_present: Boolean(
        document
          .querySelector('[data-alert-form-submit="create"]')
          ?.closest("form"),
      ),
      submit_update: describeElement(
        document.querySelector('[data-alert-form-submit="update"]'),
      ),
      submit_update_form_present: Boolean(
        document
          .querySelector('[data-alert-form-submit="update"]')
          ?.closest("form"),
      ),
      submit_events: window.__alertSubmitProbe || [],
      active_element: describeElement(document.activeElement),
      fields,
      helper_texts: helpers,
      chips,
      viewport: {
        width: window.innerWidth,
        height: window.innerHeight,
        scroll_x: window.scrollX,
        scroll_y: window.scrollY,
      },
    };
  });
}

async function fillDataField(page, field, value) {
  const selector = `input[data-alert-field="${field}"]`;
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector, { clickCount: 3 });
  await page.keyboard.down(modifierKey());
  await page.keyboard.press("A");
  await page.keyboard.up(modifierKey());
  await page.keyboard.press("Backspace");
  await page.type(selector, String(value));
}

async function selectSearchFieldOption(page, field, label) {
  const selector = `input[data-alert-field="${field}"]`;
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector, { clickCount: 3 });
  await page.keyboard.down(modifierKey());
  await page.keyboard.press("A");
  await page.keyboard.up(modifierKey());
  await page.keyboard.press("Backspace");
  await page.type(selector, String(label));
  await page.waitForFunction(
    (expectedLabel) => {
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
      return Array.from(
        document.querySelectorAll(
          '.MuiPopover-root [role="menuitem"], .MuiPopover-root .MuiMenuItem-root',
        ),
      ).some(
        (element) =>
          isVisible(element) &&
          String(element.textContent || "").trim() === expectedLabel,
      );
    },
    { timeout: 30000 },
    String(label),
  );
  await page.evaluate((expectedLabel) => {
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
    const option = Array.from(
      document.querySelectorAll(
        '.MuiPopover-root [role="menuitem"], .MuiPopover-root .MuiMenuItem-root',
      ),
    ).find(
      (element) =>
        isVisible(element) &&
        String(element.textContent || "").trim() === expectedLabel,
    );
    option?.click();
  }, String(label));
  await page.waitForFunction(
    (targetSelector) => {
      const element = document.querySelector(targetSelector);
      return element && !document.querySelector(".MuiPopover-root");
    },
    { timeout: 30000 },
    selector,
  );
}

async function waitForSelectedAlertType(page) {
  await page.waitForFunction(
    () => Boolean(document.querySelector('input[type="radio"]:checked')?.value),
    { timeout: 30000 },
  );
  const selectedAlertType = await page.evaluate(
    () => document.querySelector('input[type="radio"]:checked')?.value || "",
  );
  assert(selectedAlertType, "No alert type was selected in the create drawer.");
  return selectedAlertType;
}

async function waitForDialogToClose(page, text) {
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
      return !Array.from(document.querySelectorAll('[role="dialog"]')).some(
        (element) =>
          isVisible(element) &&
          String(element.textContent || "").includes(expectedText),
      );
    },
    { timeout: 30000 },
    text,
  );
}

async function waitForSnackbarsToClear(page) {
  await page.mouse.move(20, 20);
  await page.waitForFunction(
    () => {
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
      return !Array.from(document.querySelectorAll(".notistack-Snackbar")).some(
        isVisible,
      );
    },
    { timeout: 12000 },
  );
}

async function fillEmailChip(page, email) {
  const selector = 'input[placeholder^="Separate emails"]';
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector);
  await page.type(selector, email);
  await page.keyboard.press("Enter");
  await expectVisibleText(page, email, { exact: true });
}

async function typeSearch(page, value) {
  await page.waitForSelector('input[placeholder="Search"]', { timeout: 30000 });
  await page.click('input[placeholder="Search"]');
  await page.keyboard.down(modifierKey());
  await page.keyboard.press("A");
  await page.keyboard.up(modifierKey());
  await page.keyboard.press("Backspace");
  await page.type('input[placeholder="Search"]', value);
}

async function clickVisibleText(page, text, { exact = false } = {}) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
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
        const textContent = String(element.textContent || "").trim();
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
    },
    { timeout: 30000 },
    { text, exact },
  );
  await page.evaluate(
    ({ text: expectedText, exact: exactMatch }) => {
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
      const element = Array.from(document.querySelectorAll("body *")).find(
        (candidate) => {
          if (!isVisible(candidate)) return false;
          const textContent = String(candidate.textContent || "").trim();
          return exactMatch
            ? textContent === expectedText
            : textContent.includes(expectedText);
        },
      );
      element?.click();
    },
    { text, exact },
  );
}

async function waitForAlertRow(page, name) {
  await page.waitForFunction(
    (expectedName) => {
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
      return Array.from(
        document.querySelectorAll("[data-alert-row-name]"),
      ).some(
        (element) =>
          isVisible(element) &&
          element.getAttribute("data-alert-row-name") === expectedName,
      );
    },
    { timeout: 30000 },
    name,
  );
}

async function waitForAlertRowToDisappear(page, name) {
  await page.waitForFunction(
    (expectedName) => {
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
      return !Array.from(
        document.querySelectorAll("[data-alert-row-name]"),
      ).some(
        (element) =>
          isVisible(element) &&
          element.getAttribute("data-alert-row-name") === expectedName,
      );
    },
    { timeout: 30000 },
    name,
  );
}

async function clickAlertRow(page, name) {
  await waitForAlertRow(page, name);
  await page.evaluate((expectedName) => {
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
    const element = Array.from(
      document.querySelectorAll("[data-alert-row-name]"),
    ).find(
      (candidate) =>
        isVisible(candidate) &&
        candidate.getAttribute("data-alert-row-name") === expectedName,
    );
    element.closest('[role="row"]')?.click();
  }, name);
}

async function selectAlertRow(page, name) {
  await waitForAlertRow(page, name);
  await page.evaluate((expectedName) => {
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
    const element = Array.from(
      document.querySelectorAll("[data-alert-row-name]"),
    ).find(
      (candidate) =>
        isVisible(candidate) &&
        candidate.getAttribute("data-alert-row-name") === expectedName,
    );
    const row = element.closest('[role="row"]');
    const checkbox = row?.querySelector('input[type="checkbox"]');
    checkbox?.click();
  }, name);
}

async function expectVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
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
        const textContent = String(element.textContent || "").trim();
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function loadAlertAudit(alertId) {
  const rows = await runPsqlJson(`
WITH requested AS (
  SELECT ${sqlUuid(alertId)} AS alert_id
),
alert_row AS (
  SELECT *
  FROM tracer_useralertmonitor monitor
  JOIN requested r ON monitor.id = r.alert_id
),
log_rows AS (
  SELECT *
  FROM tracer_useralertmonitorlog log
  JOIN requested r ON log.alert_id = r.alert_id
)
SELECT json_build_object(
  'alert_exists', EXISTS (SELECT 1 FROM alert_row),
  'alert_deleted', COALESCE((SELECT deleted FROM alert_row), false),
  'workspace_id', (SELECT workspace_id::text FROM alert_row),
  'project_id', (SELECT project_id::text FROM alert_row),
  'name', (SELECT name FROM alert_row),
  'critical_threshold_value', (SELECT critical_threshold_value FROM alert_row),
  'warning_threshold_value', (SELECT warning_threshold_value FROM alert_row),
  'log_count', (SELECT count(*) FROM log_rows),
  'deleted_log_count', (SELECT count(*) FROM log_rows WHERE deleted = true)
)::text;
`);
  return rows[0] || {};
}

async function cleanupAlertArtifacts(alertId) {
  const deletedRows = await runPsqlJson(`
WITH requested AS (
  SELECT ${sqlUuid(alertId)} AS alert_id
),
deleted_logs AS (
  DELETE FROM tracer_useralertmonitorlog
  WHERE alert_id IN (SELECT alert_id FROM requested)
  RETURNING id
),
deleted_alerts AS (
  DELETE FROM tracer_useralertmonitor
  WHERE id IN (SELECT alert_id FROM requested)
  RETURNING id
)
SELECT json_build_object(
  'deleted_log_rows', (SELECT count(*) FROM deleted_logs),
  'deleted_alert_rows', (SELECT count(*) FROM deleted_alerts)
)::text;
`);
  const remainingRows = await runPsqlJson(`
WITH requested AS (
  SELECT ${sqlUuid(alertId)} AS alert_id
)
SELECT json_build_object(
  'remaining_log_count', (
    SELECT count(*) FROM tracer_useralertmonitorlog
    WHERE alert_id IN (SELECT alert_id FROM requested)
  ),
  'remaining_alert_count', (
    SELECT count(*) FROM tracer_useralertmonitor
    WHERE id IN (SELECT alert_id FROM requested)
  )
)::text;
`);
  return {
    ...(deletedRows[0] || {}),
    ...(remainingRows[0] || {}),
  };
}

async function runPsqlJson(sql) {
  const stdout = await runPsql(sql);
  return stdout
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

async function runPsql(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFileAsync(
    "docker",
    ["exec", container, "psql", "-qAt", "-U", user, "-d", database, "-c", sql],
    { maxBuffer: 1024 * 1024 * 10 },
  );
  return stdout;
}

function isAlertApiUrl(url) {
  return (
    url.includes("/tracer/user-alerts/") ||
    url.includes("/tracer/user-alert-logs/")
  );
}

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID, received ${value}`);
  return `'${value}'::uuid`;
}

function modifierKey() {
  return process.platform === "darwin" ? "Meta" : "Control";
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH)
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  if (process.platform === "linux") return "/usr/bin/google-chrome";
  return undefined;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
