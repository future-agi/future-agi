// Python snippets for `Your code` (R4-SPEC D20, D23; lane Ra). Pure.
//
//   traceUploadScript({entry, ids}) → run_and_upload.py   (complete file)
//   traceUploadSteps → [{n, title, lang, code, status, …}]
// (dataset integration FE-0: the serve_agent.py / connect generators are removed; the
// Your code card that reached this file is absent until FE-1 replaces it.)
//
// The framework parts come from catalog.js (`instrument`, `agent`); the ids
// (dataset, reserved experiment / run, label, input column, tries) from the
// drawer. Every file runs top to bottom: nothing is left for the user to
// define except `--agent file.py:variable` on the command line.
import {
  FI_BASE_URL,
  RUNNER_FILES,
  UPLOAD_HEADER,
  agentSlug,
  runnerCommand,
  commentLines,
  firstRow,
  lit,
  maskSecret,
} from "./lines";
import {
  agentExampleStep,
  installKeysStep,
  runWatchStep,
  sandboxStep,
} from "./runLocal";

export const PY_FILE = RUNNER_FILES.python;

// IT-R4 G1 P2: the framework's own packages, so step 2 installs what the
// agent imports (`langgraph langchain-openai` for LangGraph).
export const FRAMEWORK_PIP = Object.freeze({
  langgraph: ["langgraph", "langchain-openai"],
  // the sample builds its agent with langgraph.prebuilt (L3 G4 C4)
  langchain: ["langchain", "langgraph", "langchain-openai"],
  llamaindex: ["llama-index"],
  crewai: ["crewai"],
  openai_agents: ["openai-agents"],
  google_adk: ["google-adk"],
  autogen: ["autogen-agentchat", "autogen-ext[openai]"],
  smolagents: ["smolagents"],
  dspy: ["dspy"],
  haystack: ["haystack-ai"],
  pydantic_ai: ["pydantic-ai"],
  agno: ["agno"],
  strands: ["strands-agents"],
  claude_agent_sdk: ["claude-agent-sdk"],
  ag2: ["ag2[openai]"],
});

/** A pip package as one shell word: extras are quoted (zsh globs `[`). */
export const pipWord = (p) => (/[[\]]/.test(p) ? `"${p}"` : p);

/**
 * The tracing packages a generated Python file needs (S3): the traceAI
 * package (it brings fi-instrumentation-otel), else fi-instrumentation-otel
 * (the file imports fi_instrumentation) plus the framework's own
 * OpenTelemetry instrumentor (Agno: openinference-instrumentation-agno).
 */
export function pyTracePackages(entry) {
  const pip = String(instrumentFor(entry)?.pip ?? "").trim();
  const traceAI = /^traceAI-/i.test(pip);
  const out = traceAI ? [] : ["fi-instrumentation-otel"];
  if (pip && pip !== "fi-instrumentation-otel") out.push(pip);
  return out;
}

/**
 * Packages to install for an entry (Trace + upload + the framework), as
 * shell words (extras quoted). `framework: false` when a requirements.txt
 * brings the framework (CI).
 */
export function pipPackages(entry, { framework = true } = {}) {
  const pkgs = ["futureagi", ...pyTracePackages(entry), "requests"];
  (framework ? FRAMEWORK_PIP[entry?.id] ?? [] : []).forEach((p) => {
    if (!pkgs.includes(p)) pkgs.push(p);
  });
  return pkgs.map(pipWord).join(" ");
}

/**
 * OTel frameworks whose catalog entry has no instrument line: the line
 * from the framework's own OpenTelemetry docs (doc URL in `doc`).
 */
export const OTEL_INSTRUMENT = Object.freeze({
  // Strands: hand it the Future AGI provider (StrandsTelemetry() alone
  // builds its own provider, so its spans would miss the run).
  strands: {
    import: "from strands.telemetry import StrandsTelemetry",
    call: "StrandsTelemetry(tracer_provider=trace_provider)",
  },
  agno: {
    pip: "openinference-instrumentation-agno",
    import: "from openinference.instrumentation.agno import AgnoInstrumentor",
    call: "AgnoInstrumentor().instrument(tracer_provider=trace_provider)",
    doc: "https://docs.agno.com/observability/overview",
  },
});
export const instrumentFor = (entry) =>
  OTEL_INSTRUMENT[entry?.id] ?? entry?.instrument ?? null;

const isTraceAI = (entry) => entry?.badge === "auto";
const isOtelPreview = (entry) => entry?.badge === "otel_preview";
const isManual = (entry) => entry?.custom === true;

