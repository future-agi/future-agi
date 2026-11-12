// TypeScript snippets for `Your code` (R4-SPEC D20, D23; lane Ra). Pure.
//
//   traceUploadScriptTs({entry, ids}) → run_and_upload.ts  (complete file)
//   traceUploadStepsTs → steps
// (dataset integration FE-0: the serve_agent.ts / connect generators are removed.)
//
// The four parts mirror run_and_upload.py with fetch. Framework init lines
// are the ones in unified-docs `integrations/traceai/{mastra, vercel,
// langchain}.mdx`; calls come from catalog `agent` (TS overrides below for
// entries whose catalog call is Python).
import {
  FI_BASE_URL,
  MAX_LINE,
  RUNNER_FILES,
  UPLOAD_HEADER,
  agentSlug,
  runnerCommand,
  commentLines,
  firstRow,
  lit,
} from "./lines";
import {
  agentExampleStep,
  installKeysStep,
  runWatchStep,
  sandboxStep,
} from "./runLocal";

export const TS_FILE = RUNNER_FILES.typescript;

const c = (text, indent = "") => commentLines(text, { prefix: "//", indent });

// Entries whose catalog `agent` is Python: their TypeScript call.
const TS_AGENT = {
  langchain: {
    spec: "src/agent.ts:agent",
    var: "agent",
    call: 'await agent.invoke({ messages: [{ role: "user", content: text }] })',
    output: "out.messages.at(-1).content",
    source: "LangChain.js agents quickstart (createAgent)",
    check: true,
  },
  otel_app: {
    spec: "src/app.ts:target",
    var: "target",
    call: "await target(row)",
    output: "out",
    source: "your function: target(row) => output",
  },
};

/** The TypeScript `agent` block of an entry (catalog, or the override). */
export function tsAgentOf(entry) {
  if (TS_AGENT[entry?.id]) return TS_AGENT[entry.id];
  const a = entry?.agent;
  if (a && entry?.languages?.includes("typescript")) return a;
  return TS_AGENT.otel_app;
}

/**
 * The tracing packages a generated TS file imports (S3 / L3 G4 C4): Mastra's
 * `@traceai/mastra` (with the Mastra observability packages it needs), else
 * `@traceai/fi-core` (`register`), plus `@traceai/langchain` for LangChain.js.
 * Vercel AI traces through fi-core's register + experimental_telemetry in
 * the script path.
 */
export function tsTracePackages(entry) {
  if (entry?.id === "mastra")
    return [
      "@traceai/mastra",
      "@mastra/observability",
      "@mastra/otel-exporter",
    ];
  if (entry?.id === "langchain")
    return ["@traceai/fi-core", "@traceai/langchain"];
  return ["@traceai/fi-core"];
}

export const FRAMEWORK_NPM = Object.freeze({
  mastra: ["@mastra/core", "@ai-sdk/openai", "zod"],
  vercel_ai: ["ai", "@ai-sdk/openai", "zod"],
  langchain: [
    "langchain",
    "@langchain/core",
    "@langchain/langgraph",
    "@langchain/openai",
    "zod",
  ],
});

/** npm packages to install for an entry (Trace + upload + the framework). */
export function npmPackages(entry) {
  const pkgs = new Set(["@traceai/fi-core", "@opentelemetry/api"]);
  (FRAMEWORK_NPM[entry?.id] ?? []).forEach((p) => pkgs.add(p));
  if (entry?.id === "mastra")
    [
      "@traceai/mastra",
      "@mastra/observability",
      "@mastra/otel-exporter",
    ].forEach((p) => pkgs.add(p));
  if (entry?.id === "langchain") pkgs.add("@traceai/langchain");
  pkgs.add("tsx");
  return [...pkgs].join(" ");
}

const isManual = (entry) => entry?.custom === true;

function defaults(ids = {}) {
  return {
    datasetId: ids.datasetId ?? "",
    datasetName: ids.datasetName || "this dataset",
    experimentId: ids.experimentId ?? "",
    runId: ids.runId ?? "",
    runLabel: ids.runLabel || "#1A",
    inputColumn: ids.inputColumn || "input",
    tries: Math.max(1, Number(ids.tries) || 1),
    agentName: agentSlug(ids.agentName || "support_agent"),
  };
}

