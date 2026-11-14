import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  createAuthenticatedContext,
} from "../lib/api-client.mjs";
import { queuePath } from "../lib/fixtures.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";

async function main() {
  const auth = await createAuthenticatedContext();
  const queue = await resolveVisibleQueue(auth.client);
  const { project, session, baseSessionCount } = await resolveObserveSession(
    auth.client,
    queue.id,
  );
  let createdQueueItemId = null;
  const apiFailures = [];
  const pageErrors = [];
  const evidence = {
    project_id: project.id,
    queue_id: queue.id,
    session_id: session.session_id,
    session_name: session.session_name || null,
    base_session_count: baseSessionCount,
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
      (url.includes("/tracer/trace-session/") ||
        url.includes("/model-hub/annotation-queues/")) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const existingQueueEntry = await findQueueEntryForSource(
      auth.client,
      queue.id,
      session.session_id,
    );
    assert(!existingQueueEntry, "Sampled session was already in the queue.");

    const listResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/tracer/trace-session/list_sessions/") &&
        response.status() < 400,
    );
    await page.goto(`${APP_BASE}/dashboard/observe/${project.id}/sessions`, {
      waitUntil: "domcontentloaded",
    });
    await listResponse;

    const rowLabel = session.session_name || session.session_id;
    const sessionRow = page
      .locator(".ag-center-cols-container [role='row']", { hasText: rowLabel })
      .first();
    await sessionRow.waitFor({ state: "visible", timeout: 45000 });

    await sessionRow.locator(".ag-selection-checkbox").click();
    await page.getByText("1 selected").waitFor({ state: "visible", timeout: 15000 });

    await page.getByRole("button", { name: "Actions" }).click();
    await page.getByRole("menuitem", { name: "Add to annotation queue" }).click();
    await page
      .getByPlaceholder("Search queues...")
      .waitFor({ state: "visible", timeout: 15000 });
    await page.getByPlaceholder("Search queues...").fill(queue.name);

    const addResponse = page.waitForResponse(
      (response) =>
        response
          .url()
          .includes(`/model-hub/annotation-queues/${queue.id}/items/add-items/`) &&
        response.status() < 400,
    );
    await page.getByRole("menuitem", { name: queue.name }).click();
    await addResponse;

    await page
      .getByText(new RegExp(`Session added to ${escapeRegExp(queue.name)}`))
      .waitFor({ state: "visible", timeout: 15000 });

    const queueItem = await poll(
      async () =>
        findQueueItemForSession(auth.client, queue.id, session.session_id),
      { timeoutMs: 30000 },
    );
    createdQueueItemId = queueItem.id;
    const queueEntry = await poll(
      async () =>
        findQueueEntryForSource(auth.client, queue.id, session.session_id),
      { timeoutMs: 30000 },
    );
    evidence.queue_item_id = createdQueueItemId;
    evidence.for_source_item_id = queueEntry.item?.id || null;

    await page.screenshot({
      path: "/tmp/observe-session-add-to-queue-smoke.png",
      fullPage: true,
    });
    evidence.screenshot = "/tmp/observe-session-add-to-queue-smoke.png";

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
    if (createdQueueItemId) {
      await auth.client.delete(
        queuePath("/model-hub/annotation-queues/{queue_id}/items/{id}/", queue.id, {
          id: createdQueueItemId,
        }),
        { okStatuses: [200, 204, 404] },
      );
    }
    await context.close();
    await browser.close();
  }
}

async function resolveObserveSession(client, queueId) {
  const preferredProjectId = process.env.OBSERVE_PROJECT_ID;
  const projects = preferredProjectId
    ? [{ id: preferredProjectId }]
    : asArray(
        await client.get(apiPath("/tracer/project/list_projects/"), {
          query: { page_number: 0, page_size: 25 },
        }),
      );

  for (const project of projects) {
    if (!project?.id) continue;
    const payload = await client.get(apiPath("/tracer/trace-session/list_sessions/"), {
      query: {
        project_id: project.id,
        page_number: 0,
        page_size: 30,
        filters: JSON.stringify([]),
      },
    });
    const sessions = asArray(payload).filter((row) => row?.session_id);
    for (const session of sessions) {
      const queueItem = await findQueueItemForSession(
        client,
        queueId,
        session.session_id,
      );
      if (!queueItem) {
        return { project, session, baseSessionCount: sessions.length };
      }
    }
  }
  throw new Error("No unqueued observe session was found for browser smoke.");
}

async function resolveVisibleQueue(client) {
  if (process.env.ANNOTATION_QUEUE_ID) {
    return client.get(
      apiPath("/model-hub/annotation-queues/{id}/", {
        id: process.env.ANNOTATION_QUEUE_ID,
      }),
    );
  }

  const queues = asArray(
    await client.get(apiPath("/model-hub/annotation-queues/"), {
      query: { limit: 100 },
    }),
  ).filter((queue) => queue?.id && queue.status !== "completed");
  const queue =
    queues.find((item) => item.status === "active" && item.item_count > 0) ||
    queues.find((item) => item.status === "active") ||
    queues[0];
  if (!queue?.id) {
    throw new Error("No non-completed annotation queue exists for browser smoke.");
  }
  return queue;
}

async function findQueueEntryForSource(client, queueId, sessionId) {
  const payload = await client.get(apiPath("/model-hub/annotation-queues/for-source/"), {
    query: {
      source_type: "trace_session",
      source_id: sessionId,
    },
  });
  return (
    asArray(payload).find(
      (item) =>
        String(item?.queue?.id || item?.queue_id || item?.queue || "") ===
        String(queueId),
    ) || null
  );
}

async function findQueueItemForSession(client, queueId, sessionId) {
  const payload = await client.get(
    queuePath("/model-hub/annotation-queues/{queue_id}/items/", queueId),
    { query: { limit: 100, source_type: "trace_session" } },
  );
  return (
    asArray(payload).find(
      (item) => String(item?.source_preview?.session_id || "") === String(sessionId),
    ) || null
  );
}

async function poll(fn, { timeoutMs = 10000, intervalMs = 250 } = {}) {
  const start = Date.now();
  let lastValue = null;
  while (Date.now() - start < timeoutMs) {
    lastValue = await fn();
    if (lastValue) return lastValue;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`Timed out waiting for condition. Last value: ${lastValue}`);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
