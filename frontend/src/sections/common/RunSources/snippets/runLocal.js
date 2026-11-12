// IT-R1 G1 P1/P2: the three steps of `Run on your machine` (pure).
//
//   1 Install and set keys   install line + export lines (masked on screen,
//                            real on copy) + the task suite's sandbox
//   2 Point it at your agent a 6–10 line example of the file the script
//                            imports (`Your agent.py must expose graph`)
//   3 Run it and watch       the run command (the status checklist sits
//                            under it in the drawer)
//
// The complete script (run_and_upload.*) is unchanged and rides on
// `snippet.file`; the drawer shows it collapsed.
import {
  SANDBOX_COMMAND,
  SANDBOX_FILE,
  SANDBOX_HEALTH_URL,
  SANDBOX_PORT,
  sandboxServerScript,
} from "./sandbox";
import { lit, maskSecret } from "./lines";
import { pySample, tsSample } from "./agentSamples";

/**
 * The task suite's sandbox as the script sees it (null for other datasets,
 * and for an environment whose rows carry no start_state): `{name:
 * "acme-support-sandbox", envVar: "ACME_SANDBOX_URL", image, port, org:
 * "Acme", tools: [...], tables: [...]}`. Image and tools come from the
 * dataset's environment, never a literal.
 * @param {object|null} environment  the dataset's `environment`
 */
export function sandboxIdsOf(environment) {
  const name = environment?.mcp?.name;
  if (!name) return null;
  const tables = environment.start_state_tables ?? [];
  const props = Object.keys(environment.start_state_schema?.properties ?? {});
  if (!tables.length && !props.length) return null;
  const org = String(name).split(/[-_]/)[0] || "app";
  return {
    name,
    envVar: `${org.toUpperCase()}_SANDBOX_URL`,
    image: environment.image ?? null,
    port: 8080,
    org: org.charAt(0).toUpperCase() + org.slice(1),
    tools: [...(environment.mcp.tool_names ?? [])],
    tables: tables.length ? [...tables] : props,
  };
}

/** The sandbox line every surface uses (P2; G5 aligns the eval note). */
export const sandboxSentence = (sb) =>
  sb
    ? `Tools in this suite run against ${sb.name}; the script starts each task from its start_state and the end-state checks read the sandbox after your agent finishes.`
    : null;

/**
 * IT-R2 G1 P3: what the sandbox is, in one sentence (the sandbox step).
 * `The sandbox is a local copy of Acme's support system (orders, …). …`
 */
export const sandboxExplain = (sb) =>
  sb
    ? `The sandbox is a local copy of ${sb.org ?? "your"}'s support system${
        sb.tables?.length ? ` (${sb.tables.join(", ")})` : ""
      }. Your agent's tools call it; each task starts from its start_state and the checks read it afterwards.`
    : null;

/**
 * The dataset's text input columns, by name (`instruction`,
 * `customer_goal`, `order_id`), for the samples (pure).
 * @param {{name: string, role?: string, data_type?: string}[]} columns
 */
export function rowColumnsOf(columns) {
  return (columns ?? [])
    .filter(
      (c) =>
        c?.name &&
        (c.role ?? "input") === "input" &&
        ["text", "string", undefined].includes(c.data_type ?? undefined),
    )
    .map((c) => c.name)
    .slice(0, 4);
}

/**
 * Step 1 · `Install and set keys`: one block, masked keys on screen, the
 * real keys on Copy (and behind the eye toggle).
 */
export function installKeysStep({ install, ids = {} }) {
  const k = ids.keys ?? {};
  const api = k.apiKey || "";
  const secret = k.secretKey || "";
  const exp = (v) => (v ? lit(v) : '""');
  const real = [
    install,
    `export FI_API_KEY=${exp(api)}`,
    `export FI_SECRET_KEY=${exp(secret)}`,
  ].join("\n");
  const shown = [
    install,
    `export FI_API_KEY=${maskSecret(api) || "…"}`,
    `export FI_SECRET_KEY=${maskSecret(secret) || "…"}`,
  ].join("\n");
  return {
    n: 1,
    id: "install",
    title: "Install and set keys",
    lang: "bash",
    code: shown,
    revealText: real,
    copyText: real,
    masked: true,
    keysLink: "/dashboard/keys",
    note: null,
  };
}

