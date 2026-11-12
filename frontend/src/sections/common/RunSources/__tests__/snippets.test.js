import { describe, expect, it } from "vitest";
import { catalogEntry } from "../catalog";
import { FRAMEWORKS } from "../catalogEntries";
import { fileFor, snippetFor } from "../snippets";
import { traceUploadScript } from "../snippets/python";
import { tsAgentOf } from "../snippets/typescript";
import { commentLines, longestLine, maskSecret } from "../snippets/lines";
import { dryRunNote, sandboxIdsOf } from "../snippets/runLocal";
import { cappedLimits, sandboxAtATimeMax } from "../snippets/sandbox";

const IDS = {
  datasetId: "d5a1e7c2-0b3f-4a8d-9e61-1c2b3a4d5e06",
  datasetName: "Support ops tasks · sandbox",
  experimentId: "e0000000-0000-4000-8000-000000000004",
  runId: "a6000000-0000-4000-8000-00000000004a",
  runLabel: "#4A",
  inputColumn: "instruction",
  tries: 3,
  agentName: "support_agent",
  keys: { apiKey: "fi_test_placeholder", secretKey: "fs_test_placeholder" },
};

// dataset integration FE-0: Your code is absent from the picker; its generators stay
// until FE-1 replaces them.
const codeEntries = FRAMEWORKS;
const pyEntries = codeEntries.filter((e) => e.languages.includes("python"));
const tsEntries = codeEntries.filter((e) => e.languages.includes("typescript"));

// Placeholders a user would have to fill in (f-string names like {RUN_ID}
// are real variables, not placeholders).
const PLACEHOLDER =
  /<your |<[a-z_]+>|\{(dataset_id|experiment_id|run_id|task)\}|TODO|FIXME/;

