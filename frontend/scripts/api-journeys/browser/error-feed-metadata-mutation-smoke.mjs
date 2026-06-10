/* eslint-disable no-console */
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  currentUserEmail,
  envFlag,
  requireMutations,
} from "../lib/api-client.mjs";
import {
  browserExecutablePath,
  installRuntimeConfig,
  prepareErrorFeedRow,
} from "./error-feed-smoke.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/error-feed-metadata-mutation-smoke.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  assert(
    envFlag("ERROR_FEED_FORCE_FIXTURE"),
    "Set ERROR_FEED_FORCE_FIXTURE=1 so this mutation smoke only touches a disposable Error Feed fixture.",
  );

  const auth = await createAuthenticatedContext();
  const fixtureEvidence = [];
  const cleanupEvidence = [];
  const prepared = await prepareErrorFeedRow({
    client: auth.client,
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    runId: auth.runId,
    evidence: fixtureEvidence,
  });
  const row = prepared.row;
  const clusterId = row.cluster_id;
  const traceId = prepared.seededFixture?.trace_id || row.trace_id;
  const assignableMember = await resolveAssignableMember(auth);
  const memberLabel =
    assignableMember.name || assignableMember.email.split("@")[0];

  const apiFailures = [];
  const pageErrors = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  let browser = null;
  let caughtError = null;
  let cleanupError = null;
  let result = null;

  try {
    const initialDetail = await loadIssueDetail(auth.client, clusterId);
    assertIssueState(initialDetail.row, {
      status: "escalating",
      severity: "high",
      assignees: [],
      label: "initial fixture state",
    });

    const deepAnalysis = await auth.client.get(rootCausePath(clusterId), {
      query: { trace_id: traceId },
    });
    assert(
      deepAnalysis?.status === "done",
      `Expected cached deep-analysis status=done, got ${JSON.stringify(
        deepAnalysis,
      )}`,
    );

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 980 },
      args: ["--no-sandbox"],
    });
    const page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installAuthState(page, auth);
    await installBrowserHelpers(page);

    page.on("request", (request) => {
      const url = request.url();
      if (!isErrorFeedApiUrl(url)) return;
      if (!MUTATION_METHODS.has(request.method())) return;

      const mutation = {
        method: request.method(),
        path: new URL(url).pathname,
        body: parseJson(request.postData()),
      };
      browserMutations.push(mutation);
      if (!isAllowedMutation(request.method(), url, clusterId)) {
        unexpectedMutations.push(
          `${request.method()} ${url} ${request.postData() || ""}`.trim(),
        );
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isErrorFeedApiUrl(url) && response.status() >= 400) {
        apiFailures.push(
          `${response.status()} ${response.request().method()} ${url}`,
        );
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "Error Feed metadata detail load",
      (response) =>
        response.request().method() === "GET" &&
        isIssueDetailUrl(response.url(), clusterId) &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/error-feed/${clusterId}`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, `/dashboard/error-feed/${clusterId}`);
    await expectVisibleText(page, row.error.name);
    await expectVisibleText(page, "Triage", { exact: true });
    await expectTestIdText(page, "error-feed-status-dropdown", "Escalating");
    await expectTestIdText(page, "error-feed-severity-dropdown", "High");
    await expectTestIdText(page, "error-feed-assignee-dropdown", "Assign");
    await expectVisibleText(page, "Analysis complete", { exact: true });

    await patchFromAction(page, "status mutation", clusterId, async () => {
      await clickByTestId(page, "error-feed-status-dropdown");
      await clickByTestId(page, "error-feed-status-option-acknowledged");
    });
    await expectTestIdText(page, "error-feed-status-dropdown", "Acknowledged");
    const afterStatus = await loadIssueDetail(auth.client, clusterId);
    assertIssueState(afterStatus.row, {
      status: "acknowledged",
      severity: "high",
      assignees: [],
      label: "after status mutation",
    });

    await patchFromAction(page, "severity mutation", clusterId, async () => {
      await clickByTestId(page, "error-feed-severity-dropdown");
      await clickByTestId(page, "error-feed-severity-option-low");
    });
    await expectTestIdText(page, "error-feed-severity-dropdown", "Low");
    const afterSeverity = await loadIssueDetail(auth.client, clusterId);
    assertIssueState(afterSeverity.row, {
      status: "acknowledged",
      severity: "low",
      assignees: [],
      label: "after severity mutation",
    });

    await patchFromAction(page, "assignee mutation", clusterId, async () => {
      await clickByTestId(page, "error-feed-assignee-dropdown");
      await clickAssigneeOption(page, assignableMember.email);
    });
    await expectTestIdText(page, "error-feed-assignee-dropdown", memberLabel);
    const afterAssign = await loadIssueDetail(auth.client, clusterId);
    assertIssueState(afterAssign.row, {
      status: "acknowledged",
      severity: "low",
      assignees: [assignableMember.email],
      label: "after assignee mutation",
    });

    await patchFromAction(
      page,
      "assignee clear mutation",
      clusterId,
      async () => {
        await clickByTestId(page, "error-feed-assignee-dropdown");
        await clickByTestId(page, "error-feed-assignee-unassign-option");
      },
    );
    await expectTestIdText(page, "error-feed-assignee-dropdown", "Assign");
    const afterUnassign = await loadIssueDetail(auth.client, clusterId);
    assertIssueState(afterUnassign.row, {
      status: "acknowledged",
      severity: "low",
      assignees: [],
      label: "after assignee clear mutation",
    });

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected Error Feed mutations: ${unexpectedMutations.join("; ")}`,
    );
    assert(
      browserMutations.length === 4,
      `Expected 4 browser PATCH mutations, got ${JSON.stringify(
        browserMutations,
      )}`,
    );
    assertMutationBodies(browserMutations, assignableMember.email);

    result = {
      status: "passed",
      app_base: APP_BASE,
      api_base: auth.apiBase,
      organization_id: auth.organizationId,
      workspace_id: auth.workspaceId,
      evidence: {
        cluster_id: clusterId,
        project_id: row.project_id,
        trace_id: traceId,
        assignable_email: assignableMember.email,
        deep_analysis_status: deepAnalysis.status,
        final_state: {
          status: afterUnassign.row.status,
          severity: afterUnassign.row.severity,
          assignees: afterUnassign.row.assignees,
        },
        browser_mutations: browserMutations,
        screenshot: SCREENSHOT_PATH,
        fixture: fixtureEvidence,
        cleanup: cleanupEvidence,
      },
    };
  } catch (error) {
    caughtError = error;
    throw error;
  } finally {
    if (browser) await browser.close();
    const cleanupFailures = await prepared.cleanup.run(cleanupEvidence);
    if (cleanupFailures.length > 0 && !caughtError) {
      cleanupError = new Error(
        `Error Feed metadata mutation cleanup failed: ${JSON.stringify(
          cleanupFailures,
        )}`,
      );
    }
  }

  if (cleanupError) throw cleanupError;
  console.log(JSON.stringify(result, null, 2));
}

