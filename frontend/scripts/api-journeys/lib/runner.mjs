/* eslint-disable no-console */
import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import {
  CleanupStack,
  SkipJourney,
  createApiClient,
  createAuthenticatedContext,
} from "./api-client.mjs";

const execFileAsync = promisify(execFile);
const preflightScriptPath = resolvePreflightScriptPath();

function resolvePreflightScriptPath() {
  const cwdRelativePath = path.resolve(
    process.cwd(),
    "scripts/api-journeys/preflight.mjs",
  );

  if (import.meta.url.startsWith("file:")) {
    try {
      return fileURLToPath(new URL("../preflight.mjs", import.meta.url));
    } catch {
      return cwdRelativePath;
    }
  }

  return cwdRelativePath;
}

export async function runJourneys(journeys, argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  if (args.list) {
    for (const journey of journeys) {
      console.log(`${journey.id}\t${journey.title}`);
    }
    return { status: "listed", results: [] };
  }

  const knownJourneyIds = new Set(journeys.map((journey) => journey.id));
  const unknownOnlyIds = [...args.only].filter(
    (id) => !knownJourneyIds.has(id),
  );
  if (unknownOnlyIds.length) {
    const summary = {
      status: "failed",
      api_base: normalizeBaseUrl(
        process.env.API_BASE || "http://localhost:8003",
      ),
      organization_id: process.env.FUTURE_AGI_ORGANIZATION_ID || null,
      workspace_id: process.env.FUTURE_AGI_WORKSPACE_ID || null,
      total: 1,
      passed: 0,
      skipped: 0,
      failed: 1,
      requested_total: args.only.size,
      selected_total: 0,
      results: [
        {
          id: "journey_selection",
          title: "API journey selection",
          status: "failed",
          error: `Unknown --only journey id${unknownOnlyIds.length === 1 ? "" : "s"}: ${unknownOnlyIds.join(", ")}`,
          evidence: [
            {
              requested_ids: [...args.only],
              unknown_ids: unknownOnlyIds,
              available_count: knownJourneyIds.size,
            },
          ],
        },
      ],
    };
    await writeSummary(summary, args);
    process.exitCode = 1;
    return summary;
  }

  const selected = journeys.filter((journey) => {
    if (args.only.size && !args.only.has(journey.id)) return false;
    if (args.grep) {
      const haystack = `${journey.id} ${journey.title} ${journey.tags?.join(" ")}`;
      return haystack.toLowerCase().includes(args.grep.toLowerCase());
    }
    return true;
  });

  if (!selected.length) {
    const summary = {
      status: "passed",
      api_base: normalizeBaseUrl(
        process.env.API_BASE || "http://localhost:8003",
      ),
      organization_id: process.env.FUTURE_AGI_ORGANIZATION_ID || null,
      workspace_id: process.env.FUTURE_AGI_WORKSPACE_ID || null,
      total: 0,
      passed: 0,
      skipped: 0,
      failed: 0,
      selected_total: 0,
      results: [],
    };
    await writeSummary(summary, args);
    return summary;
  }

  if (
    args.requirePublicPreflight &&
    selected.some((journey) => !journey.public)
  ) {
    const summary = {
      status: "failed",
      api_base: normalizeBaseUrl(
        process.env.API_BASE || "http://localhost:8003",
      ),
      organization_id: process.env.FUTURE_AGI_ORGANIZATION_ID || null,
      workspace_id: process.env.FUTURE_AGI_WORKSPACE_ID || null,
      total: 1,
      passed: 0,
      skipped: 0,
      failed: 1,
      selected_total: selected.length,
      results: [
        {
          id: "preflight",
          title: "API journey public preflight",
          status: "failed",
          error:
            "--require-public-preflight can only be used when every selected journey is marked public.",
          evidence: [
            {
              non_public_ids: selected
                .filter((journey) => !journey.public)
                .map((journey) => journey.id),
            },
          ],
        },
      ],
    };
    await writeSummary(summary, args);
    process.exitCode = 1;
    return summary;
  }

  if (args.requirePreflight || args.requirePublicPreflight) {
    const preflight = await runPreflightCheck(args);
    if (preflight.status !== "passed") {
      const summary = {
        status: "failed",
        api_base: normalizeBaseUrl(
          process.env.API_BASE || "http://localhost:8003",
        ),
        organization_id: process.env.FUTURE_AGI_ORGANIZATION_ID || null,
        workspace_id: process.env.FUTURE_AGI_WORKSPACE_ID || null,
        total: 1,
        passed: 0,
        skipped: 0,
        failed: 1,
        selected_total: selected.length,
        results: [
          {
            id: "preflight",
            title: args.requirePublicPreflight
              ? "API journey public preflight"
              : "API journey preflight",
            status: "failed",
            error:
              preflight.error ||
              `${args.requirePublicPreflight ? "API journey public preflight" : "API journey preflight"} failed; route-level journeys were not run.`,
            elapsed_ms: preflight.elapsed_ms,
            evidence: preflight.evidence,
            preflight_json: preflight.jsonPath,
          },
        ],
      };
      await writeSummary(summary, args);
      process.exitCode = 1;
      return summary;
    }
  }

  let baseContext;
  try {
    baseContext = selected.some((journey) => !journey.public)
      ? await createAuthenticatedContext()
      : createPublicJourneyContext();
  } catch (error) {
    const summary = {
      status: "failed",
      api_base: normalizeBaseUrl(
        process.env.API_BASE || "http://localhost:8003",
      ),
      organization_id: process.env.FUTURE_AGI_ORGANIZATION_ID || null,
      workspace_id: process.env.FUTURE_AGI_WORKSPACE_ID || null,
      total: 1,
      passed: 0,
      skipped: 0,
      failed: 1,
      selected_total: selected.length,
      results: [
        {
          id: "context_setup",
          title: "Authenticated API journey context",
          status: "failed",
          error: error.message,
          stack: error.stack,
        },
      ],
    };
    await writeSummary(summary, args);
    process.exitCode = 1;
    return summary;
  }
  const results = [];

  for (const journey of selected) {
    const cleanup = new CleanupStack();
    const evidence = [];
    const startedAt = Date.now();
    let cleanupRan = false;
    process.stdout.write(`RUN ${journey.id} ${journey.title}\n`);

    try {
      const result = await journey.run({ ...baseContext, cleanup, evidence });
      const cleanupFailures = await cleanup.run(evidence);
      cleanupRan = true;
      if (cleanupFailures.length) {
        throw new Error(
          `Cleanup failed: ${cleanupFailures
            .map((item) => `${item.label}: ${item.error}`)
            .join("; ")}`,
        );
      }
      results.push({
        id: journey.id,
        title: journey.title,
        status: "passed",
        elapsed_ms: Date.now() - startedAt,
        evidence,
        result,
      });
      process.stdout.write(`PASS ${journey.id}\n`);
    } catch (error) {
      if (error instanceof SkipJourney) {
        if (!cleanupRan) await cleanup.run(evidence);
        results.push({
          id: journey.id,
          title: journey.title,
          status: "skipped",
          reason: error.reason,
          elapsed_ms: Date.now() - startedAt,
          evidence,
        });
        process.stdout.write(`SKIP ${journey.id} ${error.reason}\n`);
      } else {
        if (!cleanupRan) await cleanup.run(evidence);
        results.push({
          id: journey.id,
          title: journey.title,
          status: "failed",
          error: error.message,
          stack: error.stack,
          elapsed_ms: Date.now() - startedAt,
          evidence,
        });
        process.stdout.write(`FAIL ${journey.id} ${error.message}\n`);
        if (args.failFast) break;
      }
    }
  }

  const summary = {
    status: results.some((item) => item.status === "failed")
      ? "failed"
      : "passed",
    api_base: baseContext.apiBase,
    organization_id: baseContext.organizationId || null,
    workspace_id: baseContext.workspaceId || null,
    total: results.length,
    passed: results.filter((item) => item.status === "passed").length,
    skipped: results.filter((item) => item.status === "skipped").length,
    failed: results.filter((item) => item.status === "failed").length,
    results,
  };

  await writeSummary(summary, args);
  if (summary.failed > 0) process.exitCode = 1;
  return summary;
}