/** The instrument lines `{imports: [], calls: []}` for the script. */
function instrumentOf(entry) {
  const ins = instrumentFor(entry);
  if (isTraceAI(entry) && ins?.import && ins?.call)
    return { imports: [ins.import], calls: [ins.call] };
  if (isOtelPreview(entry) && ins?.import && ins?.call)
    return {
      imports: [ins.import],
      calls: [...(ins.doc ? [`# ${ins.doc}`] : []), ins.call],
    };
  return { imports: [], calls: [] };
}

/** `# check: …` lines for entries whose call no example runs (D23). */
function checkLines(entry, indent = "") {
  const a = entry?.agent;
  if (!a?.check) return [];
  return commentLines(
    `check: ${entry.name} call from ${a.source}; not run by an example`,
    { indent },
  );
}

/** A Python list literal of names (one line). */
function pyList(names = [], indent = "") {
  const one = `[${names.map((n) => lit(n)).join(", ")}]`;
  if (one.length + indent.length + 16 <= 88) return one;
  return `[\n${names.map((n) => `${indent}    ${lit(n)},`).join("\n")}\n${indent}]`;
}

/** The suite's tools that change state (everything but lookups / search). */
export const sideEffectTools = (tools = []) =>
  tools.filter((t) => !/^(lookup|search)_/.test(t));

const OTEL_NOTE =
  "No traceAI package verified: this framework emits OpenTelemetry itself. Check the spans of one row before a full run.";

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

/**
 * run_and_upload.py for one framework (D23, S3): 1 fetch_rows · 2 load_agent
 * · 3 the traced loop (N at a time, timeout, cost cap) · 4 upload. Takes
 * every S3 flag (RUNNER_FLAGS); Python lines ≤ 88 characters.
 * @param {{entry: object, ids: object}} p
 * @returns {string}
 */
