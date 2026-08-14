import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  createAuthenticatedContext,
} from "../lib/api-client.mjs";
import { queryWithFilters } from "../lib/fixtures.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";

async function main() {
  const auth = await createAuthenticatedContext();
  const sample = await resolveObserveVoiceCall(auth.client);
  const apiFailures = [];
  const pageErrors = [];
  const evidence = {
    project_id: sample.project.id,
    project_name: sample.project.name || null,
    trace_id: sample.call.trace_id,
    root_span_id: sample.rootSpanId,
    base_voice_count: sample.baseVoiceCount,
    queue_id: sample.queue.id,
    label_name: sample.label.name,
  };

  const browser = await chromium.launch({
    channel: process.env.PLAYWRIGHT_CHANNEL || "chrome",
    headless: process.env.HEADLESS !== "0",
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 950 },
  });
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
      (url.includes("/tracer/trace/list_voice_calls/") ||
        url.includes("/tracer/trace/voice_call_detail/") ||
        url.includes("/model-hub/annotation-queues/for-source/")) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const initialListResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/tracer/trace/list_voice_calls/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(`${APP_BASE}/dashboard/observe/${sample.project.id}/llm-tracing`, {
      waitUntil: "domcontentloaded",
    });
    await initialListResponse;

    if ((await gridRowCount(page)) < 1) {
      await page.getByRole("button", { name: /Past 7D/ }).click();
      const wideListResponse = page.waitForResponse(
        (response) =>
          response.url().includes("/tracer/trace/list_voice_calls/") &&
          response.status() < 400,
        { timeout: 60000 },
      );
      await page.getByRole("menuitem", { name: "Past 6M" }).click();
      await wideListResponse;
    }

    await waitForGridRows(page, 1);
    evidence.rendered_rows = await gridRowCount(page);
    await page.screenshot({
      path: "/tmp/observe-voice-list-smoke.png",
      fullPage: true,
    });
    evidence.list_screenshot = "/tmp/observe-voice-list-smoke.png";

    const detailResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/tracer/trace/voice_call_detail/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.locator(".ag-center-cols-container [role='row']").first().click();
    await detailResponse;
    await page.getByText("Call Analytics").waitFor({
      state: "visible",
      timeout: 30000,
    });
    await page.getByText("Voice").first().waitFor({
      state: "visible",
      timeout: 30000,
    });

    const queueResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/model-hub/annotation-queues/for-source/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.getByRole("button", { name: "Actions" }).click();
    await page.getByRole("menuitem", { name: "Annotate" }).click();
    await queueResponse;
    await page.getByRole("heading", { name: "Annotate" }).waitFor({
      state: "visible",
      timeout: 30000,
    });
    await page.getByText(sample.label.name, { exact: false }).first().waitFor({
      state: "visible",
      timeout: 30000,
    });

    await page.screenshot({
      path: "/tmp/observe-voice-drawer-annotate-smoke.png",
      fullPage: true,
    });
    evidence.drawer_screenshot = "/tmp/observe-voice-drawer-annotate-smoke.png";

    const fullPage = await context.newPage();
    const fullPageDetailResponse = fullPage.waitForResponse(
      (response) =>
        response.url().includes("/tracer/trace/voice_call_detail/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await fullPage.goto(
      `${APP_BASE}/dashboard/observe/${sample.project.id}/voice/${sample.call.trace_id}`,
      { waitUntil: "domcontentloaded" },
    );
    await fullPageDetailResponse;
    await fullPage.getByText("Call Analytics").waitFor({
      state: "visible",
      timeout: 30000,
    });
    await fullPage.screenshot({
      path: "/tmp/observe-voice-full-page-smoke.png",
      fullPage: true,
    });
    evidence.full_page_screenshot = "/tmp/observe-voice-full-page-smoke.png";
    await fullPage.close();

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

async function resolveObserveVoiceCall(client) {
  const preferredProjectId =
    process.env.OBSERVE_VOICE_PROJECT_ID || process.env.OBSERVE_PROJECT_ID;
  const projects = preferredProjectId
    ? [{ id: preferredProjectId, name: "env observe voice project" }]
    : asArray(
        await client.get(apiPath("/tracer/project/list_projects/"), {
          query: { page_number: 0, page_size: 100 },
        }),
      );

  for (const project of projects) {
    if (!project?.id) continue;
    const payload = await client.get(
      queryWithFilters(apiPath("/tracer/trace/list_voice_calls/"), [], {
        project_id: project.id,
        page: 1,
        page_size: 25,
      }),
    );
    const calls = asArray(payload).filter((row) => row?.trace_id);
    if (!calls.length) continue;

    for (const call of calls) {
      const detail = await client.get(apiPath("/tracer/trace/voice_call_detail/"), {
        query: { trace_id: call.trace_id },
      });
      const rootSpan = findRootConversationSpan(detail?.observation_span);
      if (!rootSpan?.id) continue;

      const queueEntries = asArray(
        await client.get(apiPath("/model-hub/annotation-queues/for-source/"), {
          query: {
            sources: JSON.stringify([
              {
                source_type: "trace",
                source_id: call.trace_id,
                span_notes_source_id: rootSpan.id,
              },
            ]),
          },
        }),
      );
      const queueEntry = queueEntries.find(
        (entry) => entry?.queue?.is_default && asArray(entry.labels).length > 0,
      );
      const label =
        asArray(queueEntry?.labels).find((item) => item?.allow_notes) ||
        asArray(queueEntry?.labels)[0];
      if (!queueEntry?.queue?.id || !label?.name) continue;

      return {
        project,
        call,
        rootSpanId: rootSpan.id,
        queue: queueEntry.queue,
        label,
        baseVoiceCount: calls.length,
      };
    }
  }

  throw new Error("No observe voice call with default queue labels was found.");
}

function findRootConversationSpan(spans) {
  const rows = asArray(spans);
  return (
    rows.find(
      (span) => !span?.parent_span_id && span?.observation_type === "conversation",
    ) ||
    rows.find((span) => !span?.parent_span_id) ||
    rows[0]
  );
}

async function waitForGridRows(page, minRows) {
  await page.waitForFunction(
    (minimum) =>
      document.querySelectorAll(".ag-center-cols-container [role='row']").length >=
      minimum,
    minRows,
    { timeout: 60000 },
  );
}

async function gridRowCount(page) {
  return page.locator(".ag-center-cols-container [role='row']").count();
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