/** Framework lines of run_and_upload.ts: imports, after-register, wrap. */
function frameworkTs(entry) {
  if (entry?.id === "mastra")
    return {
      imports: [
        'import { Mastra } from "@mastra/core";',
        'import { createFIObservability } from "@traceai/mastra";',
      ],
      // Mastra traces through its instance (docs: traceai/mastra).
      wrap: [
        "  const observability = createFIObservability({ serviceName: PROJECT_NAME });",
        "  const mastra = new Mastra({ agents: { agent: loaded }, observability });",
        '  const agent = mastra.getAgent("agent");',
      ],
      shutdown: ["  await observability.shutdown(); // flushes buffered spans"],
    };
  if (entry?.id === "langchain")
    return {
      imports: [
        'import { LangChainInstrumentation } from "@traceai/langchain";',
        'import * as CallbackManagerModule from "langchain/callbacks";',
      ],
      afterRegister: [
        "  const lcInstrumentation = new LangChainInstrumentation({",
        "    tracerProvider: tracerProvider,",
        "  });",
        "  lcInstrumentation.manuallyInstrument(CallbackManagerModule);",
      ],
    };
  if (entry?.id === "vercel_ai")
    return {
      notes: c(
        "Vercel AI SDK: your generateText / streamText call needs experimental_telemetry: { isEnabled: true } (docs: traceai/vercel); its spans use the global tracer this script registers.",
      ),
    };
  return {};
}

/** Rows by --rows, experiment.yml, timeouts and costs (run_and_upload.ts). */
const TS_ROWS_AND_CONFIG = Object.freeze([
  "const resultsUrl = () =>",
  "  `${FI_BASE_URL}/model-hub/experiments/v2/${EXPERIMENT_ID}` +",
  "  `/runs/${RUN_ID}/results/`;",
  "",
  "async function selectRows(rows: Row[], which: string | string[], dry: boolean) {",
  "  // --rows all | failed | new | id,id: failed and new read this run's results",
  '  const ids = Array.isArray(which) ? which : String(which).split(",");',
  '  if (ids[0] === "all") return rows;',
  '  if (ids[0] !== "failed" && ids[0] !== "new") {',
  "    // a row answers to its id, its label, its number (12) and T12",
  "    const want = new Set(ids.map((x) => String(x).trim().toUpperCase()));",
  "    return rows.filter((r, i) => {",
  "      const n = (r as any).n ?? i + 1;",
  "      const names = [r.id, (r as any).label, String(n), `T${n}`];",
  "      return names.some((x) => x && want.has(String(x).toUpperCase()));",
  "    });",
  "  }",
  "  const seen = new Map<string, any[]>(); // row id → its results",
  "  if (dry) {",
  "    console.log(`[dry-run] GET ${resultsUrl()}`);",
  '    console.log("[dry-run] no earlier results in this run");',
  "  } else {",
  "    const resp = await fetch(resultsUrl(), { headers: HEADERS });",
  "    if (!resp.ok) throw new Error(`results: ${resp.status}`);",
  "    for (const r of (await resp.json()).results ?? [])",
  "      seen.set(r.example_id, [...(seen.get(r.example_id) ?? []), r]);",
  "  }",
  '  if (ids[0] === "new") return rows.filter((r) => !seen.has(r.id));',
  "  const failed = (x: any) => Boolean(x.error) || x.passed === false;",
  "  const out = rows.filter((r) => (seen.get(r.id) ?? []).some(failed));",
  "  if (!out.length) console.log(`No failed rows in run ${RUN_ID}`);",
  "  return out;",
  "}",
  "",
  "function scalar(text: string): unknown {",
  "  // a YAML / CLI value: numbers, true/false/null, quoted or bare text",
  "  const t = text.trim();",
  '  if (t === "" || t === "~" || t === "null") return null;',
  "  try {",
  "    return JSON.parse(t);",
  "  } catch {",
  "    return t.replace(/^'|'$/g, \"\");",
  "  }",
  "}",
  "",
  "function readConfig(path: string) {",
  "  // experiment.yml: `key: value`, one level of nested maps, `- item` lists",
  "  const cfg: Record<string, any> = {};",
  "  let parent: string | null = null;",
  '  for (const raw of readFileSync(path, "utf8").split("\\n")) {',
  '    const line = raw.split(" #")[0].trimEnd();',
  '    if (!line.trim() || line.trim().startsWith("#")) continue;',
  '    const nested = raw.startsWith(" ");',
  "    const item = line.trim();",
  '    if (nested && parent && item.startsWith("- ")) {',
  "      if (!Array.isArray(cfg[parent])) cfg[parent] = [];",
  "      cfg[parent].push(scalar(item.slice(2)));",
  "      continue;",
  "    }",
  '    const at = item.indexOf(":");',
  "    const key = item.slice(0, at).trim();",
  "    const value = item.slice(at + 1);",
  "    if (nested && parent) cfg[parent][key] = scalar(value);",
  "    else if (value.trim()) [cfg[key], parent] = [scalar(value), null];",
  "    else [cfg[key], parent] = [{}, key];",
  "  }",
  "  return cfg;",
  "}",
  "",
  "function withTimeout<T>(fn: () => Promise<T>, timeoutS: number): Promise<T> {",
  "  // a try that runs past --timeout-s is recorded as timed out",
  "  if (!timeoutS) return fn();",
  "  return Promise.race([",
  "    fn(),",
  "    new Promise<T>((_, fail) => {",
  "      const t = setTimeout(",
  "        () => fail(new Error(`no answer in ${timeoutS} s`)),",
  "        timeoutS * 1000,",
  "      );",
  "      t.unref?.();",
  "    }),",
  "  ]);",
  "}",
  "",
  "// the USD a try reports (`cost_usd` in its output), for --max-cost-usd",
  "const costOf = (out: any) => Number(out?.cost_usd ?? 0) || 0;",
]);

