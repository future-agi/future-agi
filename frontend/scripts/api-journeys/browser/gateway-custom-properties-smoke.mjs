/* eslint-disable no-console */
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  requireMutations,
  unwrapApiData,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const CREATE_SCREENSHOT_PATH = "/tmp/gateway-custom-property-create-smoke.png";
const UPDATE_SCREENSHOT_PATH = "/tmp/gateway-custom-property-update-smoke.png";
const DELETE_SCREENSHOT_PATH = "/tmp/gateway-custom-property-delete-smoke.png";
const INVALID_SCREENSHOT_PATH =
  "/tmp/gateway-custom-property-invalid-default-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/gateway-custom-properties-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);
const PROPERTY_PREFIX = "ui_gateway_custom_property_";

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const propertyName = `${PROPERTY_PREFIX}${suffix}`;
  const invalidPropertyName = `${propertyName}_invalid`;
  const cleanupEvidence = [];
  const evidence = await preflightGatewayCustomProperties(
    auth.client,
    cleanupEvidence,
  );
  const apiFailures = [];
  const pageErrors = [];
  const gatewayRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let caughtError = null;
  let createdPropertyId = null;
  let deletedViaUi = false;

  await cleanupDisposableProperties(auth.client, cleanupEvidence);

  try {
    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
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
        if (!isAllowedCustomPropertyMutation(request.method(), url)) {
          unexpectedMutations.push(mutation);
        }
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isGatewayApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponsesDuring(
      page,
      "initial Gateway custom properties load",
      [
        (response) =>
          response.url().includes("/agentcc/gateways/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/custom-properties/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/custom-properties`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/custom-properties");

    for (const label of [
      "Custom Properties",
      "Define custom metadata schemas for your request logs",
      "Add Property",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await assertInvalidEnumDefaultIsBlocked(page, invalidPropertyName);
    evidence.invalid_enum_default_blocked = true;
    await page.screenshot({ path: INVALID_SCREENSHOT_PATH, fullPage: true });

    await clickVisibleText(page, "Add Property", { exact: true });
    await waitForVisibleText(page, "Create Property Schema", { exact: true });
    await setDialogInputByLabel(page, "Property Name", propertyName);
    await setDialogInputByLabel(
      page,
      "Description",
      "Browser smoke enum property",
    );
    await selectDialogOption(page, "Enum");
    await addEnumValue(page, "alpha");
    await addEnumValue(page, "beta");
    await setDialogInputByLabel(page, "Default Value", "alpha");

    const createResponse = await waitForResponseDuring(
      page,
      "create custom property",
      (response) =>
        response.url().includes("/agentcc/custom-properties/") &&
        response.request().method() === "POST" &&
        response.status() < 400,
      () => clickDialogButton(page, "Create"),
    );
    const createdProperty = await responseResult(createResponse);
    createdPropertyId = createdProperty.id;
    assert(createdPropertyId, "Create custom property response omitted id.");
    assertCustomPropertyShape(createdProperty, {
      name: propertyName,
      description: "Browser smoke enum property",
      property_type: "enum",
      allowed_values: ["alpha", "beta"],
      default_value: "alpha",
    });
    evidence.created_property = customPropertyEvidence(createdProperty);
    await waitForNoVisibleText(page, "Create Property Schema", { exact: true });

    await setVisibleInputByPlaceholder(
      page,
      "Search properties...",
      propertyName,
    );
    await waitForVisibleText(page, propertyName, { exact: true });
    await waitForVisibleText(page, "enum", { exact: true });
    await waitForVisibleText(page, "alpha", { exact: true });
    await waitForVisibleText(page, "beta", { exact: true });
    await page.screenshot({ path: CREATE_SCREENSHOT_PATH, fullPage: true });

    await clickRowButtonByTitle(page, propertyName, "Edit");
    await waitForVisibleText(page, "Edit Property Schema", { exact: true });
    await setDialogInputByLabel(
      page,
      "Description",
      "Browser smoke enum property updated",
    );
    await addEnumValue(page, "gamma");
    await setDialogInputByLabel(page, "Default Value", "gamma");

    const updateResponse = await waitForResponseDuring(
      page,
      "update custom property",
      (response) =>
        response
          .url()
          .includes(`/agentcc/custom-properties/${createdPropertyId}/`) &&
        response.request().method() === "PATCH" &&
        response.status() < 400,
      () => clickDialogButton(page, "Update"),
    );
    const updatedProperty = await responseResult(updateResponse);
    assertCustomPropertyShape(updatedProperty, {
      name: propertyName,
      description: "Browser smoke enum property updated",
      property_type: "enum",
      allowed_values: ["alpha", "beta", "gamma"],
      default_value: "gamma",
    });
    evidence.updated_property = customPropertyEvidence(updatedProperty);
    await waitForNoVisibleText(page, "Edit Property Schema", { exact: true });
    await waitForVisibleText(page, "Browser smoke enum property updated", {
      exact: true,
    });
    await waitForVisibleText(page, "gamma", { exact: true });

    const apiUpdatedProperty = await auth.client.get(
      apiPath("/agentcc/custom-properties/{id}/", { id: createdPropertyId }),
    );
    assertCustomPropertyShape(apiUpdatedProperty, {
      name: propertyName,
      description: "Browser smoke enum property updated",
      property_type: "enum",
      allowed_values: ["alpha", "beta", "gamma"],
      default_value: "gamma",
    });
    await page.screenshot({ path: UPDATE_SCREENSHOT_PATH, fullPage: true });

    await clickRowButtonByTitle(page, propertyName, "Delete");
    await waitForVisibleText(page, "Delete Property Schema", { exact: true });
    const deleteResponse = await waitForResponseDuring(
      page,
      "delete custom property",
      (response) =>
        response
          .url()
          .includes(`/agentcc/custom-properties/${createdPropertyId}/`) &&
        response.request().method() === "DELETE" &&
        response.status() < 400,
      () => clickDialogButton(page, "Delete"),
    );
    assert(
      deleteResponse.status() === 200 || deleteResponse.status() === 204,
      `Delete custom property returned HTTP ${deleteResponse.status()}.`,
    );
    await waitForPropertyAbsent(auth.client, propertyName);
    deletedViaUi = true;
    await waitForAnyVisibleText(page, [
      "No properties match your search",
      "No custom properties defined",
    ]);
    await page.screenshot({ path: DELETE_SCREENSHOT_PATH, fullPage: true });

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected Gateway custom-property mutations: ${unexpectedMutations.join(
        "; ",
      )}`,
    );
    assert(
      browserMutations.length === 3,
      `Expected 3 custom-property browser mutations, saw ${browserMutations.length}.`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence: {
            ...evidence,
            create_screenshot: CREATE_SCREENSHOT_PATH,
            update_screenshot: UPDATE_SCREENSHOT_PATH,
            delete_screenshot: DELETE_SCREENSHOT_PATH,
            invalid_default_screenshot: INVALID_SCREENSHOT_PATH,
            expected_mutation_count: browserMutations.length,
          },
          cleanup: cleanupEvidence,
          gateway_request_count: gatewayRequests.length,
          browser_mutations: browserMutations,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    caughtError = error;
    await page
      ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
  } finally {
    if (createdPropertyId && !deletedViaUi) {
      await auth.client
        .delete(
          apiPath("/agentcc/custom-properties/{id}/", {
            id: createdPropertyId,
          }),
          { okStatuses: [200, 204, 404] },
        )
        .then(() =>
          cleanupEvidence.push({
            cleanup: "created custom property",
            id: createdPropertyId,
            status: "passed",
          }),
        )
        .catch((error) =>
          cleanupEvidence.push({
            cleanup: "created custom property",
            id: createdPropertyId,
            status: "failed",
            error: error.message,
          }),
        );
    }
    await cleanupDisposableProperties(auth.client, cleanupEvidence);
    if (browser) await browser.close();
  }

  const cleanupFailures = cleanupEvidence.filter(
    (item) => item.status === "failed",
  );

  if (caughtError || cleanupFailures.length > 0) {
    console.error(
      JSON.stringify(
        {
          status: "failed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
          cleanup: cleanupEvidence,
          api_failures: apiFailures,
          page_errors: pageErrors,
          gateway_requests: gatewayRequests,
          browser_mutations: browserMutations,
          unexpected_mutations: unexpectedMutations,
          failure_screenshot: FAILURE_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
    if (caughtError) throw caughtError;
    throw new Error(
      `Gateway custom-property cleanup failed: ${cleanupFailures
        .map((item) => item.error)
        .join("; ")}`,
    );
  }
}

async function preflightGatewayCustomProperties(client, evidence) {
  const gateways = asArray(await client.get(apiPath("/agentcc/gateways/")));
  assert(gateways.length > 0, "Gateway list returned no configured gateway.");
  const properties = asArray(
    await client.get(apiPath("/agentcc/custom-properties/")),
  );
  const staleIds = await cleanupDisposableProperties(client, evidence);

  return {
    gateway_id: gateways[0].id || "default",
    gateway_name: gateways[0].name || "Agent Command Center Gateway",
    starting_custom_property_count: properties.length,
    stale_property_ids: staleIds,
  };
}

async function cleanupDisposableProperties(client, evidence = []) {
  const deletedIds = [];
  const properties = asArray(
    await client.get(apiPath("/agentcc/custom-properties/")),
  );
  for (const property of properties) {
    const name = String(property?.name || "");
    if (!name.startsWith(PROPERTY_PREFIX)) continue;
    await client
      .delete(
        apiPath("/agentcc/custom-properties/{id}/", { id: property.id }),
        {
          okStatuses: [200, 204, 404],
        },
      )
      .then(() => {
        deletedIds.push(property.id);
        evidence.push({
          cleanup: "stale UI custom property",
          id: property.id,
          name,
          status: "passed",
        });
      })
      .catch((error) =>
        evidence.push({
          cleanup: "stale UI custom property",
          id: property.id,
          name,
          status: "failed",
          error: error.message,
        }),
      );
  }
  return deletedIds;
}

async function assertInvalidEnumDefaultIsBlocked(page, propertyName) {
  await clickVisibleText(page, "Add Property", { exact: true });
  await waitForVisibleText(page, "Create Property Schema", { exact: true });
  await setDialogInputByLabel(page, "Property Name", propertyName);
  await setDialogInputByLabel(page, "Description", "Invalid enum default");
  await selectDialogOption(page, "Enum");
  await addEnumValue(page, "alpha");
  await addEnumValue(page, "beta");
  await setDialogInputByLabel(page, "Default Value", "gamma");
  await waitForVisibleText(page, "Default must match an allowed value", {
    exact: true,
  });
  assert(
    await isDialogButtonDisabled(page, "Create"),
    "Create button stayed enabled for enum default outside allowed values.",
  );
  await clickDialogButton(page, "Cancel");
  await waitForNoVisibleText(page, "Create Property Schema", { exact: true });
}

async function addEnumValue(page, value) {
  await setDialogInputByPlaceholder(page, "Add enum value", value);
  await clickDialogButton(page, "Add");
  await waitForVisibleText(page, value, { exact: true });
}

function assertCustomPropertyShape(property, expected) {
  assert(property?.name === expected.name, "Custom property name mismatch.");
  assert(
    property?.description === expected.description,
    "Custom property description mismatch.",
  );
  assert(
    property?.property_type === expected.property_type,
    "Custom property type mismatch.",
  );
  for (const value of expected.allowed_values) {
    assert(
      asArray(property?.allowed_values).includes(value),
      `Custom property allowed_values omitted ${value}.`,
    );
  }
  assert(
    property?.default_value === expected.default_value,
    "Custom property default_value mismatch.",
  );
}

function customPropertyEvidence(property) {
  return {
    id: property.id,
    name: property.name,
    description: property.description,
    property_type: property.property_type,
    required: property.required,
    allowed_values: property.allowed_values,
    default_value: property.default_value,
  };
}

async function waitForPropertyAbsent(client, propertyName, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const properties = asArray(
      await client.get(apiPath("/agentcc/custom-properties/")),
    );
    if (!properties.some((property) => property?.name === propertyName)) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(
    `Custom property ${propertyName} remained visible after delete.`,
  );
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
    window.setNativeInputValue = (input, value) => {
      const prototype =
        input.tagName === "TEXTAREA"
          ? window.HTMLTextAreaElement.prototype
          : window.HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
      descriptor.set.call(input, value);
      input.dispatchEvent(
        new InputEvent("input", {
          bubbles: true,
          cancelable: true,
          inputType: "insertText",
          data: value,
        }),
      );
      input.dispatchEvent(new Event("change", { bubbles: true }));
    };
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
      localStorage.removeItem("agentcc_getting_started_dismissed");
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

async function waitForAnyVisibleText(page, texts, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (expectedTexts) =>
      window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return expectedTexts.includes(textContent);
      }),
    { timeout },
    texts,
  );
}

async function waitForNoVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) =>
      !window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      }),
    { timeout },
    { text, exact },
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
        elements.find((candidate) =>
          candidate.closest(
            "a,[role='button'],[role='menuitem'],[role='option']",
          ),
        ) ||
        elements[0];
      const clickable =
        element?.closest(
          "button,a,[role='button'],[role='menuitem'],[role='option'],tr",
        ) || element;
      if (!clickable || clickable.disabled) return false;
      window.dispatchClick(clickable);
      return true;
    },
    { text, exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

async function clickDialogButton(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const clicked = await page.evaluate((expectedLabel) => {
    const dialog = window.visibleElements("[role='dialog']").at(-1);
    if (!dialog) return false;
    const button = Array.from(dialog.querySelectorAll("button")).find(
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

async function isDialogButtonDisabled(page, label) {
  return page.evaluate((expectedLabel) => {
    const dialog = window.visibleElements("[role='dialog']").at(-1);
    if (!dialog) return false;
    const button = Array.from(dialog.querySelectorAll("button")).find(
      (candidate) =>
        window.normalizeText(candidate.textContent) === expectedLabel,
    );
    return Boolean(button?.disabled);
  }, label);
}

async function setDialogInputByLabel(page, label, value, timeout = 30000) {
  await page.waitForFunction(
    (expectedLabel) =>
      window
        .visibleElements("[role='dialog'] label")
        .some((element) =>
          window.normalizeText(element.textContent).includes(expectedLabel),
        ),
    { timeout },
    label,
  );
  const changed = await page.evaluate(
    ({ expectedLabel, nextValue }) => {
      const dialog = window.visibleElements("[role='dialog']").at(-1);
      if (!dialog) return false;
      const labelElement = Array.from(dialog.querySelectorAll("label")).find(
        (element) =>
          window.normalizeText(element.textContent).includes(expectedLabel),
      );
      if (!labelElement) return false;
      const inputId = labelElement.getAttribute("for");
      const input = inputId
        ? dialog.querySelector(`#${CSS.escape(inputId)}`)
        : labelElement.parentElement?.querySelector("input,textarea");
      if (!input) return false;
      window.setNativeInputValue(input, nextValue);
      return true;
    },
    { expectedLabel: label, nextValue: value },
  );
  assert(changed, `Could not set dialog input: ${label}`);
}

async function setDialogInputByPlaceholder(
  page,
  placeholder,
  value,
  timeout = 30000,
) {
  await page.waitForFunction(
    (expectedPlaceholder) => {
      const dialog = window.visibleElements("[role='dialog']").at(-1);
      return Array.from(dialog?.querySelectorAll("input,textarea") || []).some(
        (element) => element.placeholder === expectedPlaceholder,
      );
    },
    { timeout },
    placeholder,
  );
  const changed = await page.evaluate(
    ({ expectedPlaceholder, nextValue }) => {
      const dialog = window.visibleElements("[role='dialog']").at(-1);
      const input = Array.from(
        dialog?.querySelectorAll("input,textarea") || [],
      ).find((element) => element.placeholder === expectedPlaceholder);
      if (!input) return false;
      window.setNativeInputValue(input, nextValue);
      return true;
    },
    { expectedPlaceholder: placeholder, nextValue: value },
  );
  assert(changed, `Could not set dialog placeholder: ${placeholder}`);
}

async function setVisibleInputByPlaceholder(
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
  const changed = await page.evaluate(
    ({ expectedPlaceholder, nextValue }) => {
      const input = window
        .visibleElements("input,textarea")
        .find((element) => element.placeholder === expectedPlaceholder);
      if (!input) return false;
      window.setNativeInputValue(input, nextValue);
      return true;
    },
    { expectedPlaceholder: placeholder, nextValue: value },
  );
  assert(changed, `Could not set input placeholder: ${placeholder}`);
}

async function selectDialogOption(page, optionLabel) {
  const opened = await page.evaluate(() => {
    const dialog = window.visibleElements("[role='dialog']").at(-1);
    const combobox = dialog?.querySelector("[role='combobox']");
    if (!combobox || combobox.getAttribute("aria-disabled") === "true") {
      return false;
    }
    window.dispatchClick(combobox);
    return true;
  });
  assert(opened, "Could not open dialog select.");
  await clickVisibleText(page, optionLabel, { exact: true });
}

async function clickRowButtonByTitle(page, rowText, title, timeout = 30000) {
  await waitForVisibleText(page, rowText, { exact: true, timeout });
  const clicked = await page.evaluate(
    ({ expectedRowText, expectedTitle }) => {
      const row = window
        .visibleElements("tr")
        .find((candidate) =>
          window.normalizeText(candidate.textContent).includes(expectedRowText),
        );
      if (!row) return false;
      const button = Array.from(row.querySelectorAll("button")).find(
        (candidate) =>
          candidate.getAttribute("title") === expectedTitle &&
          !candidate.disabled,
      );
      if (!button) return false;
      window.dispatchClick(button);
      return true;
    },
    { expectedRowText: rowText, expectedTitle: title },
  );
  assert(clicked, `Could not click row ${title} action for ${rowText}`);
}

async function responseResult(response) {
  const body = await response.json();
  return unwrapApiData(body);
}

function isAllowedCustomPropertyMutation(method, rawUrl) {
  const url = new URL(rawUrl);
  if (!url.pathname.includes("/agentcc/custom-properties/")) return false;
  if (
    method === "POST" &&
    /\/agentcc\/custom-properties\/?$/.test(url.pathname)
  ) {
    return true;
  }
  if (
    ["PATCH", "PUT", "DELETE"].includes(method) &&
    /\/agentcc\/custom-properties\/[^/]+\/?$/.test(url.pathname)
  ) {
    return true;
  }
  return false;
}

function isGatewayApiUrl(url) {
  return url.includes("/agentcc/");
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
