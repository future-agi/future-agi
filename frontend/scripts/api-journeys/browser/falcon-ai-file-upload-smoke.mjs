/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  assert,
  createAuthenticatedContext,
  currentUserId,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.FALCON_AI_FILE_SCREENSHOT ||
  "/tmp/falcon-ai-file-upload-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  process.env.FALCON_AI_FILE_FAILURE_SCREENSHOT ||
  "/tmp/falcon-ai-file-upload-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const userId = currentUserId(auth.user);
  assert(isUuid(userId), "Falcon file upload smoke requires current user id.");

  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").slice(0, 12);
  const marker = `falcon-ui-${suffix}`;
  const fileName = `${marker}.txt`;
  const fileText = `Falcon upload text ${auth.runId}`;
  const tmpDir = await mkdtemp(join(tmpdir(), "falcon-file-smoke-"));
  const filePath = join(tmpDir, fileName);

  let browser = null;
  let uploadedFileId = null;
  let storageKeys = [];
  let caughtError = null;
  const apiFailures = [];
  const pageErrors = [];
  const falconRequests = [];
  const evidence = { file_name: fileName };

  try {
    await writeFile(filePath, fileText);
    await hardDeleteFalconFileFixtures({
      marker,
      organizationId: auth.organizationId,
    });

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
      "Falcon page list load",
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
    await page.waitForSelector('button[title="Attach file"]', {
      visible: true,
      timeout: 30000,
    });

    const uploadResponsePromise = page.waitForResponse(
      (response) =>
        response.url().includes("/falcon-ai/files/upload/") &&
        response.request().method() === "POST" &&
        response.status() < 400,
      { timeout: 60000 },
    );
    const input = await page.waitForSelector('input[type="file"]', {
      timeout: 30000,
    });
    await input.uploadFile(filePath);
    const uploadResponse = await uploadResponsePromise;
    const uploaded = unwrapApiEnvelope(await uploadResponse.json());
    uploadedFileId = uploaded?.id;
    assert(
      isUuid(uploadedFileId),
      "Falcon browser upload returned no file id.",
    );
    assert(
      uploaded.name === fileName &&
        uploaded.content_type === "text/plain" &&
        uploaded.size === fileText.length,
      `Falcon browser upload response mismatch: ${JSON.stringify(uploaded)}`,
    );
    evidence.uploaded_file_id = uploadedFileId;
    evidence.upload_response = {
      name: uploaded.name,
      content_type: uploaded.content_type,
      size: uploaded.size,
    };

    await waitForVisibleText(page, fileName);
    await waitForVisibleText(page, `${fileText.length} B`);
    await waitForNoVisibleText(page, "Invalid Date");
    await waitForNoVisibleText(page, "undefined");

    const dbAudit = await loadFalconFileDbAudit({
      fileIds: [uploadedFileId],
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      userId,
      marker,
    });
    assert(
      dbAudit.file_count === 1 &&
        dbAudit.active_workspace_file_count === 1 &&
        dbAudit.user_file_count === 1 &&
        dbAudit.text_content_match_count === 1 &&
        dbAudit.storage_key_count === 1,
      `Falcon browser file upload DB audit mismatch: ${JSON.stringify(dbAudit)}`,
    );
    storageKeys = dbAudit.storage_keys || [];
    await assertFalconMinioObjectsExist(storageKeys);
    evidence.db_audit = dbAudit;
    evidence.storage_object_count = storageKeys.length;

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    const cleanup = await hardDeleteFalconFileFixtures({
      marker,
      organizationId: auth.organizationId,
    });
    assert(
      cleanup.remaining_file_count === 0,
      `Falcon file hard cleanup left DB rows behind: ${JSON.stringify(cleanup)}`,
    );
    await assertFalconMinioObjectsAbsent(storageKeys);
    storageKeys = [];
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
    await hardDeleteFalconFileFixtures({
      marker,
      organizationId: auth.organizationId,
    }).catch((error) => {
      caughtError = appendCleanupError(caughtError, error);
    });
    if (storageKeys.length) {
      await removeFalconMinioObjects(storageKeys).catch((error) => {
        caughtError = appendCleanupError(caughtError, error);
      });
    }
    await rm(tmpDir, { recursive: true, force: true }).catch(() => null);
  }

  if (caughtError) throw caughtError;
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

function unwrapApiEnvelope(value) {
  return value?.result || value?.data?.result || value?.results || value;
}