/**
 * IT-R2 G1 P3 · `Start the sandbox` (task suites with a start_state only):
 * the image from the dataset, the env var the tools read, a health check,
 * and one sentence on what the sandbox is.
 */
export function sandboxStep(sb, n = 2) {
  if (!sb) return null;
  // options-truth L1 G2: a runnable stdlib sandbox (sandbox_server.py,
  // downloadable here and in examples/experiments/r4/tools), not an image.
  const lines = [
    `${SANDBOX_COMMAND} &`,
    `export ${sb.envVar}=http://127.0.0.1:${SANDBOX_PORT}`,
    `curl -fsS $${sb.envVar}/health   # check it is up`,
  ];
  return {
    n,
    id: "sandbox",
    title: "Start the sandbox",
    lang: "bash",
    code: lines.join("\n"),
    explain: sandboxExplain(sb),
    file: sandboxServerScript(sb),
    fileName: SANDBOX_FILE,
    healthUrl: SANDBOX_HEALTH_URL,
  };
}

const PY_EXAMPLES = {
  langgraph: [
    "from langchain_core.runnables import RunnableConfig",
    "from langchain_openai import ChatOpenAI",
    "from langgraph.graph import START, MessagesState, StateGraph",
    "",
    // L3 G4 C3: the run's params reach the node as config["configurable"]
    "def reply(state: MessagesState, config: RunnableConfig):",
    '    params = config.get("configurable", {})  # the run\'s params (--param)',
    '    llm = ChatOpenAI(model=params.get("model", "gpt-4.1-mini"),',
    '                     temperature=params.get("temperature", 0.2))',
    '    return {"messages": [llm.invoke(state["messages"])]}',
    "",
    "builder = StateGraph(MessagesState)",
    'builder.add_node("reply", reply)',
    'builder.add_edge(START, "reply")',
    "graph = builder.compile()",
  ],
  langchain: [
    "from langgraph.prebuilt import create_react_agent",
    "",
    "def lookup_order(order_id: str) -> str:",
    '    """Look up an order by id."""',
    '    return f"{order_id}: shipped"',
    "",
    'agent = create_react_agent("openai:gpt-4o-mini", tools=[lookup_order])',
  ],
  crewai: [
    "from crewai import Agent, Crew, Task",
    "",
    'support = Agent(role="Support agent", goal="Resolve the request",',
    '                backstory="You answer Acme customers.", llm="gpt-4.1-mini")',
    'answer = Task(description="Reply to: {input}",',
    '              expected_output="A reply", agent=support)',
    "crew = Crew(agents=[support], tasks=[answer])",
  ],
  openai_agents: [
    "from agents import Agent, function_tool",
    "",
    "@function_tool",
    "def lookup_order(order_id: str) -> str:",
    '    return f"{order_id}: shipped"',
    "",
    'agent = Agent(name="Support agent", instructions="Help the customer.",',
    "              tools=[lookup_order])",
  ],
  custom_python: [
    "def target(row):",
    '    """One row in, one reply out. Call your own agent here."""',
    '    return {"output": f"Echo: {row[\'input\']}"}',
  ],
};