async function runPreflightCheck(args) {
  const jsonPath =
    args.preflightJsonPath ||
    path.join(
      os.tmpdir(),
      `api-journey-preflight-${Date.now().toString(36)}-${Math.random()
        .toString(36)
        .slice(2, 8)}.json`,
    );
  const startedAt = Date.now();
  let stdout = "";
  let stderr = "";
  let commandError = null;

  try {
    const preflightArgs = [preflightScriptPath, "--json", jsonPath];
    if (args.requirePublicPreflight && !args.requirePreflight) {
      preflightArgs.push("--public");
    }
    const result = await execFileAsync(process.execPath, preflightArgs);
    stdout = result.stdout;
    stderr = result.stderr;
  } catch (error) {
    commandError = error;
    stdout = error.stdout || "";
    stderr = error.stderr || "";
  }

  const summary = await readPreflightSummary(jsonPath, stdout);
  const failedChecks = (summary?.checks || []).filter(
    (check) => check.status === "failed",
  );
  const status =
    !commandError && summary?.status === "passed" && failedChecks.length === 0
      ? "passed"
      : "failed";

  return {
    status,
    jsonPath,
    elapsed_ms: Date.now() - startedAt,
    error:
      status === "passed"
        ? ""
        : failedChecks.length
          ? `Preflight failed checks: ${failedChecks
              .map((check) => check.name)
              .join(", ")}`
          : commandError?.message ||
            "Preflight did not produce a passed summary.",
    evidence: [
      {
        summary_status: summary?.status || "missing",
        failed: summary?.failed ?? failedChecks.length,
        warnings: summary?.warnings ?? null,
        checks: summary?.checks || [],
        stdout: summary ? undefined : stdout.slice(0, 2000),
        stderr: stderr ? stderr.slice(0, 2000) : undefined,
      },
    ],
  };
}