/** runRows: every row × try, N at a time, until the cost cap (TS). */
const TS_POOL = (v) => [
  `async function runRows(${v}: any, rows: Row[], tracer: any, o: any) {`,
  "  const jobs = rows.flatMap((row) =>",
  "    Array.from({ length: o.trials || TRIES }, (_, i) => [row, i + 1] as const),",
  "  );",
  "  const results: any[] = [];",
  "  let spent = 0;",
  "  let next = 0;",
  "  const worker = async () => {",
  "    while (next < jobs.length) {",
  "      const [row, trial] = jobs[next++];",
  "      if (o.maxCost && spent >= o.maxCost) {",
  "        const error = `stopped: cost cap $${o.maxCost} reached`;",
  '        const stopped = { output: null, error, status: "stopped" };',
  "        results.push({ example_id: row.id, trial, ...stopped });",
  "        continue;",
  "      }",
  `      const r = await runOne(${v}, row, trial, tracer, o.timeoutS);`,
  "      spent += r.cost_usd;",
  "      results.push(r);",
  "    }",
  "  };",
  "  const n = Math.max(1, Number(o.concurrency) || 1);",
  "  await Promise.all(Array.from({ length: n }, worker));",
  "  console.log(`${results.length} tries · ${n} at a time · $${spent.toFixed(4)}`);",
  "  return results;",
  "}",
];

/**
 * run_and_upload.ts (D23): 1 fetchRows · 2 loadAgent · 3 the traced loop ·
 * 4 upload. Lines ≤ 88 characters.
 */