export function traceUploadScript({ entry, ids }) {
  const d = defaults(ids);
  const a = entry?.agent ?? {
    spec: "agent.py:target",
    var: "target",
    call: "target(row)",
    output: "out",
  };
  const ins = instrumentOf(entry);
  const v = a.var || "target";
  const call = callWithParams(entry, a, v, "PARAMS", "cfg");
  const projectName = `${d.datasetName} · runs`;
  const extraImports = [...new Set([...(a.imports ?? []), ...ins.imports])]
    .filter(
      (l) =>
        !/^import (argparse|concurrent|contextvars|json|os|sys|threading|time|importlib)/.test(
          l,
        ),
    )
    .sort();
  const stdImports = extraImports.filter((l) => l.startsWith("import "));
  const pkgImports = extraImports.filter((l) => l.startsWith("from "));
  const sb = ids?.sandbox ?? null;
  const cmd = (rest) => runnerCommand({ entry: a.spec, ...rest });
  const L = [];
  L.push(...commentLines(UPLOAD_HEADER));
  L.push(
    ...commentLines(
      `${PY_FILE} · run "${d.runLabel}" (${entry?.name ?? "your code"}) on "${d.datasetName}"`,
    ),
  );
  L.push(`#   ${cmd({ limit: 1 })}`);
  if (sb) {
    L.push(`#   ${cmd({ dryRun: true, mockTools: true, limit: 1 })}`);
    L.push(`#   ${cmd({ dryRun: true, noSandbox: true, limit: 1 })}`);
  } else L.push(`#   ${cmd({ dryRun: true, limit: 1 })}`);
  L.push(`#   ${cmd({ rows: "failed", trials: 3 })}`);
  L.push(`#   python ${PY_FILE} --config experiment.yml`);
  L.push(
    ...commentLines(
      `${sb ? "--mock-tools answers the suite's tools from each task's start_state (no sandbox); --no-sandbox runs without any tool server. " : ""}--dry-run reads sample_rows.jsonl, needs no keys and prints the request instead of sending it. --rows all|failed|new|<id or label,…> (T4, 12) · --trials N · --concurrency N · --max-cost-usd X · --timeout-s N · --param key=value · --side-effects real|stubs|dry_run · --fail-under 0.0-1.0 (exit 1 below that pass rate).`,
    ),
  );
  L.push("import argparse");
  L.push("import concurrent.futures");
  L.push("import contextvars");
  L.push("import importlib.util");
  L.push("import json");
  L.push("import os");
  L.push("import sys");
  L.push("import threading");
  L.push("import time");
  L.push(...stdImports);
  L.push("");
  L.push("import requests");
  L.push("from fi_instrumentation import register");
  L.push("from fi_instrumentation.fi_types import ProjectType");
  if (sb) L.push("from http.server import BaseHTTPRequestHandler, HTTPServer");
  L.push("from opentelemetry import baggage, context, trace");
  L.push(...pkgImports);
  L.push("");
  L.push(`FI_BASE_URL = os.environ.get("FI_BASE_URL", ${lit(FI_BASE_URL)})`);
  L.push(`DATASET_ID = ${lit(d.datasetId)}`);
  L.push(`EXPERIMENT_ID = ${lit(d.experimentId)}`);
  L.push(`RUN_ID = ${lit(d.runId)}  # --run`);
  L.push(`RUN_LABEL = ${lit(d.runLabel)}`);
  L.push(`PROJECT_NAME = ${lit(projectName)}`);
  L.push(`INPUT_COLUMN = ${lit(d.inputColumn)}  # from the drawer's mapping`);
  L.push(`TRIES = ${d.tries}  # tries per row (--trials)`);
  L.push("PARAMS = {}  # --param key=value: what your agent reads");
  L.push('SIDE_EFFECTS = "real"  # --side-effects real|stubs|dry_run');
  if (sb) {
    L.push(`SANDBOX_ENV = ${lit(sb.envVar)}  # ${sb.name}`);
    L.push('SANDBOX_URL = os.environ.get(SANDBOX_ENV, "")');
    L.push("MOCK_TOOLS = False  # --mock-tools");
    L.push(`WRITE_TOOLS = ${pyList(sideEffectTools(sb.tools))}`);
  }
  L.push("HEADERS = {");
  L.push('    "X-Api-Key": os.environ.get("FI_API_KEY", ""),');
  L.push('    "X-Secret-Key": os.environ.get("FI_SECRET_KEY", ""),');
  L.push("}");
  if (a.helper?.length) {
    L.push("");
    L.push("");
    L.push(...commentLines(`Helper for ${entry.name} (${a.source}).`));
    L.push(...a.helper);
  }
  L.push("");
  L.push("");
  L.push("# 1. fetch_rows()");
  L.push("def fetch_rows():");
  L.push(
    '    """This dataset\'s rows as {"id": row id, <column name>: value}."""',
  );
  L.push(
    '    url = f"{FI_BASE_URL}/model-hub/develops/{DATASET_ID}/get-dataset-table/"',
  );
  L.push("    page, total_pages = 0, 1");
  L.push("    while page < total_pages:");
  L.push('        params = {"page_size": 100, "current_page_index": page}');
  L.push(
    "        resp = requests.get(url, headers=HEADERS, params=params, timeout=60)",
  );
  L.push("        resp.raise_for_status()");
  L.push('        result = resp.json()["result"]');
  L.push(
    '        names = {c["id"]: c["name"] for c in result["columnConfig"]}',
  );
  L.push('        for row in result["table"]:');
  L.push('            item = {"id": row["rowId"]}');
  L.push("            for col_id, name in names.items():");
  L.push(
    '                item[name] = (row.get(col_id) or {}).get("cellValue")',
  );
  L.push("            yield item");
  L.push('        total_pages = result["metadata"]["totalPages"]');
  L.push("        page += 1");
  L.push("");
  L.push("");
  L.push('def read_sample_rows(path="sample_rows.jsonl"):');
  L.push(
    '    """--dry-run: rows from a local file, one JSON object per line."""',
  );
  L.push('    with open(path, encoding="utf-8") as f:');
  L.push("        return [json.loads(line) for line in f if line.strip()]");
  L.push("");
  L.push("");
  L.push("# 2. Which rows run (--rows)");
  L.push("def results_url():");
  L.push("    return (");
  L.push('        f"{FI_BASE_URL}/model-hub/experiments/v2/{EXPERIMENT_ID}"');
  L.push('        f"/runs/{RUN_ID}/results/"');
  L.push("    )");
  L.push("");
  L.push("");
  // options-truth L2 G4 (C4): --rows takes row ids and row labels (T4, 12)
  L.push("def row_names(row, n):");
  L.push(
    '    """A row answers to its id, its label, its number (12) and T12."""',
  );
  L.push('    n = row.get("n") or n');
  L.push('    names = {row.get("id"), row.get("label"), str(n), f"T{n}"}');
  L.push("    return {str(x).upper() for x in names if x}");
  L.push("");
  L.push("");
  L.push("def select_rows(rows, which, dry_run=False):");
  L.push(
    '    """--rows all | failed | new | id,id: failed and new read this run\'s results."""',
  );
  L.push('    if which in (None, "", "all"):');
  L.push("        return rows");
  L.push('    if which not in ("failed", "new"):');
  L.push(
    '        wanted = {w.strip().upper() for w in which.split(",") if w.strip()}',
  );
  L.push(
    "        return [r for i, r in enumerate(rows, 1) if row_names(r, i) & wanted]",
  );
  L.push("    seen = {}");
  L.push("    if dry_run:");
  L.push('        print(f"[dry-run] GET {results_url()}")');
  L.push('        print("[dry-run] no earlier results in this run")');
  L.push("    else:");
  L.push(
    "        resp = requests.get(results_url(), headers=HEADERS, timeout=60)",
  );
  L.push("        resp.raise_for_status()");
  L.push('        for res in resp.json().get("results", []):');
  L.push('            seen.setdefault(res["example_id"], []).append(res)');
  L.push('    if which == "new":');
  L.push('        return [r for r in rows if r["id"] not in seen]');
  L.push("");
  L.push("    def failed(res):");
  L.push('        return bool(res.get("error")) or res.get("passed") is False');
  L.push("");
  L.push(
    '    out = [r for r in rows if any(failed(x) for x in seen.get(r["id"], []))]',
  );
  L.push("    if not out:");
  L.push('        print(f"No failed rows in run {RUN_ID}")');
  L.push("    return out");
  if (sb) {
    L.push("");
    L.push("");
    L.push(...commentLines(`Tools in this suite run against ${sb.name}.`));
    L.push("def start_task(row):");
    L.push(
      '    """Reset the sandbox to this task\'s start_state; returns its session."""',
    );
    L.push("    if not SANDBOX_URL:  # --no-sandbox");
    L.push("        return None");
    L.push("    if MOCK_TOOLS:");
    L.push('        MockTools.state = dict(row.get("start_state") or {})');
    L.push("        return None");
    L.push(
      '    body = {"start_state": row.get("start_state"), "side_effects": SIDE_EFFECTS}',
    );
    L.push(
      '    resp = requests.post(f"{SANDBOX_URL}/reset", json=body, timeout=60)',
    );
    L.push("    resp.raise_for_status()");
    L.push('    return resp.json()["session_id"]');
    L.push("");
    L.push("");
    L.push("def end_state(session):");
    L.push(
      '    """What the end-state checks read after your agent finishes."""',
    );
    L.push("    if not SANDBOX_URL or MOCK_TOOLS:");
    L.push("        return None  # no sandbox: end-state checks are skipped");
    L.push('    params = {"session_id": session}');
    L.push(
      '    resp = requests.get(f"{SANDBOX_URL}/state", params=params, timeout=60)',
    );
    L.push("    resp.raise_for_status()");
    L.push("    return resp.json()");
    L.push("");
    L.push("");
    L.push("class MockTools(BaseHTTPRequestHandler):");
    L.push(
      '    """--mock-tools: the suite\'s tools answer from the task\'s start_state."""',
    );
    L.push("    state = {}");
    L.push("");
    L.push("    def do_GET(self):  # /health");
    L.push('        self.answer({"ok": True, "tools": len(TOOL_NAMES)})');
    L.push("");
    L.push("    def do_POST(self):");
    L.push('        size = int(self.headers.get("Content-Length") or 0)');
    L.push('        args = json.loads(self.rfile.read(size) or b"{}")');
    L.push('        tool = self.path.rsplit("/", 1)[-1]');
    L.push('        verb, _, noun = tool.partition("_")');
    L.push('        rows = self.state.get(f"{noun}s", [])');
    L.push('        hit = [r for r in rows if r.get("id") in args.values()]');
    L.push("        if tool not in TOOL_NAMES:");
    L.push('            answer = {"error": f"unknown tool {tool}"}');
    L.push('        elif verb == "lookup" or verb == "search":');
    L.push('            answer = hit[0] if hit else {"found": False}');
    L.push('        elif tool in WRITE_TOOLS and SIDE_EFFECTS != "real":');
    L.push(
      '            answer = {"ok": True, SIDE_EFFECTS: True, "tool": tool}',
    );
    L.push("        else:");
    L.push(
      '            self.state.setdefault("calls", []).append([tool, args])',
    );
    L.push('            answer = {"ok": True, "tool": tool}');
    L.push("        self.answer(answer)");
    L.push("");
    L.push("    def answer(self, body):");
    L.push("        self.send_response(200)");
    L.push('        self.send_header("Content-Type", "application/json")');
    L.push("        self.end_headers()");
    L.push("        self.wfile.write(json.dumps(body).encode())");
    L.push("");
    L.push("    def log_message(self, *args):");
    L.push("        pass");
    L.push("");
    L.push("");
    L.push(`TOOL_NAMES = ${pyList(sb.tools ?? [])}`); // the 12 tools
    L.push("");
    L.push("");
    L.push("def start_mock_tools():");
    L.push('    server = HTTPServer(("127.0.0.1", 0), MockTools)');
    L.push(
      "    threading.Thread(target=server.serve_forever, daemon=True).start()",
    );
    L.push('    return f"http://127.0.0.1:{server.server_port}"');
  }
  L.push("");
  L.push("");
  L.push("# 3. Your agent");
  L.push("def load_agent(spec):");
  L.push(
    '    """Import `file.py:variable`: the only thing you change (--agent)."""',
  );
  L.push('    path, _, name = spec.partition(":")');
  L.push("    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))");
  L.push(
    '    module_spec = importlib.util.spec_from_file_location("user_agent", path)',
  );
  L.push("    module = importlib.util.module_from_spec(module_spec)");
  L.push("    module_spec.loader.exec_module(module)");
  L.push('    if isinstance(getattr(module, "PARAMS", None), dict):');
  L.push("        module.PARAMS.update(PARAMS)  # --param reaches your module");
  L.push("    return getattr(module, name)");
  L.push("");
  L.push("");
  L.push("def with_timeout(fn, timeout_s):");
  L.push(
    '    """Run fn in this context; a try that runs past --timeout-s is timed out."""',
  );
  L.push("    if not timeout_s:");
  L.push("        return fn()");
  L.push("    box, ctx = {}, contextvars.copy_context()");
  L.push("");
  L.push("    def work():");
  L.push("        try:");
  L.push('            box["out"] = fn()');
  L.push("        except Exception as exc:  # re-raised below");
  L.push('            box["exc"] = exc');
  L.push("");
  L.push(
    "    worker = threading.Thread(target=ctx.run, args=(work,), daemon=True)",
  );
  L.push("    worker.start()");
  L.push("    worker.join(timeout_s)");
  L.push("    if worker.is_alive():");
  L.push('        raise TimeoutError(f"no answer in {timeout_s:g} s")');
  L.push('    if "exc" in box:');
  L.push('        raise box["exc"]');
  L.push('    return box["out"]');
  L.push("");
  L.push("");
  L.push("# 4. The traced loop");
  // options-truth L2 G4 (C2): a framework that answers with a message
  // object (Haystack's ChatMessage) uploads its text, not a JSON error.
  L.push("def answer_of(value):");
  L.push(
    '    """Your agent\'s answer as JSON: a message object becomes its text."""',
  );
  L.push(
    "    if value is None or isinstance(value, (str, int, float, bool, list, dict)):",
  );
  L.push("        return value");
  L.push(
    '    return getattr(value, "text", None) or getattr(value, "content", None) or str(value)',
  );
  L.push("");
  L.push("");
  if (isOtelPreview(entry)) L.push(...commentLines(OTEL_NOTE));
  L.push(...checkLines(entry));
  L.push(`def run_one(${v}, row, trial, tracer, timeout_s):`);
  L.push(
    '    """One try in one span with the baton fi.run_id / fi.row_id / fi.trial."""',
  );
  L.push('    text = str(row.get(INPUT_COLUMN) or "")');
  if (sb) L.push("    session = start_task(row)  # the task's start_state");
  L.push('    ctx = baggage.set_baggage("fi.run_id", RUN_ID)');
  L.push('    ctx = baggage.set_baggage("fi.row_id", row["id"], context=ctx)');
  L.push('    ctx = baggage.set_baggage("fi.trial", str(trial), context=ctx)');
  L.push("    token = context.attach(ctx)");
  L.push(
    '    attrs = {"fi.run_id": RUN_ID, "fi.row_id": row["id"], "fi.trial": trial}',
  );
  if (isManual(entry))
    L.push(
      '    attrs["graph.node.id"] = "target"  # set graph.node.* per step',
    );
  if (call.passesParams)
    L.push(
      '    cfg = {"configurable": PARAMS}  # --param, as the graph config',
    );
  L.push("");
  L.push("    def call():");
  L.push(`        return ${call.code}`);
  L.push("");
  L.push("    started, out = time.time(), None");
  L.push(
    '    with tracer.start_as_current_span("row", attributes=attrs) as span:',
  );
  L.push('        trace_id = format(span.get_span_context().trace_id, "032x")');
  L.push("        try:");
  L.push("            out = with_timeout(call, timeout_s)");
  L.push(`            output, error = answer_of(${a.output}), None`);
  L.push(
    "        except Exception as exc:  # recorded per row; the run goes on",
  );
  L.push('            output, error = None, f"{type(exc).__name__}: {exc}"');
  L.push("    context.detach(token)");
  L.push("    result = {");
  L.push('        "example_id": row["id"],');
  L.push('        "trial": trial,');
  L.push('        "output": output,');
  L.push('        "error": error,');
  L.push('        "latency_ms": int((time.time() - started) * 1000),');
  L.push('        "trace_id": trace_id,');
  L.push('        "cost_usd": cost_of(out),');
  L.push("    }");
  if (sb) L.push('    result["end_state"] = end_state(session)');
  L.push(
    "    print(f\"{row['id']} try {trial}: {'error' if error else 'ok'}\")",
  );
  L.push("    return result");
  L.push("");
  L.push("");
  L.push("def cost_of(out):");
  L.push(
    '    """The USD a try reports (`cost_usd` in its output), for --max-cost-usd."""',
  );
  L.push("    if isinstance(out, dict):");
  L.push('        return float(out.get("cost_usd") or 0)');
  L.push('    return float(getattr(out, "cost_usd", 0) or 0)');
  L.push("");
  L.push("");
  L.push(
    `def run_rows(${v}, rows, tracer, trials=TRIES, concurrency=1, timeout_s=None,`,
  );
  L.push("             max_cost_usd=None):");
  L.push(
    '    """Every row × try, N at a time; stops starting tries at the cost cap."""',
  );
  L.push("    spent, lock = [0.0], threading.Lock()");
  L.push("");
  L.push("    def job(row, trial):");
  L.push("        with lock:");
  L.push("            capped = max_cost_usd and spent[0] >= max_cost_usd");
  L.push("        if capped:");
  L.push('            error = f"stopped: cost cap ${max_cost_usd:g} reached"');
  L.push(
    '            return {"example_id": row["id"], "trial": trial, "output": None,',
  );
  L.push('                    "error": error, "status": "stopped"}');
  L.push(`        result = run_one(${v}, row, trial, tracer, timeout_s)`);
  L.push("        with lock:");
  L.push('            spent[0] += result["cost_usd"]');
  L.push("        return result");
  L.push("");
  L.push("    jobs = [(row, t) for row in rows for t in range(1, trials + 1)]");
  L.push(
    "    with concurrent.futures.ThreadPoolExecutor(max(1, concurrency)) as pool:",
  );
  L.push("        results = list(pool.map(lambda j: job(*j), jobs))");
  L.push(
    '    print(f"{len(results)} tries · {concurrency} at a time · ${spent[0]:.4f}")',
  );
  L.push("    return results");
  L.push("");
  L.push("");
  L.push("# 5. upload(results)");
  L.push("def upload(results, dry_run=False):");
  L.push(
    '    """POST in batches of 50; the last batch marks the run complete."""',
  );
  L.push(
    "    batches = [results[i : i + 50] for i in range(0, len(results), 50)] or [[]]",
  );
  L.push("    for n, batch in enumerate(batches, start=1):");
  L.push('        body = {"results": batch, "complete": n == len(batches)}');
  L.push("        if dry_run:");
  L.push('            print(f"[dry-run] POST {results_url()}")');
  L.push("            print(json.dumps(body, indent=2))");
  L.push("            continue");
  L.push(
    "        resp = requests.post(results_url(), headers=HEADERS, json=body, timeout=60)",
  );
  L.push("        resp.raise_for_status()");
  L.push('        print(f"uploaded batch {n} of {len(batches)}")');
  L.push("");
  L.push("");
  L.push("def pass_rate(results):");
  L.push(
    '    """Share of tries that finished without an error and did not fail a check."""',
  );
  L.push(
    '    ok = [r for r in results if not r.get("error") and r.get("passed") is not False]',
  );
  L.push("    return len(ok) / len(results) if results else 0.0");
  L.push("");
  L.push("");
  L.push(...PY_READ_CONFIG);
  L.push("");
  L.push("");
  L.push("def parse_args(argv=None):");
  L.push(
    `    parser = argparse.ArgumentParser(description=${lit(`Run "${d.runLabel}" and upload it.`)})`,
  );
  L.push(
    `    parser.add_argument("--agent", default=None, help=${lit(a.spec)})`,
  );
  L.push('    parser.add_argument("--run", default=None, help="the run id")');
  L.push(
    '    parser.add_argument("--rows", default=None, help="all|failed|new|id-or-label,…")',
  );
  L.push('    parser.add_argument("--limit", type=int, default=None)');
  L.push('    parser.add_argument("--trials", type=int, default=None)');
  L.push('    parser.add_argument("--concurrency", type=int, default=None)');
  L.push('    parser.add_argument("--max-cost-usd", type=float, default=None)');
  L.push('    parser.add_argument("--timeout-s", type=float, default=None)');
  L.push(
    '    parser.add_argument("--param", action="append", default=[], help="key=value")',
  );
  L.push(
    '    parser.add_argument("--config", default=None, help="experiment.yml")',
  );
  L.push("    parser.add_argument(");
  L.push(
    '        "--side-effects", choices=["real", "stubs", "dry_run"], default=None',
  );
  L.push("    )");
  L.push('    parser.add_argument("--dry-run", action="store_true")');
  L.push('    parser.add_argument("--mock-tools", action="store_true")');
  L.push('    parser.add_argument("--no-sandbox", action="store_true")');
  L.push('    parser.add_argument("--fail-under", type=float, default=None)');
  L.push("    args = parser.parse_args(argv)");
  L.push("    cfg = read_config(args.config) if args.config else {}");
  L.push(
    '    limits, safety = cfg.get("limits") or {}, cfg.get("safety") or {}',
  );
  L.push("");
  L.push(
    "    def pick(*vals):  # the flag, then experiment.yml, then the default",
  );
  L.push('        return next((x for x in vals if x not in (None, "")), None)');
  L.push("");
  L.push(`    args.agent = pick(args.agent, cfg.get("agent"), ${lit(a.spec)})`);
  L.push('    args.run = pick(args.run, cfg.get("run_id"), RUN_ID)');
  L.push('    args.rows = pick(args.rows, cfg.get("rows"), "all")');
  L.push("    if isinstance(args.rows, list):");
  L.push('        args.rows = ",".join(args.rows)');
  L.push('    args.limit = pick(args.limit, cfg.get("limit"))');
  L.push('    args.trials = int(pick(args.trials, cfg.get("trials"), TRIES))');
  L.push(
    '    args.concurrency = int(pick(args.concurrency, limits.get("concurrency"), 1))',
  );
  L.push(
    '    args.max_cost_usd = pick(args.max_cost_usd, limits.get("max_cost_usd"))',
  );
  L.push('    args.timeout_s = pick(args.timeout_s, limits.get("timeout_s"))');
  L.push(
    '    args.side_effects = pick(args.side_effects, safety.get("side_effects"), "real")',
  );
  L.push('    args.fail_under = pick(args.fail_under, cfg.get("fail_under"))');
  L.push('    params = dict(cfg.get("params") or {})');
  L.push("    for pair in args.param:");
  L.push('        key, _, value = pair.partition("=")');
  L.push("        params[key] = scalar(value)");
  L.push("    args.params = params");
  L.push("    return args");
  L.push("");
  L.push("");
  L.push("def main(argv=None):");
  L.push(
    `    global RUN_ID, PARAMS, SIDE_EFFECTS${sb ? ", MOCK_TOOLS, SANDBOX_URL" : ""}`,
  );
  L.push("    args = parse_args(argv)");
  L.push(
    "    RUN_ID, PARAMS, SIDE_EFFECTS = args.run, args.params, args.side_effects",
  );
  L.push('    os.environ["FI_PARAMS"] = json.dumps(PARAMS)');
  L.push('    os.environ["FI_SIDE_EFFECTS"] = SIDE_EFFECTS');
  if (sb) {
    L.push("    if args.no_sandbox:  # no tool server: tools answer nothing");
    L.push('        SANDBOX_URL = ""');
    L.push("    elif args.mock_tools or (args.dry_run and not SANDBOX_URL):");
    L.push("        MOCK_TOOLS, SANDBOX_URL = True, start_mock_tools()");
    L.push("    os.environ[SANDBOX_ENV] = SANDBOX_URL  # your tools read it");
  }
  L.push(
    '    if not args.dry_run and not (HEADERS["X-Api-Key"] and HEADERS["X-Secret-Key"]):',
  );
  L.push(
    '        sys.exit("Set FI_API_KEY and FI_SECRET_KEY in this shell first.")',
  );
  if (sb) {
    L.push(
      "    if not args.dry_run and not SANDBOX_URL and not args.no_sandbox:",
    );
    L.push(
      '        sys.exit(f"Set {SANDBOX_ENV}, or pass --mock-tools or --no-sandbox.")',
    );
  }
  L.push("    trace_provider = None");
  L.push("    if args.dry_run:  # nothing leaves this machine: no traces");
  L.push("        tracer = trace.NoOpTracer()");
  L.push("    else:");
  L.push("        trace_provider = register(");
  L.push("            project_type=ProjectType.EXPERIMENT,");
  L.push("            project_name=PROJECT_NAME,");
  L.push("            project_version_name=RUN_LABEL,");
  if (isOtelPreview(entry))
    L.push("            set_global_tracer_provider=True,");
  L.push("        )");
  ins.calls.forEach((c) => L.push(`        ${c}`));
  L.push('        tracer = trace_provider.get_tracer("run_and_upload")');
  L.push(`    ${v} = load_agent(args.agent)`);
  L.push(
    "    rows = read_sample_rows() if args.dry_run else list(fetch_rows())",
  );
  L.push("    rows = select_rows(rows, args.rows, dry_run=args.dry_run)");
  L.push("    if args.limit:");
  L.push("        rows = rows[: int(args.limit)]");
  L.push("    if PARAMS:");
  L.push('        print(f"params {json.dumps(PARAMS)}")');
  L.push(`    results = run_rows(`);
  L.push(`        ${v},`);
  L.push("        rows,");
  L.push("        tracer,");
  L.push("        trials=args.trials,");
  L.push("        concurrency=args.concurrency,");
  L.push(
    "        timeout_s=float(args.timeout_s) if args.timeout_s else None,",
  );
  L.push(
    "        max_cost_usd=float(args.max_cost_usd) if args.max_cost_usd else None,",
  );
  L.push("    )");
  L.push("    if trace_provider:");
  L.push("        trace_provider.force_flush()");
  L.push("    upload(results, dry_run=args.dry_run)");
  L.push("    rate = pass_rate(results)");
  L.push('    print(f"pass rate {rate:.2f}")');
  L.push(
    "    if args.fail_under is not None and rate < float(args.fail_under):",
  );
  L.push(
    '        sys.exit(f"pass rate {rate:.2f} is below --fail-under {args.fail_under}")',
  );
  L.push("");
  L.push("");
  L.push('if __name__ == "__main__":');
  L.push("    main()");
  return `${L.join("\n")}\n`;
}