function isFalconApiUrl(url) {
  return url.includes("/falcon-ai/");
}

async function loadFalconFileDbAudit({
  fileIds,
  organizationId,
  workspaceId,
  userId,
  marker,
}) {
  const sql = `
WITH requested_files AS (
  SELECT unnest(${sqlUuidArray(fileIds)}) AS file_id
),
file_rows AS (
  SELECT file.*
  FROM falcon_ai_falconfile file
  JOIN requested_files requested
    ON file.id = requested.file_id
  WHERE file.organization_id = ${sqlUuid(organizationId)}
    AND file.name LIKE ${sqlTextLiteral(`${marker}%`)}
)
SELECT json_build_object(
  'file_count', (SELECT count(*) FROM file_rows),
  'active_workspace_file_count',
    (SELECT count(*) FROM file_rows WHERE workspace_id = ${sqlUuid(workspaceId)}),
  'user_file_count',
    (SELECT count(*) FROM file_rows WHERE user_id = ${sqlUuid(userId)}),
  'text_content_match_count',
    (SELECT count(*) FROM file_rows WHERE text_content LIKE '%Falcon upload text%'),
  'storage_key_count',
    (SELECT count(*) FROM file_rows WHERE storage_key LIKE 'falcon-ai/%'),
  'storage_keys',
    COALESCE((SELECT json_agg(storage_key ORDER BY created_at) FROM file_rows), '[]'::json)
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteFalconFileFixtures({ marker, organizationId }) {
  const sql = `
WITH target_files AS (
  SELECT id, storage_key
  FROM falcon_ai_falconfile
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlTextLiteral(`${marker}%`)}
),
deleted_files AS (
  DELETE FROM falcon_ai_falconfile file
  USING target_files target
  WHERE file.id = target.id
  RETURNING file.id
)
SELECT json_build_object(
  'deleted_file_count', (SELECT count(*) FROM deleted_files),
  'remaining_file_count',
    (SELECT count(*) FROM target_files) - (SELECT count(*) FROM deleted_files),
  'storage_keys',
    COALESCE((SELECT json_agg(storage_key) FROM target_files), '[]'::json)
);
`;
  const audit = await runPostgresJson(sql);
  await removeFalconMinioObjects(audit.storage_keys || []);
  return audit;
}

async function assertFalconMinioObjectsExist(storageKeys) {
  for (const storageKey of storageKeys || []) {
    await runFalconMinioCommand(["stat", falconMinioTarget(storageKey)]);
  }
}

async function assertFalconMinioObjectsAbsent(storageKeys) {
  for (const storageKey of storageKeys || []) {
    try {
      await runFalconMinioCommand(["stat", falconMinioTarget(storageKey)]);
    } catch {
      continue;
    }
    throw new Error(
      `Falcon MinIO object still exists after cleanup: ${storageKey}`,
    );
  }
}

async function removeFalconMinioObjects(storageKeys) {
  for (const storageKey of storageKeys || []) {
    const target = falconMinioTarget(storageKey);
    try {
      await runFalconMinioCommand(["rm", "--force", target]);
    } catch (error) {
      const stderr = String(error?.stderr || "");
      if (
        stderr.includes("Object does not exist") ||
        stderr.includes("Unable to stat")
      ) {
        continue;
      }
      throw error;
    }
  }
}

async function runFalconMinioCommand(args) {
  const container =
    process.env.API_JOURNEY_MINIO_CONTAINER || "futureagi-ws2-minio-1";
  const command = [
    'mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null',
    `mc ${args.map((arg) => shellQuote(arg)).join(" ")}`,
  ].join(" && ");
  await execFileAsync("docker", ["exec", container, "sh", "-lc", command], {
    maxBuffer: 5 * 1024 * 1024,
  });
}

function falconMinioTarget(storageKey) {
  const bucket = process.env.API_JOURNEY_MINIO_BUCKET || "fi-content";
  return `local/${bucket}/${storageKey}`;
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

function sqlUuidArray(values) {
  const rows = values || [];
  assert(rows.length > 0, "SQL UUID array cannot be empty.");
  return `ARRAY[${rows.map((value) => sqlUuid(value)).join(", ")}]::uuid[]`;
}

function sqlTextLiteral(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function shellQuote(value) {
  return `'${String(value).replaceAll("'", "'\"'\"'")}'`;
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