export function traceUploadScriptTs({ entry, ids }) {
  const d = defaults(ids);
  const a = tsAgentOf(entry);
  const fw = frameworkTs(entry);
  const v = a.var || "target";
  const sb = ids?.sandbox ?? null;
  // IT-R2 G1 P3: a task suite's --dry-run sends nothing (no tracer); Mastra
  // exports through its own instance, so it keeps tracing.
  const quiet = Boolean(sb) && entry?.id !== "mastra";
  const L = [];
  L.push(...c(UPLOAD_HEADER));
  L.push(
    ...c(
      `${TS_FILE} · run ${d.runLabel} (${entry?.name ?? "your code"}) on "${d.datasetName}"`,
    ),
  );
  const cmd = (rest) =>
    runnerCommand({ lang: "typescript", entry: a.spec, ...rest });
  L.push(`// ${cmd({ limit: 1 })}`);
  if (sb) {
    L.push(`// ${cmd({ dryRun: true, mockTools: true, limit: 1 })}`);
    L.push(`// ${cmd({ dryRun: true, noSandbox: true, limit: 1 })}`);
  } else L.push(`// ${cmd({ dryRun: true, limit: 1 })}`);
  L.push(`// ${cmd({ rows: "failed", trials: 3 })}`);
  L.push(`// npx tsx ${TS_FILE} --config experiment.yml`);
  L.push(
    ...c(
      "Flags as run_and_upload.py: --run --rows all|failed|new|<id or label,…> --limit --trials --concurrency --max-cost-usd --timeout-s --param key=value --config --side-effects real|stubs|dry_run --dry-run --mock-tools --no-sandbox --fail-under.",
    ),
  );
  L.push('import { readFileSync } from "node:fs";');
  if (sb) L.push('import { createServer } from "node:http";');
  L.push('import { resolve } from "node:path";');
  L.push('import { pathToFileURL } from "node:url";');
  L.push(
    `import { context, propagation${quiet ? ", trace" : ""} } from "@opentelemetry/api";`,
  );
  L.push('import { register, ProjectType } from "@traceai/fi-core";');
  L.push(...(fw.imports ?? []));
  L.push("");
  L.push(`const FI_BASE_URL = process.env.FI_BASE_URL ?? ${lit(FI_BASE_URL)};`);
  L.push(`const DATASET_ID = ${lit(d.datasetId)};`);
  L.push(`const EXPERIMENT_ID = ${lit(d.experimentId)};`);
  L.push(`let RUN_ID = ${lit(d.runId)}; // --run`);
  L.push(`const RUN_LABEL = ${lit(d.runLabel)};`);
  L.push(`const PROJECT_NAME = ${lit(`${d.datasetName} · runs`)};`);
  L.push(
    `const INPUT_COLUMN = ${lit(d.inputColumn)}; // from the drawer's mapping`,
  );
  L.push(`const TRIES = ${d.tries}; // tries per row (--trials)`);
  L.push("let PARAMS: Record<string, unknown> = {}; // --param key=value");
  L.push('let SIDE_EFFECTS = "real"; // --side-effects real|stubs|dry_run');
  if (sb) {
    L.push(`let SANDBOX_URL = process.env.${sb.envVar} ?? ""; // ${sb.name}`);
    L.push("let mock: { state: any } | null = null; // --mock-tools");
  }
  L.push("const HEADERS = {");
  L.push('  "X-Api-Key": process.env.FI_API_KEY ?? "",');
  L.push('  "X-Secret-Key": process.env.FI_SECRET_KEY ?? "",');
  L.push("};");
  L.push("const argv = process.argv.slice(2);");
  L.push("const valuesOf = (flag: string) =>");
  L.push("  argv.flatMap((x, i) => (x === flag ? [argv[i + 1]] : []));");
  L.push("const valueOf = (flag: string) => valuesOf(flag)[0];");
  L.push("");
  L.push("type Row = { id: string; [column: string]: unknown };");
  L.push("");
  L.push("// 1. The rows");
  L.push("async function fetchRows(): Promise<Row[]> {");
  L.push("  const rows: Row[] = [];");
  L.push("  let page = 0;");
  L.push("  let totalPages = 1;");
  L.push("  while (page < totalPages) {");
  L.push("    const url = new URL(");
  L.push(
    "      `${FI_BASE_URL}/model-hub/develops/${DATASET_ID}/get-dataset-table/`,",
  );
  L.push("    );");
  L.push('    url.searchParams.set("page_size", "100");');
  L.push('    url.searchParams.set("current_page_index", String(page));');
  L.push("    const resp = await fetch(url, { headers: HEADERS });");
  L.push(
    "    if (!resp.ok) throw new Error(`get-dataset-table: ${resp.status}`);",
  );
  L.push("    const { result } = await resp.json();");
  L.push("    const names: [string, string][] = result.columnConfig.map(");
  L.push("      (col: { id: string; name: string }) => [col.id, col.name],");
  L.push("    );");
  L.push("    for (const row of result.table) {");
  L.push("      const item: Row = { id: row.rowId };");
  L.push(
    "      for (const [colId, name] of names) item[name] = row[colId]?.cellValue;",
  );
  L.push("      rows.push(item);");
  L.push("    }");
  L.push("    totalPages = result.metadata.totalPages;");
  L.push("    page += 1;");
  L.push("  }");
  L.push("  return rows;");
  L.push("}");
  L.push("");
  L.push('function readSampleRows(path = "sample_rows.jsonl"): Row[] {');
  L.push('  return readFileSync(path, "utf8")');
  L.push('    .split("\\n")');
  L.push("    .filter((line) => line.trim())");
  L.push("    .map((line) => JSON.parse(line));");
  L.push("}");
  L.push("");
  if (sb) {
    L.push(...c(`Tools in this suite run against ${sb.name}.`));
    L.push("async function startTask(row: Row): Promise<string> {");
    L.push(
      "  // reset the sandbox to this task's start_state; returns its session",
    );
    L.push('  if (!SANDBOX_URL) return ""; // --no-sandbox');
    L.push("  if (mock) {");
    L.push("    mock.state = row.start_state ?? {};");
    L.push('    return "";');
    L.push("  }");
    L.push("  const resp = await fetch(`${SANDBOX_URL}/reset`, {");
    L.push('    method: "POST",');
    L.push('    headers: { "Content-Type": "application/json" },');
    L.push(
      "    body: JSON.stringify({ start_state: row.start_state, side_effects: SIDE_EFFECTS }),",
    );
    L.push("  });");
    L.push("  if (!resp.ok) throw new Error(`sandbox reset: ${resp.status}`);");
    L.push("  return (await resp.json()).session_id;");
    L.push("}");
    L.push("");
    L.push("async function endState(session: string) {");
    L.push("  // what the end-state checks read after your agent finishes");
    L.push(
      "  if (mock || !SANDBOX_URL) return null; // no sandbox: checks are skipped",
    );
    L.push(
      "  const resp = await fetch(`${SANDBOX_URL}/state?session_id=${session}`);",
    );
    L.push("  if (!resp.ok) throw new Error(`sandbox state: ${resp.status}`);");
    L.push("  return resp.json();");
    L.push("}");
    L.push("");
    L.push("function startMockTools(): Promise<string> {");
    L.push(
      "  // --mock-tools: no docker; tools answer from the task's start_state",
    );
    L.push("  mock = { state: {} };");
    L.push("  const server = createServer((req, res) => {");
    L.push('    let raw = "";');
    L.push('    req.on("data", (chunk) => (raw += chunk));');
    L.push('    req.on("end", () => {');
    L.push('      const args = Object.values(JSON.parse(raw || "{}"));');
    L.push(
      '      const [verb, noun] = String(req.url).split("/").pop()!.split(/_(.+)/);',
    );
    L.push("      const rows: any[] = mock?.state?.[`${noun}s`] ?? [];");
    L.push("      const hit = rows.find((r) => args.includes(r.id));");
    L.push(
      '      res.end(JSON.stringify(verb === "lookup" && hit ? hit : { ok: true }));',
    );
    L.push("    });");
    L.push("  });");
    L.push("  server.unref();");
    L.push("  return new Promise((ok) =>");
    L.push("    server.listen(0, () =>");
    L.push("      ok(`http://127.0.0.1:${(server.address() as any).port}`),");
    L.push("    ),");
    L.push("  );");
    L.push("}");
    L.push("");
  }
  L.push(...TS_ROWS_AND_CONFIG);
  L.push("");
  L.push("// 2. Your agent");
  L.push("async function loadAgent(spec: string) {");
  L.push("  // `file.ts:export`: the only thing you change (--agent)");
  L.push('  const [file, name] = spec.split(":");');
  L.push("  const mod = await import(pathToFileURL(resolve(file)).href);");
  L.push('  if (mod.PARAMS && typeof mod.PARAMS === "object")');
  L.push(
    "    Object.assign(mod.PARAMS, PARAMS); // --param reaches your module",
  );
  L.push("  return mod[name];");
  L.push("}");
  L.push("");
  L.push("// 3. The traced loop");
  if (a.check)
    L.push(
      ...c(
        `check: ${entry?.name} call from ${a.source}; not run by an example`,
      ),
    );
  L.push(...(fw.notes ?? []));
  L.push(
    `async function runOne(${v}: any, row: Row, trial: number, tracer: any, timeoutS = 0) {`,
  );
  L.push("  {");
  L.push("    {");
  L.push('      const text = String(row[INPUT_COLUMN] ?? "");');
  if (sb)
    L.push("      const session = await startTask(row); // its start_state");
  L.push("      const bag = propagation.createBaggage({");
  L.push('        "fi.run_id": { value: RUN_ID },');
  L.push('        "fi.row_id": { value: row.id },');
  L.push('        "fi.trial": { value: String(trial) },');
  L.push("      });");
  L.push("      const ctx = propagation.setBaggage(context.active(), bag);");
  L.push("      const attributes: Record<string, string | number> = {");
  L.push('        "fi.run_id": RUN_ID,');
  L.push('        "fi.row_id": row.id,');
  L.push('        "fi.trial": trial,');
  L.push("      };");
  if (isManual(entry))
    L.push(
      '      attributes["graph.node.id"] = "target"; // set graph.node.* per step',
    );
  L.push("      const started = Date.now();");
  L.push("      const done = await context.with(ctx, () =>");
  L.push(
    '        tracer.startActiveSpan("row", { attributes }, async (span: any) => {',
  );
  L.push("          const traceId = span.spanContext().traceId;");
  L.push("          try {");
  const call = String(a.call).replace(/^await /, "");
  const outLine = `            const out: any = await withTimeout(async () => ${call}, timeoutS);`;
  if (outLine.length > MAX_LINE) {
    L.push("            const out: any = await withTimeout(");
    L.push(`              async () => ${call},`);
    L.push("              timeoutS,");
    L.push("            );");
  } else L.push(outLine);
  L.push("            return {");
  L.push(`              output: ${a.output},`);
  L.push("              error: null,");
  L.push("              traceId,");
  L.push("              cost: costOf(out),");
  L.push("            };");
  L.push("          } catch (err) {");
  L.push(
    "            return { output: null, error: String(err), traceId, cost: 0 };",
  );
  L.push("          } finally {");
  L.push("            span.end();");
  L.push("          }");
  L.push("        }),");
  L.push("      );");
  L.push(
    '      console.log(`${row.id} try ${trial}: ${done.error ? "error" : "ok"}`);',
  );
  L.push("      return {");
  L.push("        example_id: row.id,");
  L.push("        trial,");
  L.push("        output: done.output,");
  L.push("        error: done.error,");
  L.push("        latency_ms: Date.now() - started,");
  L.push("        trace_id: done.traceId,");
  L.push("        cost_usd: done.cost,");
  if (sb) L.push("        end_state: await endState(session),");
  L.push("      };");
  L.push("    }");
  L.push("  }");
  L.push("}");
  L.push("");
  L.push(...TS_POOL(v));
  L.push("");
  L.push("// 4. Upload");
  L.push("async function upload(results: unknown[], dryRun: boolean) {");
  L.push("  const url =");
  L.push("    `${FI_BASE_URL}/model-hub/experiments/v2/${EXPERIMENT_ID}` +");
  L.push("    `/runs/${RUN_ID}/results/`;");
  L.push("  const batches = [];");
  L.push("  for (let i = 0; i < results.length; i += 50)");
  L.push("    batches.push(results.slice(i, i + 50));");
  L.push("  if (!batches.length) batches.push([]);");
  L.push("  for (const [i, batch] of batches.entries()) {");
  L.push(
    "    const body = { results: batch, complete: i === batches.length - 1 };",
  );
  L.push("    if (dryRun) {");
  L.push("      console.log(`[dry-run] POST ${url}`);");
  L.push("      console.log(JSON.stringify(body, null, 2));");
  L.push("      continue;");
  L.push("    }");
  L.push("    const resp = await fetch(url, {");
  L.push('      method: "POST",');
  L.push('      headers: { ...HEADERS, "Content-Type": "application/json" },');
  L.push("      body: JSON.stringify(body),");
  L.push("    });");
  L.push("    if (!resp.ok) throw new Error(`upload: ${resp.status}`);");
  L.push("    console.log(`uploaded batch ${i + 1} of ${batches.length}`);");
  L.push("  }");
  L.push("}");
  L.push("");
  L.push("async function main() {");
  L.push('  const dryRun = argv.includes("--dry-run");');
  L.push(
    '  const cfg: any = valueOf("--config") ? readConfig(valueOf("--config")!) : {};',
  );
  L.push("  const limits = cfg.limits ?? {};");
  L.push("  const pick = (...vals: unknown[]) =>");
  L.push('    vals.find((x) => x !== undefined && x !== null && x !== "");');
  L.push('  RUN_ID = String(pick(valueOf("--run"), cfg.run_id, RUN_ID));');
  L.push("  SIDE_EFFECTS = String(");
  L.push(
    '    pick(valueOf("--side-effects"), cfg.safety?.side_effects, "real"),',
  );
  L.push("  );");
  L.push("  PARAMS = { ...(cfg.params ?? {}) };");
  L.push('  for (const pair of valuesOf("--param")) {');
  L.push('    const [key, ...rest] = String(pair).split("=");');
  L.push('    PARAMS[key] = scalar(rest.join("="));');
  L.push("  }");
  L.push("  process.env.FI_PARAMS = JSON.stringify(PARAMS);");
  L.push("  process.env.FI_SIDE_EFFECTS = SIDE_EFFECTS;");
  if (sb) {
    L.push(
      '  if (argv.includes("--no-sandbox")) SANDBOX_URL = ""; // no tool server',
    );
    L.push(
      '  else if (argv.includes("--mock-tools") || (dryRun && !SANDBOX_URL))',
    );
    L.push(
      "    SANDBOX_URL = await startMockTools(); // the 12 tools, no sandbox",
    );
    L.push(`  process.env.${sb.envVar} = SANDBOX_URL; // your tools read it`);
  }
  L.push(
    '  const limit = Number(pick(valueOf("--limit"), cfg.limit)) || undefined;',
  );
  L.push(
    `  const spec = String(pick(valueOf("--agent"), cfg.agent, ${lit(a.spec)}));`,
  );
  L.push(
    '  const trials = Number(pick(valueOf("--trials"), cfg.trials, TRIES));',
  );
  L.push(
    '  const concurrency = Number(pick(valueOf("--concurrency"), limits.concurrency, 1));',
  );
  L.push(
    '  const maxCost = Number(pick(valueOf("--max-cost-usd"), limits.max_cost_usd, 0));',
  );
  L.push(
    '  const timeoutS = Number(pick(valueOf("--timeout-s"), limits.timeout_s, 0));',
  );
  L.push('  const failUnder = pick(valueOf("--fail-under"), cfg.fail_under);');
  L.push('  const which = pick(valueOf("--rows"), cfg.rows, "all");');
  L.push(
    '  if (!dryRun && !(HEADERS["X-Api-Key"] && HEADERS["X-Secret-Key"]))',
  );
  L.push(
    '    throw new Error("Set FI_API_KEY and FI_SECRET_KEY in this shell first.");',
  );
  if (sb) {
    L.push('  if (!SANDBOX_URL && !argv.includes("--no-sandbox"))');
    L.push(
      `    throw new Error("Set ${sb.envVar}, or pass --mock-tools or --no-sandbox.");`,
    );
  }
  if (quiet) {
    L.push("  // --dry-run sends nothing to Future AGI: no tracer provider");
    L.push("  const tracerProvider = dryRun");
    L.push("    ? null");
    L.push("    : register({");
    L.push("        projectType: ProjectType.EXPERIMENT,");
    L.push("        projectName: PROJECT_NAME,");
    L.push("        projectVersionName: RUN_LABEL,");
    L.push("        setGlobalTracerProvider: true,");
    L.push("      });");
    if (fw.afterRegister?.length) {
      L.push("  if (tracerProvider) {");
      L.push(...fw.afterRegister.map((x) => `  ${x}`));
      L.push("  }");
    }
    L.push("  const tracer = tracerProvider");
    L.push('    ? tracerProvider.getTracer("run_and_upload")');
    L.push(
      '    : trace.getTracer("dry-run"); // no provider: nothing is exported',
    );
  } else {
    L.push("  const tracerProvider = register({");
    L.push("    projectType: ProjectType.EXPERIMENT,");
    L.push("    projectName: PROJECT_NAME,");
    L.push("    projectVersionName: RUN_LABEL,");
    L.push("    setGlobalTracerProvider: true,");
    L.push("  });");
    L.push(...(fw.afterRegister ?? []));
    L.push('  const tracer = tracerProvider.getTracer("run_and_upload");');
  }
  if (fw.wrap) {
    L.push("  const loaded = await loadAgent(spec);");
    L.push(...fw.wrap);
  } else L.push(`  const ${v} = await loadAgent(spec);`);
  L.push("  let rows = dryRun ? readSampleRows() : await fetchRows();");
  L.push(
    "  rows = await selectRows(rows, which as string | string[], dryRun);",
  );
  L.push("  if (limit) rows = rows.slice(0, limit);");
  L.push(
    "  if (Object.keys(PARAMS).length) console.log(`params ${JSON.stringify(PARAMS)}`);",
  );
  L.push(
    `  const results = await runRows(${fw.wrap ? "agent" : v}, rows, tracer, {`,
  );
  L.push("    trials,");
  L.push("    concurrency,");
  L.push("    timeoutS,");
  L.push("    maxCost,");
  L.push("  });");
  L.push(`  await tracerProvider${quiet ? "?" : ""}.forceFlush();`);
  L.push(...(fw.shutdown ?? []));
  L.push("  await upload(results, dryRun);");
  L.push(
    "  const ok = results.filter((r: any) => !r.error && r.passed !== false);",
  );
  L.push("  const rate = results.length ? ok.length / results.length : 0;");
  L.push("  console.log(`pass rate ${rate.toFixed(2)}`);");
  L.push("  if (failUnder !== undefined && rate < Number(failUnder)) {");
  L.push(
    "    console.error(`pass rate ${rate.toFixed(2)} is below --fail-under ${failUnder}`);",
  );
  L.push("    process.exitCode = 1;");
  L.push("  }");
  L.push("}");
  L.push("");
  L.push("main().catch((err) => {");
  L.push("  console.error(err);");
  L.push("  process.exit(1);");
  L.push("});");
  return `${L.join("\n")}\n`;
}