const TS_EXAMPLES = {
  mastra: [
    'import { Agent } from "@mastra/core/agent";',
    'import { openai } from "@ai-sdk/openai";',
    "",
    "export const agent = new Agent({",
    '  name: "support-agent",',
    '  instructions: "Help the customer.",',
    '  model: openai("gpt-4o-mini"),',
    "});",
    "export default agent;",
  ],
  vercel_ai: [
    'import { generateText } from "ai";',
    'import { openai } from "@ai-sdk/openai";',
    "",
    "export async function run(text: string) {",
    "  const { text: reply } = await generateText({",
    '    model: openai("gpt-4o-mini"),',
    "    prompt: text,",
    "  });",
    "  return reply;",
    "}",
    "export default run;",
  ],
  langchain: [
    'import { createReactAgent } from "@langchain/langgraph/prebuilt";',
    'import { ChatOpenAI } from "@langchain/openai";',
    "",
    "export const agent = createReactAgent({",
    '  llm: new ChatOpenAI({ model: "gpt-4o-mini" }),',
    "  tools: [],",
    "});",
    "export default agent;",
  ],
  custom_typescript: [
    "type Row = { id: string; [column: string]: unknown };",
    "",
    "export async function target(row: Row) {",
    "  // one row in, one reply out: call your own agent here",
    '  return { output: `Echo: ${String(row.input ?? "")}` };',
    "}",
    "export default target;",
  ],
};

/** `agent.py` / `graph` from `agent.py:graph`. */
export function agentSpecParts(spec, fallback = "agent.py:agent") {
  const [file, name] = String(spec || fallback).split(":");
  return { file: file || "agent.py", name: name || "agent" };
}

/**
 * Step 2 · `Point it at your agent`: the file the script imports, as a
 * 6–10 line example; generic when no example is written for the entry.
 * @returns {{n, id, title, must, lang, code, fileName}}
 */
export function agentExampleStep({
  entry,
  lang = "python",
  ids = {},
  scriptName,
}) {
  const ts = lang === "typescript";
  const spec =
    entry?.agent?.spec ?? (ts ? "src/agent.ts:target" : "agent.py:target");
  const { file, name } = agentSpecParts(spec);
  const id = entry?.id;
  const sb = ids.sandbox ?? null;
  // IT-R2 G1 P3: the suite's tools and the dataset's columns, when known.
  const columns = ids.columns?.length
    ? ids.columns
    : ids.inputColumn
      ? [ids.inputColumn]
      : [];
  const generated = (ts ? tsSample : pySample)(id, { sandbox: sb, columns });
  const body = generated ?? (ts ? TS_EXAMPLES[id] : PY_EXAMPLES[id]) ?? null;
  const head = ts
    ? `// ${file} · ${scriptName ?? "run_and_upload.ts"} imports \`${name}\` from here`
    : `# ${file} · ${scriptName ?? "run_and_upload.py"} imports \`${name}\` from here`;
  const generic = ts
    ? [
        `// your ${entry?.name ?? "agent"} object, built the way you build it today`,
        `export const ${name} = /* … */ undefined as any;`,
        `export default ${name};`,
        `// the script calls: ${entry?.agent?.call ?? `${name}(row)`}`,
      ]
    : [
        `# your ${entry?.name ?? "agent"} object, built the way you build it today`,
        `${name} = ...`,
        `# the script calls: ${entry?.agent?.call ?? `${name}(row)`}`,
      ];
  const sandboxNote =
    sb && !generated
      ? [
          ts
            ? `// tools call the sandbox: fetch(\`\${process.env.${sb.envVar}}/tools/lookup_order\`, …)`
            : `# tools call the sandbox: requests.post(os.environ["${sb.envVar}"] + "/tools/…")`,
        ]
      : [];
  return {
    n: ids.sandbox ? 3 : 2,
    id: "agent",
    title: "Point it at your agent",
    must: ts
      ? `Your ${file} must export ${name}`
      : `Your ${file} must expose ${name}`,
    lang: ts ? "typescript" : "python",
    code: `${[head, ...(body ?? generic), ...sandboxNote].join("\n")}\n`,
    fileName: file,
  };
}

/**
 * IT-R3 G1 P2: the agent contract of `Point it at your agent`, in three
 * lines (pure): how the runner calls the agent and reads its answer, where
 * the tools go, and what to edit for another shape (true for every
 * generated runner: the call and the answer are two lines of run_rows()).
 * @returns {{call: string, answer: string, lines: string[]}}
 */