async function readPreflightSummary(jsonPath, stdout) {
  try {
    return JSON.parse(await fs.readFile(jsonPath, "utf8"));
  } catch {
    try {
      return JSON.parse(stdout);
    } catch {
      return null;
    }
  }
}

function createPublicJourneyContext() {
  const apiBase = normalizeBaseUrl(
    process.env.API_BASE || "http://localhost:8003",
  );
  return {
    client: createApiClient({ apiBase }),
    user: null,
    tokens: {},
    apiBase,
    organizationId: process.env.FUTURE_AGI_ORGANIZATION_ID || "",
    workspaceId: process.env.FUTURE_AGI_WORKSPACE_ID || "",
    runId: `${Date.now().toString(36)}-${Math.random()
      .toString(36)
      .slice(2, 8)}`,
  };
}

async function writeSummary(summary, args) {
  if (args.jsonPath) {
    await fs.writeFile(args.jsonPath, `${JSON.stringify(summary, null, 2)}\n`);
  }
  console.log(JSON.stringify(summary, null, 2));
}

function normalizeBaseUrl(value) {
  return String(value || "").replace(/\/+$/, "");
}

function parseArgs(argv) {
  const args = {
    failFast: false,
    grep: "",
    jsonPath: "",
    list: false,
    only: new Set(),
    preflightJsonPath: "",
    requirePreflight: false,
    requirePublicPreflight: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--fail-fast") {
      args.failFast = true;
    } else if (arg === "--list") {
      args.list = true;
    } else if (arg === "--require-preflight") {
      args.requirePreflight = true;
    } else if (arg === "--require-public-preflight") {
      args.requirePublicPreflight = true;
    } else if (arg === "--preflight-json") {
      args.preflightJsonPath = argv[++index] || "";
    } else if (arg === "--grep") {
      args.grep = argv[++index] || "";
    } else if (arg === "--only") {
      for (const id of String(argv[++index] || "").split(",")) {
        if (id.trim()) args.only.add(id.trim());
      }
    } else if (arg === "--json") {
      args.jsonPath = argv[++index] || "";
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return args;
}