/**
 * The framework call with the run's params. LangGraph and
 * LangChain take the UI's params as `config={"configurable": params}`;
 * other calls stay as the catalog gives them.
 */
function callWithParams(entry, a, local, paramsVar = "params", configVar) {
  let code = String(a.call ?? "target(row)");
  if (a.var && local !== a.var)
    code = code.replace(new RegExp(`\\b${a.var}\\(`), `${local}(`);
  if (["langgraph", "langchain"].includes(entry?.id) && /\.invoke\(/.test(code))
    return {
      code: code.replace(
        /\)$/,
        configVar
          ? `, config=${configVar})`
          : `, config={"configurable": ${paramsVar}})`,
      ),
      passesParams: true,
    };
  return { code, passesParams: false };
}

/** `--config experiment.yml`: the subset of YAML experiment.yml uses (stdlib). */
export const PY_READ_CONFIG = Object.freeze([
  "def scalar(text):",
  '    """A YAML / CLI value: numbers, true/false/null, quoted or bare text."""',
  "    text = text.strip()",
  '    if text in ("", "~", "null"):',
  "        return None",
  "    try:",
  "        return json.loads(text)",
  "    except ValueError:",
  '        return text.strip("\'")',
  "",
  "",
  "def read_config(path):",
  '    """experiment.yml: `key: value`, one level of nested maps, `- item` lists."""',
  "    cfg, parent = {}, None",
  '    with open(path, encoding="utf-8") as f:',
  "        for raw in f:",
  '            line = raw.split(" #", 1)[0].rstrip()',
  '            if not line.strip() or line.lstrip().startswith("#"):',
  "                continue",
  '            nested, item = raw.startswith(" "), line.strip()',
  '            if nested and item.startswith("- ") and parent:',
  "                if not isinstance(cfg[parent], list):",
  "                    cfg[parent] = []",
  "                cfg[parent].append(scalar(item[2:]))",
  "                continue",
  '            key, _, value = item.partition(":")',
  "            if nested and parent:",
  "                if not isinstance(cfg[parent], dict):",
  "                    cfg[parent] = {}",
  "                cfg[parent][key.strip()] = scalar(value)",
  "            elif value.strip():",
  "                cfg[key.strip()], parent = scalar(value), None",
  "            else:",
  "                cfg[key.strip()], parent = {}, key.strip()",
  "    return cfg",
]);