/** Trace + upload steps (TypeScript). */
export function traceUploadStepsTs({ entry, ids }) {
  const a = tsAgentOf(entry);
  const file = traceUploadScriptTs({ entry, ids });
  const cmd = (o = {}) =>
    runnerCommand({
      lang: "typescript",
      entry: a.spec,
      sideEffects: ids?.sideEffects,
      ...o,
    });
  // options-truth L2 G4 (C1): the form's flags (ids.run, runFlagsOf) ride
  // on every command this card prints; without them the plain commands.
  const f = ids?.run ?? null;
  const form = f
    ? {
        runId: f.runId,
        trials: f.trials ?? ids?.tries,
        limits: f.limits,
        params: f.params,
      }
    : { trials: ids?.tries };
  const ns = { noSandbox: Boolean(f?.noSandbox && ids?.sandbox) };
  // the no-keys tries keep the form's tries, limits and params (no run id)
  const dry = f ? { ...form, runId: undefined } : {};
  const run = cmd({ ...form, rows: f?.rows, ...ns });
  return {
    fileName: TS_FILE,
    file,
    // options-truth L3 G4 fix 5: the first picked row (`--rows T5 --limit 1`)
    tryCommand: cmd({ ...form, rows: firstRow(f?.rows), limit: 1, ...ns }),
    runCommand: run,
    dryRunCommand: cmd({ ...dry, dryRun: true }),
    mockCommand: ids?.sandbox
      ? cmd({
          ...dry,
          dryRun: true,
          mockTools: true,
          limit: 1,
        })
      : null,
    noSandboxCommand: ids?.sandbox
      ? cmd({
          ...dry,
          dryRun: true,
          noSandbox: true,
          limit: 1,
        })
      : null,
    command: cmd,
    steps: [
      installKeysStep({ install: `npm i ${npmPackages(entry)}`, ids }),
      sandboxStep(ids?.sandbox ?? null),
      agentExampleStep({
        entry: { ...entry, agent: { ...(entry?.agent ?? {}), spec: a.spec } },
        lang: "typescript",
        ids,
        scriptName: TS_FILE,
      }),
      runWatchStep(run, ids?.sandbox ? 4 : 3),
    ].filter(Boolean),
  };
}