async function resolveAssignableMember(auth) {
  const payload = await auth.client.get(
    apiPath("/model-hub/organizations/{organization_id}/users/", {
      organization_id: auth.organizationId,
    }),
    { query: { is_active: true } },
  );
  const members = asArray(payload).filter((member) => member?.email);
  const currentEmail = currentUserEmail(auth.user);
  const member =
    members.find(
      (candidate) =>
        currentEmail &&
        candidate.email.toLowerCase() === currentEmail.toLowerCase(),
    ) || members[0];
  assert(
    member?.email,
    `No assignable organization member found: ${JSON.stringify(payload)}`,
  );
  return member;
}

async function loadIssueDetail(client, clusterId) {
  return client.get(issueDetailPath(clusterId));
}

function assertIssueState(row, { status, severity, assignees, label }) {
  assert(
    row?.status === status,
    `${label}: expected status=${status}, got ${row?.status}`,
  );
  assert(
    row?.severity === severity,
    `${label}: expected severity=${severity}, got ${row?.severity}`,
  );
  const actualAssignees = asArray(row?.assignees).map((email) =>
    String(email).toLowerCase(),
  );
  const expectedAssignees = assignees.map((email) =>
    String(email).toLowerCase(),
  );
  assert(
    JSON.stringify(actualAssignees) === JSON.stringify(expectedAssignees),
    `${label}: expected assignees=${JSON.stringify(
      expectedAssignees,
    )}, got ${JSON.stringify(actualAssignees)}`,
  );
}