export function agentContract({
  entry,
  lang = "python",
  ids = {},
  scriptName,
} = {}) {
  const ts = lang === "typescript";
  const spec =
    entry?.agent?.spec ?? (ts ? "src/agent.ts:target" : "agent.py:target");
  const { name } = agentSpecParts(spec);
  const input = `<${ids.inputColumn || "input"}>`;
  const v = entry?.agent?.var;
  let call = String(entry?.agent?.call ?? `${name}(row)`);
  if (v && v !== name) call = call.replace(new RegExp(`\\b${v}\\b`), name);
  call = call.replace(/\btext\b/, input);
  const out = String(entry?.agent?.output ?? "");
  const answer =
    /messages/.test(out) && /-1|at\(-1\)|\.at\(|length - 1/.test(out)
      ? "the last message"
      : out
        ? `\`${out.replace(/\bout\b/, "result")}\``
        : "the return value";
  const sb = ids.sandbox ?? null;
  const file = scriptName ?? (ts ? "run_and_upload.ts" : "run_and_upload.py");
  const fn = ts ? "runRows()" : "run_rows()";
  const lines = [
    `We call ${call} once per row and read ${answer} as the answer.`,
    sb
      ? `Your tools call $${sb.envVar}/tools/<name>, so the checks can read the end state.`
      : null,
    `Different input or output shape? Edit the two lines of ${fn} in ${file} that call your agent and read its answer.`,
  ].filter(Boolean);
  return { call, answer, lines };
}

/**
 * IT-R2 G1 P3: what `--dry-run` does, exactly (pure). A task suite's script
 * builds no tracer on --dry-run (Mastra's own exporter still traces), so it
 * sends nothing; other scripts still trace. `mock` explains --mock-tools.
 */
export function dryRunNote({ sandbox, lang, entryId, rows = 3 } = {}) {
  const quiet =
    Boolean(sandbox) && !(lang === "typescript" && entryId === "mastra");
  return {
    dry: quiet
      ? `runs ${rows} rows on your machine and prints the results and the request. It needs no keys and sends nothing to Future AGI (no traces, no results); without the sandbox it serves the suite's tools itself.`
      : `runs ${rows} rows on your machine and prints the results instead of uploading them (traces still go to your Future AGI project).`,
    mock: sandbox
      ? `No sandbox needed: the script serves the suite's ${sandbox.tools?.length ?? 0} tools from each task's start_state; end-state checks are skipped. --no-sandbox runs with no tool server at all.`
      : null,
  };
}

/** Step 3 (4 with a sandbox) · `Run it and watch`: the run command. */
export const runWatchStep = (command, n = 3) => ({
  n,
  id: "run",
  title: "Run it and watch",
  lang: "bash",
  code: command,
});

const cellOf = (v) => {
  const x = v && typeof v === "object" && "cell_value" in v ? v.cell_value : v;
  if (x === undefined) return null;
  if (typeof x === "string") {
    const t = x.trim();
    if ((t.startsWith("{") && t.endsWith("}")) || t.startsWith("[")) {
      try {
        return JSON.parse(t);
      } catch {
        return x;
      }
    }
  }
  return x;
};

/**
 * `sample_rows.jsonl` for `--dry-run`: the first `n` rows in the shape
 * fetch_rows() yields (`{"id": row id, <column name>: value}`).
 * @param {object[]} rows  table rows (`row_id` + cells by column id)
 * @param {{id: string, name: string}[]} columns
 */
export function sampleRowsJsonl(rows, columns, n = 3) {
  return (rows ?? [])
    .slice(0, n)
    .map((r) => {
      const item = { id: r.row_id ?? r.id };
      (columns ?? []).forEach((c) => {
        if (!c?.id || !c?.name) return;
        const v = cellOf(r[c.id]);
        if (v !== null) item[c.name] = v;
      });
      return JSON.stringify(item);
    })
    .join("\n")
    .concat(rows?.length ? "\n" : "");
}
