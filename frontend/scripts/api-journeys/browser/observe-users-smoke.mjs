import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  createAuthenticatedContext,
} from "../lib/api-client.mjs";
import { canonicalTextFilter, queryWithFilters } from "../lib/fixtures.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";

async function main() {
  const auth = await createAuthenticatedContext();
  const sample = await resolveObserveUser(auth.client);
  const apiFailures = [];
  const pageErrors = [];
  const evidence = {
    project_id: sample.project.id,
    project_name: sample.project.name || null,
    user_id: sample.user.user_id,
    end_user_id: sample.user.end_user_id,
    base_user_count: sample.baseUserCount,
    related_sessions: sample.relatedSessions,
    related_traces: sample.relatedTraces,
  };

  const browser = await chromium.launch({
    channel: process.env.PLAYWRIGHT_CHANNEL || "chrome",
    headless: process.env.HEADLESS !== "0",
  });
  const context = await browser.newContext();
  await context.addInitScript(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      if (organizationId) sessionStorage.setItem("organizationId", organizationId);
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id) sessionStorage.setItem("futureagi-current-user-id", user.id);
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );

  const page = await context.newPage();
  page.on("response", (response) => {
    const url = response.url();
    if (
      (url.includes("/tracer/users/") ||
        url.includes("/tracer/project/get_users_aggregate_graph_data/") ||
        url.includes("/tracer/trace-session/list_sessions/") ||
        url.includes("/tracer/trace/list_traces_of_session/")) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const usersResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/tracer/users/") && response.status() < 400,
    );
    await page.goto(`${APP_BASE}/dashboard/observe/${sample.project.id}/users`, {
      waitUntil: "domcontentloaded",
    });
    await usersResponse;

    const userRow = page
      .locator(".ag-center-cols-container [role='row']", {
        hasText: String(sample.user.user_id),
      })
      .first();
    await userRow.waitFor({ state: "visible", timeout: 45000 });
    await page.getByText("User Metrics").waitFor({
      state: "visible",
      timeout: 15000,
    });

    await page.screenshot({
      path: "/tmp/observe-users-list-smoke.png",
      fullPage: true,
    });
    evidence.list_screenshot = "/tmp/observe-users-list-smoke.png";

    const sessionResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/tracer/trace-session/list_sessions/") &&
        response.status() < 400,
    );
    await userRow.locator("[col-id='user_id']").click();
    await page.waitForURL(/\/dashboard\/users\//, { timeout: 15000 });
    await sessionResponse;
    await page
      .getByText(String(sample.user.user_id), { exact: false })
      .first()
      .waitFor({ state: "visible", timeout: 15000 });
    await waitForGridRows(page, 1);

    const traceResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/tracer/trace/list_traces_of_session/") &&
        response.status() < 400,
    );
    await page.getByRole("button", { name: /^Trace/ }).first().click();
    await traceResponse;

    if (sample.relatedTraces > 0) {
      if ((await gridRowCount(page)) < 1) {
        await page.getByRole("button", { name: /Past 7D/ }).click();
        const wideTraceResponse = page.waitForResponse(
          (response) =>
            response.url().includes("/tracer/trace/list_traces_of_session/") &&
            response.status() < 400,
        );
        await page.getByRole("menuitem", { name: "Past 6M" }).click();
        await wideTraceResponse;
      }
      await waitForGridRows(page, 1);
    }

    await page.screenshot({
      path: "/tmp/observe-users-detail-trace-smoke.png",
      fullPage: true,
    });
    evidence.detail_screenshot = "/tmp/observe-users-detail-trace-smoke.png";

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
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
  } finally {
    await context.close();
    await browser.close();
  }
}

async function resolveObserveUser(client) {
  const preferredProjectId =
    process.env.OBSERVE_USERS_PROJECT_ID || process.env.OBSERVE_PROJECT_ID;
  const projects = preferredProjectId
    ? [{ id: preferredProjectId, name: "env observe users project" }]
    : asArray(
        await client.get(apiPath("/tracer/project/list_projects/"), {
          query: { page_number: 0, page_size: 50 },
        }),
      );
  const listFilters = [dateFilter(90, "created_at")];
  let bestMatch = null;

  for (const project of projects) {
    if (!project?.id) continue;
    const users = await client.get(
      queryWithFilters(apiPath("/tracer/users/"), listFilters, {
        project_id: project.id,
        current_page_index: 0,
        page_size: 10,
      }),
    );
    const rows = asArray(users.table || users).filter(
      (row) => row?.user_id && row?.end_user_id,
    );
    if (!rows.length) continue;
    const user = rows[0];
    const userFilter = canonicalTextFilter("user_id", "equals", user.user_id);
    const relatedSessions = responseCount(
      await client.get(
        queryWithFilters(
          apiPath("/tracer/trace-session/list_sessions/"),
          userFilter,
          { page_number: 0, page_size: 5 },
        ),
      ),
    );
    const relatedTraces = responseCount(
      await client.get(
        queryWithFilters(apiPath("/tracer/trace/list_traces_of_session/"), userFilter, {
          page_number: 0,
          page_size: 5,
        }),
      ),
    );
    const candidate = {
      project,
      user,
      baseUserCount: responseCount(users),
      relatedSessions,
      relatedTraces,
    };
    if (!bestMatch || candidate.baseUserCount > bestMatch.baseUserCount) {
      bestMatch = candidate;
    }
  }

  if (!bestMatch?.user?.user_id) {
    throw new Error("No recent observe user row was found for browser smoke.");
  }
  if (bestMatch.relatedSessions < 1) {
    throw new Error("Selected observe user has no related sessions.");
  }
  return bestMatch;
}

function dateFilter(days, columnId) {
  const end = new Date();
  const start = new Date(end);
  start.setDate(end.getDate() - days);
  return {
    column_id: columnId,
    filter_config: {
      filter_type: "datetime",
      filter_op: "between",
      filter_value: [start.toISOString(), end.toISOString()],
    },
  };
}

async function waitForGridRows(page, minRows) {
  await page.waitForFunction(
    (minimum) =>
      document.querySelectorAll(".ag-center-cols-container [role='row']").length >=
      minimum,
    minRows,
    { timeout: 45000 },
  );
}

async function gridRowCount(page) {
  return page.locator(".ag-center-cols-container [role='row']").count();
}

function responseCount(payload) {
  if (typeof payload?.metadata?.total_rows === "number") {
    return payload.metadata.total_rows;
  }
  if (typeof payload?.total_count === "number") return payload.total_count;
  if (typeof payload?.total === "number") return payload.total;
  if (typeof payload?.count === "number") return payload.count;
  return asArray(payload).length;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