export function keysStep(n, ids = {}) {
  const k = ids.keys ?? {};
  const api = k.apiKey || "";
  const secret = k.secretKey || "";
  const copy = `export FI_API_KEY=${api ? lit(api) : '""'}\nexport FI_SECRET_KEY=${secret ? lit(secret) : '""'}`;
  const shown = `export FI_API_KEY=${maskSecret(api) || "…"} FI_SECRET_KEY=${maskSecret(secret) || "…"}`;
  return {
    n,
    id: "keys",
    title: "Keys",
    lang: "bash",
    code: shown,
    copyText: copy,
    masked: true,
    status: "today",
    note: "FI_API_KEY and FI_SECRET_KEY in this shell",
    keysLink: "/dashboard/keys",
  };
}

/**
 * Run on your machine (IT-R1 G1 P1): three steps · 1 Install and set keys ·
 * 2 Point it at your agent · 3 Run it and watch. The complete script is
 * `file` (shown collapsed); `status` is the panel's one status line.
 * @returns {{steps: object[], fileName: string, file: string, status: "today"}}
 */
export function traceUploadSteps({ entry, ids }) {
  const a = entry?.agent ?? { spec: "agent.py:target" };
  const file = traceUploadScript({ entry, ids });
  const cmd = (o = {}) =>
    runnerCommand({ entry: a.spec, sideEffects: ids?.sideEffects, ...o });
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
    fileName: PY_FILE,
    file,
    // options-truth L3 G4 fix 5: the 1-row command runs the first picked
    // row (as the stand-in does): picked T5, T12 → `--rows T5 --limit 1`.
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
      installKeysStep({ install: `pip install ${pipPackages(entry)}`, ids }),
      sandboxStep(ids?.sandbox ?? null),
      agentExampleStep({ entry, lang: "python", ids, scriptName: PY_FILE }),
      runWatchStep(run, ids?.sandbox ? 4 : 3),
    ].filter(Boolean),
  };
}