// Strip string literals and comments so names inside them don't count.
const stripPy = (src) =>
  src
    .replace(/"""[\s\S]*?"""/g, '""')
    .replace(/f?"(?:[^"\\\n]|\\.)*"/g, '""')
    .replace(/'(?:[^'\\\n]|\\.)*'/g, "''")
    .replace(/#.*$/gm, "");

const PY_BUILTINS = new Set(
  "str int float len range print format type Exception None True False list dict open json".split(
    " ",
  ),
);
const rootNames = (expr) =>
  [...stripPy(expr).matchAll(/(?<![.\w])([A-Za-z_]\w*)/g)]
    // keyword arguments (`user_msg=text`) are not names the file defines
    .filter((m) => !/^\s*=[^=]/.test(m.input.slice(m.index + m[1].length)))
    .map((m) => m[1])
    .filter(
      (n) =>
        !PY_BUILTINS.has(n) &&
        !["await", "for", "in", "if", "else", "and", "or", "not"].includes(n),
    );

/** Is `name` defined in the Python source (assigned, imported, def, param)? */
function pyDefines(src, name) {
  const s = stripPy(src);
  const n = name.replace(/[$]/g, "\\$");
  return [
    new RegExp(`^\\s*(async )?def ${n}\\(`, "m"),
    new RegExp(`^\\s*(async )?def \\w+\\([^)]*\\b${n}\\b`, "m"),
    new RegExp(`^\\s*import .*\\b${n}\\b`, "m"),
    new RegExp(`^\\s*from \\S+ import .*\\b${n}\\b`, "m"),
    new RegExp(`^\\s*${n}(, \\w+)* =`, "m"),
    new RegExp(`^\\s*\\w+, ${n} =`, "m"),
    new RegExp(`for ${n} in`, "m"),
    new RegExp(`as ${n}\\b`, "m"),
  ].some((re) => re.test(s));
}

describe("snippets · run_and_upload.py for every Python entry", () => {
  it.each(pyEntries.map((e) => [e.id, e]))("%s", (_id, entry) => {
    const file = traceUploadScript({ entry, ids: IDS });
    const lines = file.split("\n");
    // ot-L1 S1: the factual upload line first, no status words anywhere
    expect(lines[0]).toMatch(/^# Traces every row with traceAI, then uploads/);
    expect(file.replace(/\n# /g, " ")).toContain(
      "POST /model-hub/experiments/v2/{EXPERIMENT_ID}/runs/{RUN_ID}/results/",
    );
    expect(file).not.toMatch(/Preview|Works today|not released|PREVIEW/);
    // the five labelled parts, in order
    const heads = [
      "# 1. fetch_rows()",
      "# 2. Which rows run (--rows)",
      "# 3. Your agent",
      "# 4. The traced loop",
      "# 5. upload(results)",
    ].map((h) => file.indexOf(h));
    heads.forEach((i) => expect(i).toBeGreaterThan(-1));
    expect([...heads].sort((a, b) => a - b)).toEqual(heads);
    // no placeholder, ids filled
    expect(file).not.toMatch(PLACEHOLDER);
    expect(file).toContain(IDS.datasetId);
    expect(file).toContain(IDS.runId);
    expect(file).toContain('INPUT_COLUMN = "instruction"');
    // ≤ 88 characters per line
    expect(longestLine(file)).toBeLessThanOrEqual(88);
    // the dataset route the SDK reads
    expect(file).toContain("/get-dataset-table/");
    expect(file).toContain('row["rowId"]');
    expect(file).toContain('"cellValue"');
    // every name the framework call and output use is defined or imported
    const a = entry.agent;
    [...rootNames(a.call), ...rootNames(a.output)]
      .filter((n) => n !== "text" && n !== "row" && n !== "out")
      .forEach((n) => expect(pyDefines(file, n), `${n} defined`).toBe(true));
    ["fetch_rows", "load_agent", "run_rows", "upload", "register"].forEach(
      (n) => expect(pyDefines(file, n), n).toBe(true),
    );
    // check comment where no example runs the call
    if (a.check) expect(file).toMatch(/# check: /);
    // OTel · Preview note
    if (entry.badge === "otel_preview")
      expect(file).toMatch(/No traceAI package verified/);
  });

  it("LangGraph script is the documented traceAI setup", () => {
    const f = fileFor({ entryId: "langgraph", ids: IDS });
    expect(f).toContain("from traceai_langchain import LangChainInstrumentor");
    expect(f).toContain(
      "LangChainInstrumentor().instrument(tracer_provider=trace_provider)",
    );
    expect(f).toContain("project_type=ProjectType.EXPERIMENT");
    expect(f).toContain(
      'app.invoke({"messages": [("user", text)]}, config=cfg)',
    );
    expect(f).toContain('cfg = {"configurable": PARAMS}');
    expect(f).toContain("--limit");
    expect(f).toContain("--dry-run");
    expect(f).toContain("sample_rows.jsonl");
  });
});

describe("snippets · TypeScript", () => {
  it.each(tsEntries.map((e) => [e.id, e]))("%s run_and_upload.ts", (_, e) => {
    const file = fileFor({ entryId: e.id, lang: "typescript", ids: IDS });
    expect(file.split("\n")[0]).toMatch(/^\/\/ Traces every row with traceAI/);
    [
      "// 1. The rows",
      "// 2. Your agent",
      "// 3. The traced loop",
      "// 4. Upload",
    ].forEach((h) => expect(file).toContain(h));
    expect(file).not.toMatch(/Preview|Works today|not released|PREVIEW/);
    // no top-level await (S3): the file ends with main().catch
    expect(file).toMatch(/\nmain\(\)\.catch\(/);
    expect(file).not.toMatch(PLACEHOLDER);
    expect(longestLine(file)).toBeLessThanOrEqual(88);
    // never a Python call in a TypeScript file
    expect(tsAgentOf(e).call).not.toMatch(/\("user", text\)/);
  });

  it("Mastra wires createFIObservability into a Mastra instance (docs)", () => {
    const f = fileFor({ entryId: "mastra", ids: IDS });
    expect(f).toContain(
      'import { createFIObservability } from "@traceai/mastra";',
    );
    expect(f).toContain('mastra.getAgent("agent")');
    expect(f).toContain("await observability.shutdown();");
  });
});

describe("IT-R2 G1 P3 · samples read the dataset", () => {
  it("custom code reads the real columns, never row.input", () => {
    const ids = {
      ...IDS,
      columns: ["instruction", "customer_goal", "order_id"],
    };
    const py = snippetFor({ kind: "code", entryId: "custom_python", ids });
    expect(py.steps[1].code).toContain('instruction = row["instruction"]');
    expect(py.steps[1].code).toContain('order_id = row["order_id"]');
    expect(py.steps[1].code).not.toContain("row['input']");
    const ts = snippetFor({
      kind: "code",
      entryId: "custom_typescript",
      lang: "typescript",
      ids,
    });
    expect(ts.steps[1].code).toContain(
      "const { instruction, customer_goal, order_id } = row;",
    );
    expect(ts.steps[1].code).not.toContain("row.input");
  });

  it("dryRunNote says what leaves the machine", () => {
    const sb = { name: "acme-support-sandbox" };
    expect(dryRunNote({ sandbox: sb, lang: "python" }).dry).toBe(
      "runs 3 rows on your machine and prints the results and the request. It needs no keys and sends nothing to Future AGI (no traces, no results); without the sandbox it serves the suite's tools itself.",
    );
    // the tool count is the sandbox's own list (never a constant)
    expect(
      dryRunNote({ sandbox: { ...sb, tools: ["a", "b", "c"] } }).mock,
    ).toBe(
      "No sandbox needed: the script serves the suite's 3 tools from each task's start_state; end-state checks are skipped. --no-sandbox runs with no tool server at all.",
    );
    expect(dryRunNote({}).dry).toContain("traces still go");
    expect(dryRunNote({}).mock).toBeNull();
  });

  it("sandboxIdsOf needs a start_state and carries the suite's tools", () => {
    expect(sandboxIdsOf({ mcp: { name: "acme-support-sandbox" } })).toBeNull();
    const sb = sandboxIdsOf({
      image: "ghcr.io/acme/acme-support-sandbox:1.4",
      mcp: { name: "acme-support-sandbox", tool_names: ["lookup_order"] },
      start_state_tables: ["orders"],
    });
    expect(sb).toMatchObject({
      envVar: "ACME_SANDBOX_URL",
      org: "Acme",
      tools: ["lookup_order"],
      image: "ghcr.io/acme/acme-support-sandbox:1.4",
    });
  });
});

describe("snippetFor · steps", () => {
  it("Run on your machine: 1 install and set keys · 2 your agent · 3 run it", () => {
    const s = snippetFor({ kind: "code", entryId: "langgraph", ids: IDS });
    expect(s.steps.map((x) => x.id)).toEqual(["install", "agent", "run"]);
    expect(s.steps.map((x) => x.title)).toEqual([
      "Install and set keys",
      "Point it at your agent",
      "Run it and watch",
    ]);
    expect(s.steps[0].code.split("\n")[0]).toBe(
      "pip install futureagi traceAI-langchain requests langgraph langchain-openai",
    );
    // the run's tries (IDS.tries = 3) are in every command (S3)
    expect(s.tryCommand).toBe(
      "python run_and_upload.py --agent agent.py:graph --limit 1 --trials 3",
    );
    expect(s.steps[2].code).toBe(
      "python run_and_upload.py --agent agent.py:graph --trials 3",
    );
    expect(s.fileName).toBe("run_and_upload.py");
    expect(s.file).toContain("def load_agent(spec):");
    // Step 2: the file the script imports, 6–10 lines of real code.
    expect(s.steps[1].must).toBe("Your agent.py must expose graph");
    expect(s.steps[1].code).toContain("graph = builder.compile()");
    expect(s.steps[1].code).toContain("MessagesState");
  });

  it("keys: masked on screen (eye shows them), real export lines on copy", () => {
    const step = snippetFor({ kind: "code", entryId: "langgraph", ids: IDS })
      .steps[0];
    expect(step.code).not.toContain(IDS.keys.apiKey);
    expect(step.code).toContain("…lder");
    expect(step.copyText.split("\n").slice(1)).toEqual([
      'export FI_API_KEY="fi_test_placeholder"',
      'export FI_SECRET_KEY="fs_test_placeholder"',
    ]);
    expect(step.revealText).toBe(step.copyText);
  });

  it("a task suite wires its sandbox: step 2 starts it, the script resets start_state", () => {
    const sandbox = {
      name: "acme-support-sandbox",
      envVar: "ACME_SANDBOX_URL",
      image: "ghcr.io/acme/acme-support-sandbox:1.4",
      port: 8080,
      org: "Acme",
      tools: [
        "lookup_order",
        "lookup_customer",
        "update_address",
        "issue_refund",
      ],
      tables: ["orders", "customers"],
    };
    const s = snippetFor({
      kind: "code",
      entryId: "langgraph",
      ids: { ...IDS, sandbox },
    });
    // IT-R2 G1 P3: install · sandbox · agent · run; the sandbox step says
    // what it is and how to check it is up.
    expect(s.steps.map((x) => x.id)).toEqual([
      "install",
      "sandbox",
      "agent",
      "run",
    ]);
    expect(s.steps.map((x) => x.n)).toEqual([1, 2, 3, 4]);
    expect(s.steps[0].copyText).not.toContain("docker");
    // a runnable stdlib sandbox, downloadable from the step (S3)
    expect(s.steps[1].code).toContain("python sandbox_server.py --port 8765 &");
    expect(s.steps[1].code).toContain(
      "export ACME_SANDBOX_URL=http://127.0.0.1:8765",
    );
    expect(s.steps[1].fileName).toBe("sandbox_server.py");
    expect(s.steps[1].file).toContain('if url.path == "/health":');
    expect(s.steps[1].healthUrl).toBe("http://127.0.0.1:8765/health");
    expect(s.steps[1].code).toContain("curl -fsS $ACME_SANDBOX_URL/health");
    expect(s.steps[1].explain).toBe(
      "The sandbox is a local copy of Acme's support system (orders, customers). Your agent's tools call it; each task starts from its start_state and the checks read it afterwards.",
    );
    // The LangGraph sample is a tool-calling graph over the suite's tools,
    // each a POST to the sandbox.
    const agent = s.steps[2].code;
    expect(agent).toContain("ToolNode(TOOLS)");
    expect(agent).toContain("def lookup_order(order_id: str) -> dict:");
    expect(agent).toContain("def update_address(order_id: str, address: str)");
    expect(agent).toContain('return call("lookup_order", order_id=order_id)');
    expect(agent).toContain(
      'SANDBOX = os.environ.get("ACME_SANDBOX_URL", "http://127.0.0.1:8765")',
    );
    expect(agent).toContain("+ 1 more of the suite's tools");
    expect(longestLine(agent)).toBeLessThanOrEqual(88);
    // --dry-run sends nothing; --mock-tools needs no docker.
    expect(s.mockCommand).toBe(
      "python run_and_upload.py --agent agent.py:graph --limit 1 --dry-run --mock-tools",
    );
    expect(s.noSandboxCommand).toBe(
      "python run_and_upload.py --agent agent.py:graph --limit 1 --dry-run --no-sandbox",
    );
    expect(s.file).toContain("tracer = trace.NoOpTracer()");
    expect(s.file).toContain("class MockTools(BaseHTTPRequestHandler):");
    expect(s.file).toContain(
      "elif args.mock_tools or (args.dry_run and not SANDBOX_URL):",
    );
    expect(s.file).toContain("if args.no_sandbox:");
    expect(s.file).toContain("def start_task(row):");
    expect(s.file).toContain("session = start_task(row)");
    expect(s.file).toContain('result["end_state"] = end_state(session)');
    expect(longestLine(s.file)).toBeLessThanOrEqual(88);
    const ts = snippetFor({
      kind: "code",
      entryId: "mastra",
      lang: "typescript",
      ids: { ...IDS, sandbox },
    });
    expect(ts.file).toContain("const session = await startTask(row);");
    expect(ts.steps[2].must).toBe("Your src/agent.ts must export agent");
    expect(ts.steps[2].code).toContain("createTool({");
    expect(ts.file).toContain(
      'else if (argv.includes("--mock-tools") || (dryRun && !SANDBOX_URL))',
    );
    expect(ts.file).toContain(
      'if (argv.includes("--no-sandbox")) SANDBOX_URL = "";',
    );
    // Without a sandbox the file is the repo example, byte for byte.
    expect(
      snippetFor({ kind: "code", entryId: "langgraph", ids: IDS }).file,
    ).not.toContain("SANDBOX_URL");
  });

  // dataset integration FE-0: there is no live connection; form=connect falls back to
  // the script steps (Your code itself is absent until FE-1).
  it("form=connect prints the script steps, never a connect command", () => {
    const s = snippetFor({
      kind: "code",
      entryId: "langgraph",
      form: "connect",
      ids: { ...IDS, token: "fic_test_placeholder" },
    });
    expect(s.steps.find((x) => x.id === "connect")).toBeUndefined();
    expect(JSON.stringify(s)).not.toMatch(/futureagi connect|\[connect\]/);
  });

  it("harness / ci / sandbox dispatch to lane Rb's slots", () => {
    ["harness"].forEach((kind) => {
      const s = snippetFor({
        kind,
        entryId: catalogEntry("claude_code").id,
        ids: IDS,
      });
      expect(Array.isArray(s.steps)).toBe(true);
    });
    expect(
      snippetFor({ kind: "code", entryId: "langgraph", mode: "ci", ids: IDS })
        .steps.length,
    ).toBeGreaterThan(0);
    expect(
      snippetFor({
        kind: "code",
        entryId: "langgraph",
        mode: "sandbox",
        ids: IDS,
      }).steps.length,
    ).toBeGreaterThan(0);
  });
});

describe("lines helpers", () => {
  it("commentLines wraps at 88", () => {
    const out = commentLines("word ".repeat(60));
    out.forEach((l) => expect(l.length).toBeLessThanOrEqual(88));
    expect(out.every((l) => l.startsWith("# "))).toBe(true);
  });
  it("maskSecret keeps the prefix and the last 4", () => {
    expect(maskSecret("fic_live_placeholder")).toBe("fic_live_…lder");
    expect(maskSecret("")).toBe("");
  });
});

// options-truth L2 G4 fix 9 (S5): the sandbox runs at most min(2 × vCPU,
// 10) tries at a time, and the drawer says so whenever a cap applies.
describe("sandbox at-a-time cap", () => {
  it("min(2 × vCPU, 10)", () => {
    expect([1, 2, 4, 8].map(sandboxAtATimeMax)).toEqual([2, 4, 8, 10]);
  });
  it("a value above the cap is clamped and the hint says so", () => {
    const l = cappedLimits({ tries: 3, concurrency: 7 }, { sandboxCpu: 1 });
    expect(l.concurrency).toBe(2);
    expect(l.concurrencyMax).toBe(2);
    expect(l.capLine).toBe("The sandbox runs at most 2 at a time (you set 7)");
    const ok = cappedLimits({ concurrency: 3 }, { sandboxCpu: 4 });
    expect(ok.concurrency).toBe(3);
    expect(ok.capLine).toBe("The sandbox runs at most 8 at a time");
  });
  it("an agent's own lower cap is named; no cap, no line", () => {
    const l = cappedLimits(
      { concurrency: 6, concurrencyMax: 4 },
      { agentName: "support_agent", machineName: "alice@mbp-14" },
    );
    expect(l.concurrency).toBe(4);
    expect(l.capLine).toBe(
      "support_agent on alice@mbp-14 runs at most 4 at a time (you set 6)",
    );
    expect(cappedLimits({ concurrency: 6 }).capLine).toBeNull();
  });
});