async function installAuthState(page, auth) {
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

async function installBrowserHelpers(page) {
  await page.evaluateOnNewDocument(() => {
    window.__apiJourneyIsVisible = (element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    };
    window.__apiJourneyVisibleTexts = () =>
      Array.from(document.querySelectorAll("body *"))
        .filter(window.__apiJourneyIsVisible)
        .map((element) => String(element.textContent || "").trim())
        .filter(Boolean);
  });
}

async function patchFromAction(page, label, clusterId, action) {
  return waitForResponseDuring(
    page,
    label,
    (response) =>
      response.request().method() === "PATCH" &&
      isIssueDetailUrl(response.url(), clusterId) &&
      response.status() < 400,
    action,
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

async function waitForPath(page, expectedPath) {
  await page.waitForFunction(
    (pathName) => window.location.pathname === pathName,
    { timeout: 30000 },
    expectedPath,
  );
}

async function expectVisibleText(page, text, { exact = false } = {}) {
  await page.waitForFunction(
    ({ expectedText, exactMatch }) =>
      window
        .__apiJourneyVisibleTexts()
        .some((value) =>
          exactMatch ? value === expectedText : value.includes(expectedText),
        ),
    { timeout: 30000 },
    { expectedText: text, exactMatch: exact },
  );
}

async function expectTestIdText(page, testId, text) {
  await page.waitForFunction(
    ({ targetTestId, expectedText }) => {
      const element = document.querySelector(`[data-testid="${targetTestId}"]`);
      return (
        element &&
        window.__apiJourneyIsVisible(element) &&
        (element.textContent || "").trim().includes(expectedText)
      );
    },
    { timeout: 30000 },
    { targetTestId: testId, expectedText: text },
  );
}

async function clickByTestId(page, testId) {
  await page.waitForFunction(
    (targetTestId) =>
      Array.from(
        document.querySelectorAll(`[data-testid="${targetTestId}"]`),
      ).some((element) => window.__apiJourneyIsVisible(element)),
    { timeout: 30000 },
    testId,
  );
  const clicked = await page.evaluate((targetTestId) => {
    const element = Array.from(
      document.querySelectorAll(`[data-testid="${targetTestId}"]`),
    ).find((candidate) => window.__apiJourneyIsVisible(candidate));
    if (!element) return false;
    element.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return true;
  }, testId);
  assert(clicked, `Could not click [data-testid="${testId}"].`);
}

async function clickAssigneeOption(page, email) {
  await page.waitForFunction(
    (targetEmail) =>
      Array.from(
        document.querySelectorAll('[data-testid="error-feed-assignee-option"]'),
      ).some(
        (element) =>
          window.__apiJourneyIsVisible(element) &&
          element.getAttribute("data-email") === targetEmail,
      ),
    { timeout: 30000 },
    email,
  );
  const clicked = await page.evaluate((targetEmail) => {
    const element = Array.from(
      document.querySelectorAll('[data-testid="error-feed-assignee-option"]'),
    ).find(
      (candidate) =>
        window.__apiJourneyIsVisible(candidate) &&
        candidate.getAttribute("data-email") === targetEmail,
    );
    if (!element) return false;
    element.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return true;
  }, email);
  assert(clicked, `Could not click assignee option ${email}.`);
}

function assertMutationBodies(mutations, assigneeEmail) {
  assert(
    mutations.some(
      (mutation) =>
        mutation.method === "PATCH" && mutation.body?.status === "acknowledged",
    ),
    `Missing acknowledged status PATCH: ${JSON.stringify(mutations)}`,
  );
  assert(
    mutations.some(
      (mutation) =>
        mutation.method === "PATCH" && mutation.body?.severity === "low",
    ),
    `Missing low severity PATCH: ${JSON.stringify(mutations)}`,
  );
  assert(
    mutations.some(
      (mutation) =>
        mutation.method === "PATCH" &&
        mutation.body?.assignee === assigneeEmail,
    ),
    `Missing assignee PATCH: ${JSON.stringify(mutations)}`,
  );
  assert(
    mutations.some(
      (mutation) =>
        mutation.method === "PATCH" &&
        Object.prototype.hasOwnProperty.call(mutation.body || {}, "assignee") &&
        mutation.body.assignee === null,
    ),
    `Missing assignee clear PATCH: ${JSON.stringify(mutations)}`,
  );
}

function issueDetailPath(clusterId) {
  return `/tracer/feed/issues/${encodeURIComponent(clusterId)}/`;
}

function rootCausePath(clusterId) {
  return `/tracer/feed/issues/${encodeURIComponent(clusterId)}/root-cause/`;
}

function isIssueDetailUrl(url, clusterId) {
  return stripQuery(url).endsWith(issueDetailPath(clusterId));
}

function isErrorFeedApiUrl(url) {
  return (
    url.includes("/tracer/feed/") ||
    url.includes("/tracer/trace-error-analysis/")
  );
}

function isAllowedMutation(method, url, clusterId) {
  return method === "PATCH" && isIssueDetailUrl(url, clusterId);
}

function stripQuery(url) {
  return String(url || "").split("?")[0];
}

function parseJson(value) {
  if (!value) return null;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
