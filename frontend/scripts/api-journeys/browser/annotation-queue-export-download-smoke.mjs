/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { createRequire } from "node:module";
import { mkdtemp, readFile, readdir, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
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
const SCREENSHOT_PATH =
  process.env.ANNOTATION_QUEUE_EXPORT_SCREENSHOT ||
  "/tmp/annotation-queue-export-download-smoke.png";
const FAILURE_SCREENSHOT_PATH = SCREENSHOT_PATH.replace(
  /\.png$/,
  "-failure.png",
);
const OVERALL_TIMEOUT_MS = Number(
  process.env.ANNOTATION_QUEUE_EXPORT_TIMEOUT_MS || 120_000,
);
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  let currentStage = "starting";
  const setStage = (stage) => {
    currentStage = stage;
    console.error(`[annotation-queue-export] ${stage}`);
  };
  const watchdog = setTimeout(() => {
    console.error(
      JSON.stringify(
        {
          status: "timed_out",
          stage: currentStage,
          timeout_ms: OVERALL_TIMEOUT_MS,
          app_base: APP_BASE,
        },
        null,
        2,
      ),
    );
    process.exit(124);
  }, OVERALL_TIMEOUT_MS);
  watchdog.unref?.();

  setStage("auth");
  const auth = await createAuthenticatedContext();
  const apiFailures = [];
  const pageErrors = [];
  const modelHubRequests = [];
  const browserMutations = [];
  let browser = null;
  let page = null;
  let downloadDir = null;
  let caughtError = null;

  try {
    setStage("select export queue");
    const selection = await selectExportQueue(auth);
    downloadDir = await mkdtemp(path.join(tmpdir(), "aq-export-download-"));

    setStage("launch browser");
    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });
    page = await browser.newPage();
    page.setDefaultTimeout(45_000);
    page.setDefaultNavigationTimeout(45_000);
    await allowDownloads(page, downloadDir);
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installBrowserState(page, auth);

    page.on("request", (request) => {
      if (!isAnnotationQueueApiUrl(request.url())) return;
      modelHubRequests.push(`${request.method()} ${request.url()}`);
      if (MUTATION_METHODS.has(request.method())) {
        browserMutations.push(`${request.method()} ${request.url()}`);
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isAnnotationQueueApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    setStage("load queue detail");
    await waitForResponseDuring(
      page,
      "annotation queue progress load",
      (response) =>
        isQueueMetricResponse(response, selection.queue.id, "progress"),
      () =>
        page.goto(
          `${APP_BASE}/dashboard/annotations/queues/${selection.queue.id}`,
          { waitUntil: "domcontentloaded" },
        ),
    );
    await waitForPathIncludes(
      page,
      `/dashboard/annotations/queues/${selection.queue.id}`,
    );
    await waitForVisibleText(page, selection.queue.name, { exact: true });

    setStage("open analytics tab");
    await waitForResponseDuring(
      page,
      "annotation queue analytics tab",
      (response) =>
        isQueueMetricResponse(response, selection.queue.id, "analytics"),
      () => clickTab(page, "Analytics"),
    );
    await waitForVisibleText(page, "Export JSON", { exact: true });
    await waitForVisibleText(page, "Export CSV", { exact: true });

    setStage("download JSON export");
    const jsonResponse = await waitForResponseDuring(
      page,
      "annotation queue JSON export",
      (response) => isQueueExportResponse(response, selection.queue.id, "json"),
      () => clickButtonWithText(page, "Export JSON"),
    );
    const jsonResponseText = await jsonResponse.text();
    const jsonDownload = await waitForDownloadedFile(
      downloadDir,
      "queue-export.json",
    );
    assertJsonExportMatches(jsonResponseText, jsonDownload.text);

    setStage("download CSV export");
    const csvResponse = await waitForResponseDuring(
      page,
      "annotation queue CSV export",
      (response) => isQueueExportResponse(response, selection.queue.id, "csv"),
      () => clickButtonWithText(page, "Export CSV"),
    );
    const csvResponseText = await csvResponse.text();
    const csvDownload = await waitForDownloadedFile(
      downloadDir,
      "queue-export.csv",
    );
    assertCsvExportMatches(csvResponseText, csvDownload.text);

    setStage("capture evidence");
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      browserMutations.length === 0,
      `Unexpected browser mutations: ${browserMutations
        .map(maskRequest)
        .join(", ")}`,
    );
    assert(
      apiFailures.length === 0,
      `Unexpected annotation queue API failures: ${apiFailures.join(", ")}`,
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
          queue_id: selection.queue.id,
          queue_name: selection.queue.name,
          preflight: selection.preflight,
          downloads: {
            json: {
              path: jsonDownload.path,
              bytes: jsonDownload.bytes,
              rows: extractJsonRows(jsonDownload.text).length,
            },
            csv: {
              path: csvDownload.path,
              bytes: csvDownload.bytes,
              header: csvDownload.text.split(/\r?\n/)[0],
            },
          },
          model_hub_request_count: modelHubRequests.length,
          browser_mutations: browserMutations.map(maskRequest),
          screenshot: SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    caughtError = error;
    const domDebug = page
      ? await page
          .evaluate(() => ({
            url: window.location.href,
            text: document.body?.innerText?.slice(0, 2500) || "",
          }))
          .catch(() => null)
      : null;
    if (page) {
      await page
        .screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
    }
    console.error(
      JSON.stringify(
        {
          status: "failed",
          error: error.message,
          dom: domDebug,
          api_failures: apiFailures.map(maskRequest),
          browser_mutations: browserMutations.map(maskRequest),
          screenshot: FAILURE_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
  } finally {
    clearTimeout(watchdog);
    if (browser) await browser.close();
    if (downloadDir && process.env.KEEP_EXPORT_DOWNLOADS !== "1") {
      await rm(downloadDir, { recursive: true, force: true }).catch(() => null);
    }
  }

  if (caughtError) throw caughtError;
}

async function selectExportQueue(auth) {
  const configuredQueueId =
    process.env.ANNOTATION_EXPORT_QUEUE_ID ||
    process.env.ANNOTATION_METRICS_QUEUE_ID;
  const candidates = configuredQueueId
    ? [
        {
          queue_id: configuredQueueId,
          queue_name: "configured",
          completed_items: null,
          active_items: null,
          active_scores: null,
        },
      ]
    : await loadExportQueueCandidatesDb({
        organizationId: auth.organizationId,
        workspaceId: auth.workspaceId,
      });
  assert(candidates.length > 0, "No exportable annotation queue was found.");

  const failures = [];
  for (const candidate of candidates) {
    try {
      console.error(
        `[annotation-queue-export] preflight queue ${candidate.queue_id} (${candidate.completed_items ?? "?"} completed, ${candidate.active_items ?? "?"} active)`,
      );
      const queue = await auth.client.get(
        apiPath("/model-hub/annotation-queues/{id}/", {
          id: candidate.queue_id,
        }),
      );
      const jsonExport = await auth.client.get(
        apiPath("/model-hub/annotation-queues/{id}/export/", { id: queue.id }),
        { query: { export_format: "json" } },
      );
      const csvExport = await auth.client.get(
        apiPath("/model-hub/annotation-queues/{id}/export/", { id: queue.id }),
        { query: { export_format: "csv" } },
      );
      const jsonRows = asArray(jsonExport);
      assert(jsonRows.length > 0, `Queue ${queue.id} returned no JSON rows.`);
      assert(
        typeof csvExport === "string" && csvExport.includes("item_id"),
        `Queue ${queue.id} returned invalid CSV export.`,
      );
      return {
        candidate,
        queue,
        preflight: {
          json_rows: jsonRows.length,
          csv_bytes: csvExport.length,
          first_source_type: jsonRows[0]?.source_type || null,
          first_status: jsonRows[0]?.status || null,
        },
      };
    } catch (error) {
      failures.push({
        queue_id: candidate.queue_id,
        error: error.message,
        status: error.status || null,
      });
    }
  }

  throw new Error(
    `No accessible queue exported JSON and CSV: ${JSON.stringify(failures)}`,
  );
}

async function loadExportQueueCandidatesDb({ organizationId, workspaceId }) {
  const sql = `
WITH score_counts AS (
  SELECT qi.queue_id, COUNT(s.id) AS active_scores
  FROM model_hub_queueitem qi
  JOIN model_hub_score s ON s.queue_item_id = qi.id
  WHERE qi.deleted = FALSE
    AND s.deleted = FALSE
  GROUP BY qi.queue_id
),
ranked AS (
  SELECT
    q.id::text AS queue_id,
    q.name AS queue_name,
    COUNT(DISTINCT qi.id) FILTER (WHERE qi.deleted = FALSE AND qi.status = 'completed') AS completed_items,
    COUNT(DISTINCT qi.id) FILTER (WHERE qi.deleted = FALSE) AS active_items,
    COALESCE(sc.active_scores, 0) AS active_scores
  FROM model_hub_annotationqueue q
  LEFT JOIN model_hub_queueitem qi ON qi.queue_id = q.id
  LEFT JOIN score_counts sc ON sc.queue_id = q.id
  WHERE q.deleted = FALSE
    AND q.organization_id = ${sqlUuid(organizationId)}
    AND q.workspace_id = ${sqlUuid(workspaceId)}
  GROUP BY q.id, q.name, sc.active_scores
  HAVING COUNT(DISTINCT qi.id) FILTER (WHERE qi.deleted = FALSE AND qi.status = 'completed') > 0
  ORDER BY
    CASE
      WHEN COUNT(DISTINCT qi.id) FILTER (WHERE qi.deleted = FALSE) BETWEEN 1 AND 25
      THEN 0
      ELSE 1
    END,
    COUNT(DISTINCT qi.id) FILTER (WHERE qi.deleted = FALSE) ASC,
    COUNT(DISTINCT qi.id) FILTER (WHERE qi.deleted = FALSE AND qi.status = 'completed') DESC,
    COALESCE(sc.active_scores, 0) DESC
  LIMIT 10
)
SELECT COALESCE(json_agg(row_to_json(ranked)), '[]'::json)::text FROM ranked;
`;
  return asArray(await runPostgresJson(sql));
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
  assert(text, "Postgres export queue audit returned no JSON output.");
  return JSON.parse(text);
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
    window.normalizeText = (value) =>
      String(value || "")
        .replace(/\s+/g, " ")
        .trim();
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
      if (organizationId)
        sessionStorage.setItem("organizationId", organizationId);
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id) {
        sessionStorage.setItem("futureagi-current-user-id", user.id);
        sessionStorage.setItem("currentUserId", user.id);
      }
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );
}

async function allowDownloads(page, downloadPath) {
  const session = await page.target().createCDPSession();
  try {
    await session.send("Browser.setDownloadBehavior", {
      behavior: "allow",
      downloadPath,
      eventsEnabled: true,
    });
  } catch {
    await session.send("Page.setDownloadBehavior", {
      behavior: "allow",
      downloadPath,
    });
  }
}

async function waitForDownloadedFile(downloadDir, fileName, timeout = 30000) {
  const target = path.join(downloadDir, fileName);
  const started = Date.now();
  while (Date.now() - started < timeout) {
    const files = await readdir(downloadDir).catch(() => []);
    if (!files.some((file) => file.endsWith(".crdownload"))) {
      const found = files.find((file) => file === fileName);
      if (found) {
        const fileStat = await stat(target);
        if (fileStat.size > 0) {
          const text = await readFile(target, "utf8");
          return { path: target, bytes: fileStat.size, text };
        }
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Timed out waiting for browser download ${fileName}.`);
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

async function waitForPathIncludes(page, pathName, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname.includes(expectedPath),
    { timeout },
    pathName,
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

async function clickTab(page, label) {
  await waitForVisibleText(page, label, { exact: true });
  const clicked = await page.evaluate((expectedLabel) => {
    const tab = window
      .visibleElements('button[role="tab"]')
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedLabel &&
          !candidate.disabled,
      );
    if (!tab) return false;
    tab.click();
    return true;
  }, label);
  assert(clicked, `Could not click tab: ${label}`);
}

async function clickButtonWithText(page, text) {
  await waitForVisibleText(page, text, { exact: true });
  const clicked = await page.evaluate((expectedText) => {
    const button = window
      .visibleElements("button")
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedText &&
          !candidate.disabled,
      );
    if (!button) return false;
    button.click();
    return true;
  }, text);
  assert(clicked, `Could not click button: ${text}`);
}

function assertJsonExportMatches(responseText, downloadText) {
  assert(
    responseText === downloadText,
    "Downloaded JSON did not match the export response body.",
  );
  const rows = extractJsonRows(downloadText);
  assert(rows.length > 0, "Downloaded JSON export had no rows.");
  const first = rows[0];
  assert(first.item_id, "Downloaded JSON row missing item_id.");
  assert(first.source_type, "Downloaded JSON row missing source_type.");
  assert(
    first.annotations !== undefined,
    "Downloaded JSON row missing annotations.",
  );
}

function assertCsvExportMatches(responseText, downloadText) {
  assert(
    responseText === downloadText,
    "Downloaded CSV did not match the export response body.",
  );
  const [header, firstRow] = downloadText.split(/\r?\n/);
  const requiredHeaders = [
    "item_id",
    "source_type",
    "status",
    "label_id",
    "label_name",
    "value",
  ];
  for (const column of requiredHeaders) {
    assert(header.includes(column), `Downloaded CSV missing ${column} header.`);
  }
  assert(firstRow && firstRow.length > 0, "Downloaded CSV had no data rows.");
}

function extractJsonRows(text) {
  const parsed = JSON.parse(text);
  return asArray(parsed?.result ?? parsed);
}

function isQueueMetricResponse(response, queueId, metricName) {
  if (response.request().method() !== "GET") return false;
  const url = new URL(response.url());
  if (!isAnnotationQueueApiUrl(response.url())) return false;
  if (response.status() >= 400) return false;
  return (
    url.pathname === `/model-hub/annotation-queues/${queueId}/${metricName}/`
  );
}

function isQueueExportResponse(response, queueId, format) {
  if (response.request().method() !== "GET") return false;
  const url = new URL(response.url());
  if (!isAnnotationQueueApiUrl(response.url())) return false;
  if (response.status() >= 400) return false;
  return (
    url.pathname === `/model-hub/annotation-queues/${queueId}/export/` &&
    url.searchParams.get("export_format") === format
  );
}

function isAnnotationQueueApiUrl(rawUrl) {
  const url = new URL(rawUrl);
  return (
    url.origin ===
      new URL(process.env.API_BASE || "http://localhost:8003").origin &&
    url.pathname.startsWith("/model-hub/annotation-queues/")
  );
}

function maskRequest(rawRequest) {
  const [method, rawUrl] = rawRequest.split(" ");
  const url = new URL(rawUrl);
  return `${method} ${url.pathname}`;
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
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
  process.exitCode = 1;
});
