/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.FALCON_AI_SCREENSHOT || "/tmp/falcon-ai-conversation-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  process.env.FALCON_AI_FAILURE_SCREENSHOT ||
  "/tmp/falcon-ai-conversation-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9-]/gi, "-").slice(0, 24);
  const title = `ui falcon conversation ${suffix}`;
  const userMessage = `Falcon browser user prompt ${auth.runId}`;
  const assistantMessage = `Falcon browser assistant response ${auth.runId}`;

  await hardDeleteFalconConversationFixtures({
    titlePrefix: title,
    organizationId: auth.organizationId,
  });

  let conversationId = null;
  let browser = null;
  let caughtError = null;
  const apiFailures = [];
  const pageErrors = [];
  const falconRequests = [];
  const evidence = { title };

  try {
    const created = await auth.client.post(
      apiPath("/falcon-ai/conversations/"),
      {
        title,
        context_page: "api-ui-e2e-coverage",
      },
    );
    conversationId = created.id;
    assert(
      isUuid(conversationId),
      "Falcon conversation create did not return a UUID id.",
    );

    const seed = await seedFalconConversationMessages({
      conversationId,
      userMessage,
      assistantMessage,
    });
    assert(
      seed.inserted_message_count === 2,
      "Falcon conversation message seed did not insert two messages.",
    );
    evidence.conversation_id = conversationId;
    evidence.seeded_message_count = seed.inserted_message_count;

    const searchRows = await auth.client.get(
      apiPath("/falcon-ai/conversations/"),
      {
        query: { search: title, limit: 10, offset: 0 },
      },
    );
    assert(
      Array.isArray(searchRows) &&
        searchRows.some((conversation) => conversation.id === conversationId),
      "Falcon conversation search API did not return the disposable row.",
    );
    const detail = await auth.client.get(
      apiPath("/falcon-ai/conversations/{conversation_id}/", {
        conversation_id: conversationId,
      }),
    );
    assert(
      Array.isArray(detail.messages) && detail.messages.length === 2,
      "Falcon conversation detail API did not return seeded messages.",
    );
    await auth.client.get(
      `${apiPath("/falcon-ai/conversations/{conversation_id}/", {
        conversation_id: conversationId,
      })}stream-status/`,
    );

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });
    const page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installAuthState(page, auth);

    page.on("request", (request) => {
      if (isFalconApiUrl(request.url())) {
        falconRequests.push(`${request.method()} ${request.url()}`);
      }
    });
    page.on("response", (response) => {
      if (isFalconApiUrl(response.url()) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${response.url()}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "Falcon list load",
      (response) =>
        response.url().includes("/falcon-ai/conversations/") &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/falcon-ai`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForVisibleText(page, "Falcon AI", { exact: true });
    await waitForVisibleText(page, "New chat", { exact: true });
    await waitForVisibleText(page, "Customize", { exact: true });

    await waitForResponseDuring(
      page,
      "Falcon search",
      (response) => {
        if (
          !response.url().includes("/falcon-ai/conversations/") ||
          response.request().method() !== "GET" ||
          response.status() >= 400
        ) {
          return false;
        }
        const url = new URL(response.url());
        return url.searchParams.get("search") === title;
      },
      () => typeSearch(page, title),
    );
    await waitForVisibleText(page, title, { exact: true });

    await waitForResponseDuring(
      page,
      "Falcon conversation detail",
      (response) =>
        response
          .url()
          .includes(`/falcon-ai/conversations/${conversationId}/`) &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () => clickDeepestVisibleText(page, title),
    );
    await waitForPath(page, `/dashboard/falcon-ai/${conversationId}`);
    await waitForVisibleText(page, title, { exact: true });
    await waitForVisibleText(page, userMessage);
    await waitForVisibleText(page, assistantMessage);
    await waitForNoVisibleText(page, "Invalid Date");
    await waitForNoVisibleText(page, "undefined");
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    const deleted = await auth.client.delete(
      apiPath("/falcon-ai/conversations/{conversation_id}/", {
        conversation_id: conversationId,
      }),
    );
    evidence.public_delete_status = deleted?.status ?? true;
    const postDeleteSearch = await auth.client.get(
      apiPath("/falcon-ai/conversations/"),
      {
        query: { search: title, limit: 10, offset: 0 },
      },
    );
    assert(
      Array.isArray(postDeleteSearch) &&
        !postDeleteSearch.some(
          (conversation) => conversation.id === conversationId,
        ),
      "Falcon conversation remained visible after public delete.",
    );

    const cleanup = await hardDeleteFalconConversationFixtures({
      titlePrefix: title,
      organizationId: auth.organizationId,
    });
    assert(
      cleanup.remaining_conversation_count === 0 &&
        cleanup.remaining_message_count === 0,
      "Falcon conversation fixture residue remained after cleanup.",
    );
    evidence.cleanup = cleanup;

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
          falcon_request_count: falconRequests.length,
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
          page_errors: pageErrors,
          falcon_requests: falconRequests,
        },
        null,
        2,
      ),
    );
    if (browser) {
      const pages = await browser.pages();
      await pages
        .at(-1)
        ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
    }
  } finally {
    if (browser) await browser.close();
    await hardDeleteFalconConversationFixtures({
      titlePrefix: title,
      organizationId: auth.organizationId,
    }).catch((error) => {
      caughtError = appendCleanupError(caughtError, error);
    });
  }

  if (caughtError) throw caughtError;
}

async function seedFalconConversationMessages({
  conversationId,
  userMessage,
  assistantMessage,
}) {
  const userMessageId = randomUUID();
  const assistantMessageId = randomUUID();
  const sql = `
WITH inserted AS (
  INSERT INTO falcon_ai_message (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    role,
    content,
    thoughts,
    tool_calls,
    completion_card,
    feedback,
    token_count,
    model_used,
    latency_ms,
    conversation_id,
    input_tokens,
    output_tokens,
    files
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(userMessageId)},
      'user',
      ${sqlTextLiteral(userMessage)},
      '[]'::jsonb,
      '[]'::jsonb,
      NULL,
      '',
      7,
      '',
      0,
      ${sqlUuid(conversationId)},
      7,
      0,
      '[]'::jsonb
    ),
    (
      now() + interval '1 second',
      now() + interval '1 second',
      false,
      NULL,
      ${sqlUuid(assistantMessageId)},
      'assistant',
      ${sqlTextLiteral(assistantMessage)},
      '[]'::jsonb,
      '[]'::jsonb,
      NULL,
      '',
      11,
      'browser-smoke-model',
      321,
      ${sqlUuid(conversationId)},
      7,
      11,
      '[]'::jsonb
    )
  RETURNING id
),
updated_conversation AS (
  UPDATE falcon_ai_conversation
  SET updated_at = now(), total_tokens = 18
  WHERE id = ${sqlUuid(conversationId)}
  RETURNING id
)
SELECT json_build_object(
  'inserted_message_count', (SELECT count(*) FROM inserted),
  'updated_conversation_count', (SELECT count(*) FROM updated_conversation),
  'user_message_id', ${sqlTextLiteral(userMessageId)},
  'assistant_message_id', ${sqlTextLiteral(assistantMessageId)}
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteFalconConversationFixtures({
  titlePrefix,
  organizationId,
}) {
  const deleted = await runPostgresJson(`
WITH requested AS (
  SELECT
    ${sqlTextLiteral(titlePrefix)} AS title_prefix,
    ${sqlUuid(organizationId)} AS organization_id
),
target_conversations AS (
  SELECT conversation.id
  FROM falcon_ai_conversation conversation, requested r
  WHERE conversation.organization_id = r.organization_id
    AND conversation.title LIKE r.title_prefix || '%'
),
deleted_usage AS (
  DELETE FROM falcon_ai_falconusage usage
  USING target_conversations target
  WHERE usage.conversation_id = target.id
  RETURNING usage.id
),
deleted_messages AS (
  DELETE FROM falcon_ai_message message
  USING target_conversations target
  WHERE message.conversation_id = target.id
  RETURNING message.id
),
deleted_files AS (
  DELETE FROM falcon_ai_falconfile file
  USING target_conversations target
  WHERE file.conversation_id = target.id
  RETURNING file.id
),
deleted_conversations AS (
  DELETE FROM falcon_ai_conversation conversation
  USING target_conversations target
  WHERE conversation.id = target.id
  RETURNING conversation.id
)
SELECT json_build_object(
  'deleted_usage_count', (SELECT count(*) FROM deleted_usage),
  'deleted_message_count', (SELECT count(*) FROM deleted_messages),
  'deleted_file_count', (SELECT count(*) FROM deleted_files),
  'deleted_conversation_count', (SELECT count(*) FROM deleted_conversations)
);
`);
  const residue = await runPostgresJson(`
WITH requested AS (
  SELECT
    ${sqlTextLiteral(titlePrefix)} AS title_prefix,
    ${sqlUuid(organizationId)} AS organization_id
)
SELECT json_build_object(
  'remaining_conversation_count', (
    SELECT count(*)
    FROM falcon_ai_conversation conversation, requested r
    WHERE conversation.organization_id = r.organization_id
      AND conversation.title LIKE r.title_prefix || '%'
  ),
  'remaining_message_count', (
    SELECT count(*)
    FROM falcon_ai_message message
    JOIN falcon_ai_conversation conversation
      ON conversation.id = message.conversation_id
    CROSS JOIN requested r
    WHERE conversation.organization_id = r.organization_id
      AND conversation.title LIKE r.title_prefix || '%'
  )
);
`);
  return { ...deleted, ...residue };
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

async function waitForResponseDuring(page, label, predicate, action) {
  const waiter = page.waitForResponse(predicate, { timeout: 60000 });
  await action();
  try {
    return await waiter;
  } catch (error) {
    throw new Error(
      `${label} did not observe expected response: ${error.message}`,
    );
  }
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
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
        const textContent = normalized(element.textContent);
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function waitForNoVisibleText(page, text, { timeout = 5000 } = {}) {
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
      return !Array.from(document.querySelectorAll("body *")).some(
        (element) =>
          isVisible(element) &&
          String(element.textContent || "").includes(expectedText),
      );
    },
    { timeout },
    text,
  );
}

async function typeSearch(page, value) {
  const selector = 'input[placeholder="Search chats..."]';
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector, { clickCount: 3 });
  await page.type(selector, value, { delay: 2 });
}

async function clickDeepestVisibleText(page, text) {
  await waitForVisibleText(page, text);
  await page.evaluate((expectedText) => {
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
    const containsText = (element) =>
      String(element.textContent || "").includes(expectedText);
    const candidates = Array.from(document.querySelectorAll("body *"))
      .filter((element) => isVisible(element) && containsText(element))
      .filter(
        (element) =>
          !Array.from(element.children).some(
            (child) => isVisible(child) && containsText(child),
          ),
      )
      .sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return ar.width * ar.height - br.width * br.height;
      });
    const textElement = candidates[0];
    let target = textElement;
    while (target && target !== document.body) {
      const style = window.getComputedStyle(target);
      if (
        style.cursor === "pointer" ||
        target.tagName === "BUTTON" ||
        target.tagName === "A" ||
        target.getAttribute("role") === "button"
      ) {
        break;
      }
      target = target.parentElement;
    }
    (target || textElement)?.click();
  }, text);
}

async function waitForPath(page, expectedPath) {
  await page.waitForFunction(
    (path) => window.location.pathname === path,
    { timeout: 30000 },
    expectedPath,
  );
}

function isFalconApiUrl(url) {
  return url.includes("/falcon-ai/");
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
  assert(text, "Postgres DB command returned no JSON output.");
  return JSON.parse(text);
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlTextLiteral(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function browserExecutablePath() {
  return (
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_BIN ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  );
}

function appendCleanupError(originalError, cleanupError) {
  if (!originalError) return cleanupError;
  originalError.message = `${originalError.message}; cleanup: ${cleanupError.message}`;
  return originalError;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
