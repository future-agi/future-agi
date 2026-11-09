// IT-R2 G1 P3: the `Point it at your agent` samples, generated from the
// dataset (pure). A task suite's sample is a tool-calling agent whose tools
// are the suite's own (`lookup_order`, `update_address`, … from the
// environment's `mcp.tool_names`), each a 2-line HTTP call to the sandbox;
// a custom `target(row)` reads the dataset's real columns (`row.instruction`,
// `row.customer_goal`, `row.order_id`), never `row.input`. Without a
// sandbox the framework samples keep their short shape (runLocal.js).

import { SANDBOX_PORT } from "./sandbox";

/** Argument names and one-line docs of the suite's tools. */
export const TOOL_SIGS = Object.freeze({
  lookup_order: [["order_id"], "Look up an order: status, items, shipping."],
  update_address: [["order_id", "address"], "Change an order's address."],
  issue_refund: [["order_id", "amount"], "Refund an order (amount in USD)."],
  lookup_customer: [["customer_id"], "Look up a customer and their orders."],
  issue_store_credit: [["customer_id", "amount"], "Give store credit."],
  cancel_item: [["order_id", "sku"], "Cancel one item of an order."],
  schedule_callback: [["customer_id", "slot"], "Book a callback slot."],
  create_ticket: [["customer_id", "summary"], "Open a support ticket."],
  escalate_to_human: [["reason"], "Hand the case to a person."],
  search_kb: [["query"], "Search the help-centre articles."],
  send_email: [["to", "body"], "Email the customer."],
  apply_discount: [["order_id", "percent"], "Apply a discount to an order."],
});

const FIRST = ["lookup_order", "update_address", "issue_refund"];

/** The tools a sample spells out (3), then how many more follow the pattern. */
export function sampleTools(tools = [], n = 3) {
  const all = [...new Set(tools)].filter(Boolean);
  const picked = [
    ...FIRST.filter((t) => all.includes(t)),
    ...all.filter((t) => !FIRST.includes(t)),
  ].slice(0, n);
  return { picked, more: all.filter((t) => !picked.includes(t)) };
}

const sigOf = (name) => TOOL_SIGS[name] ?? [["value"], `Call ${name}.`];

const moreLine = (more, mark) =>
  more.length
    ? `${mark} + ${more.length} more of the suite's tools, same pattern: ${more
        .slice(0, 3)
        .join(", ")}${more.length > 3 ? ", …" : ""}`
    : null;

// ------------------------------------------------------------------ Python
// The sandbox step serves the tools on 127.0.0.1:8765 (sandbox.js); the
// runner sets the variable for its rows, and the same default lets
// serve_agent.py import this file as written (L3 G4 C3).
const PY_CALL = (sb) => [
  `# the sandbox step starts it (the runner sets ${sb.envVar} for each run)`,
  `SANDBOX = os.environ.get("${sb.envVar}", "http://127.0.0.1:${SANDBOX_PORT}")`,
  "",
  "",
  "def call(tool, **args):  # every tool is one POST to the sandbox",
  '    return requests.post(f"{SANDBOX}/tools/{tool}", json=args, timeout=30).json()',
];

function pyTools(picked, decorator) {
  const out = [];
  picked.forEach((name) => {
    const [args, doc] = sigOf(name);
    out.push("", "");
    out.push(decorator(name));
    out.push(
      `def ${name}(${args.map((a) => `${a}: str`).join(", ")}) -> dict:`,
    );
    out.push(`    """${doc}"""`);
    out.push(
      `    return call("${name}", ${args.map((a) => `${a}=${a}`).join(", ")})`,
    );
  });
  return out;
}

const pyToolsList = (picked, more) => [
  "",
  "",
  `TOOLS = [${picked.join(", ")}]`,
  ...[moreLine(more, "#")].filter(Boolean),
];

/** A target(row) that reads the dataset's columns (Python). */
function pyTarget(columns, sb) {
  const cols = columns.length ? columns : ["input"];
  const idCol = cols.find((c) => /_id$/.test(c));
  const body = [
    "def target(row):",
    '    """One task in, one reply out. Call your own agent here."""',
    ...cols.map((c) => `    ${c} = row[${JSON.stringify(c)}]`),
  ];
  if (sb && idCol && /order/.test(idCol))
    body.push(
      `    order = call("lookup_order", order_id=${idCol})  # a sandbox tool`,
    );
  body.push(
    `    reply = f"Working on it: {${cols[0]}}"  # your agent's reply here`,
  );
  body.push('    return {"output": reply}');
  return body;
}

// options-truth L2 G4 (C2): runnable samples for the six frameworks whose
// example was `x = ...`. Each uses the suite's tools (the sandbox wrappers
// above) and exposes exactly what the runner's contract line calls.
const PY_LOCAL_CALL = [
  "def call(tool, **args):  # your own systems answer here",
  '    return {"tool": tool, "ok": True, **args}',
];

/** Plain typed functions (LlamaIndex, ADK, AutoGen, DSPy read the hints). */
function pyFns(picked, { argsDoc = false } = {}) {
  const out = [];
  picked.forEach((name) => {
    const [args, doc] = sigOf(name);
    out.push("", "");
    if (argsDoc) out.push("@tool");
    out.push(
      `def ${name}(${args.map((a) => `${a}: str`).join(", ")}) -> dict:`,
    );
    if (argsDoc) {
      out.push(`    """${doc}`, "", "    Args:");
      args.forEach((a) =>
        out.push(`        ${a}: the ${a.replace(/_/g, " ")}.`),
      );
      out.push('    """');
    } else out.push(`    """${doc}"""`);
    out.push(
      `    return call("${name}", ${args.map((a) => `${a}=${a}`).join(", ")})`,
    );
  });
  return out;
}

const SIX = new Set([
  "llamaindex",
  "google_adk",
  "autogen",
  "smolagents",
  "dspy",
  "haystack",
]);

function sixSample(id, sb) {
  const tools = sb?.tools?.length ? sb.tools : ["lookup_order"];
  const { picked, more } = sampleTools(tools);
  const head = sb ? ["import os", "", "import requests"] : [];
  const callLines = sb ? PY_CALL(sb) : PY_LOCAL_CALL;
  const list = pyToolsList(picked, more);
  const ask = "Resolve the support task with the tools.";
  if (id === "llamaindex")
    return [
      ...head,
      "from llama_index.core.agent.workflow import FunctionAgent",
      "from llama_index.llms.openai import OpenAI",
      "",
      ...callLines,
      ...pyFns(picked),
      ...list,
      "# the runner calls: asyncio.run(agent.run(user_msg=text))",
      "agent = FunctionAgent(",
      "    tools=TOOLS,",
      '    llm=OpenAI(model="gpt-4o-mini"),',
      `    system_prompt="${ask}",`,
      ")",
    ];
  if (id === "google_adk")
    return [
      ...head,
      "from google.adk.agents import LlmAgent",
      "",
      ...callLines,
      ...pyFns(picked),
      ...list,
      "# the runner calls root_agent through InMemoryRunner (run_once)",
      "root_agent = LlmAgent(",
      '    name="support_agent",',
      '    model="gemini-2.5-flash",',
      `    instruction="${ask}",`,
      "    tools=TOOLS,",
      ")",
    ];
  if (id === "autogen")
    return [
      ...head,
      "from autogen_agentchat.agents import AssistantAgent",
      "from autogen_agentchat.conditions import MaxMessageTermination",
      "from autogen_agentchat.teams import RoundRobinGroupChat",
      "from autogen_ext.models.openai import OpenAIChatCompletionClient",
      "",
      ...callLines,
      ...pyFns(picked),
      ...list,
      'model_client = OpenAIChatCompletionClient(model="gpt-4o-mini")',
      "support = AssistantAgent(",
      '    "support",',
      "    model_client=model_client,",
      "    tools=TOOLS,",
      `    system_message="${ask}",`,
      "    reflect_on_tool_use=True,",
      ")",
      "# the runner calls: asyncio.run(team.run(task=text))",
      "team = RoundRobinGroupChat(",
      "    [support], termination_condition=MaxMessageTermination(4)",
      ")",
    ];
  if (id === "smolagents")
    return [
      ...head,
      "from smolagents import CodeAgent, OpenAIServerModel, tool",
      "",
      ...callLines,
      ...pyFns(picked, { argsDoc: true }),
      ...list,
      "# the runner calls: agent.run(text)",
      "agent = CodeAgent(",
      '    tools=TOOLS, model=OpenAIServerModel(model_id="gpt-4o-mini")',
      ")",
    ];
  if (id === "dspy")
    return [
      ...head,
      "import dspy",
      "",
      'dspy.configure(lm=dspy.LM("openai/gpt-4o-mini"))',
      "",
      ...callLines,
      ...pyFns(picked),
      ...list,
      "# the runner calls: program(question=text).answer",
      'program = dspy.ReAct("question -> answer", tools=TOOLS)',
    ];
  if (id === "haystack")
    return [
      ...head,
      "from haystack import Pipeline",
      "from haystack.components.builders import ChatPromptBuilder",
      "from haystack.components.generators.chat import OpenAIChatGenerator",
      "from haystack.components.joiners import ListJoiner",
      "from haystack.components.tools import ToolInvoker",
      "from haystack.dataclasses import ChatMessage",
      "from haystack.tools import create_tool_from_function",
      "",
      ...callLines,
      ...pyFns(picked),
      "",
      "",
      `TOOLS = [create_tool_from_function(f) for f in (${picked.join(", ")},)]`,
      ...[moreLine(more, "#")].filter(Boolean),
      "PROMPT = [",
      `    ChatMessage.from_system("${ask}"),`,
      '    ChatMessage.from_user("{{ question }}"),',
      "]",
      "",
      "# prompt_builder → planner (picks tools) → tool_invoker → llm (answers)",
      '# the runner calls: pipeline.run({"prompt_builder": {"question": text}})',
      "pipeline = Pipeline()",
      'pipeline.add_component("prompt_builder", ChatPromptBuilder(template=PROMPT))',
      'pipeline.add_component("planner", OpenAIChatGenerator(model="gpt-4o-mini", tools=TOOLS))',
      'pipeline.add_component("tool_invoker", ToolInvoker(tools=TOOLS))',
      'pipeline.add_component("history", ListJoiner(list[ChatMessage]))',
      'pipeline.add_component("llm", OpenAIChatGenerator(model="gpt-4o-mini"))',
      'pipeline.connect("prompt_builder.prompt", "planner.messages")',
      'pipeline.connect("planner.replies", "tool_invoker.messages")',
      'pipeline.connect("prompt_builder.prompt", "history")',
      'pipeline.connect("planner.replies", "history")',
      'pipeline.connect("tool_invoker.tool_messages", "history")',
      'pipeline.connect("history.values", "llm.messages")',
    ];
  return null;
}

/** Python sample lines for an entry, or null (runLocal keeps its own). */
/**
 * The LangGraph node that calls the model (L3 G4 C3): the run's params
 * (`--param model=…`, the UI's values) arrive as the graph's
 * `config["configurable"]`, so `model` and `temperature` change the call;
 * the defaults are the declared ones (gpt-4.1-mini, 0.2).
 */
export const LANGGRAPH_MODEL_NODE = (name, bind = "") => [
  "",
  "",
  `def ${name}(state: MessagesState, config: RunnableConfig):`,
  '    params = config.get("configurable", {})  # the run\'s params (--param)',
  '    llm = ChatOpenAI(model=params.get("model", "gpt-4.1-mini"),',
  `                     temperature=params.get("temperature", 0.2))${bind}`,
  '    return {"messages": [llm.invoke(state["messages"])]}',
];

export function pySample(id, { sandbox: sb, columns = [] } = {}) {
  if (SIX.has(id)) return sixSample(id, sb);
  if (id === "custom_python") {
    if (!sb && !columns.length) return null;
    return [
      ...(sb ? ["import os", "", "import requests", "", ...PY_CALL(sb)] : []),
      ...(sb ? ["", ""] : []),
      ...pyTarget(columns, sb),
    ];
  }
  if (!sb?.tools?.length) return null;
  const { picked, more } = sampleTools(sb.tools);
  if (id === "langgraph")
    return [
      "import os",
      "",
      "import requests",
      "from langchain_core.runnables import RunnableConfig",
      "from langchain_core.tools import tool",
      "from langchain_openai import ChatOpenAI",
      "from langgraph.graph import START, MessagesState, StateGraph",
      "from langgraph.prebuilt import ToolNode, tools_condition",
      "",
      ...PY_CALL(sb),
      ...pyTools(picked, () => "@tool"),
      ...pyToolsList(picked, more),
      ...LANGGRAPH_MODEL_NODE("model", ".bind_tools(TOOLS)"),
      "",
      "",
      "builder = StateGraph(MessagesState)",
      'builder.add_node("model", model)',
      'builder.add_node("tools", ToolNode(TOOLS))',
      'builder.add_edge(START, "model")',
      'builder.add_conditional_edges("model", tools_condition)',
      'builder.add_edge("tools", "model")',
      "graph = builder.compile()",
    ];
  if (id === "langchain")
    return [
      "import os",
      "",
      "import requests",
      "from langchain_core.tools import tool",
      "from langgraph.prebuilt import create_react_agent",
      "",
      ...PY_CALL(sb),
      ...pyTools(picked, () => "@tool"),
      ...pyToolsList(picked, more),
      'agent = create_react_agent("openai:gpt-4o-mini", tools=TOOLS)',
    ];
  if (id === "openai_agents")
    return [
      "import os",
      "",
      "import requests",
      "from agents import Agent, function_tool",
      "",
      ...PY_CALL(sb),
      ...pyTools(picked, () => "@function_tool"),
      ...pyToolsList(picked, more),
      'agent = Agent(name="Support agent", instructions="Resolve the task.",',
      "              tools=TOOLS)",
    ];
  if (id === "crewai")
    return [
      "import os",
      "",
      "import requests",
      "from crewai import Agent, Crew, Task",
      "from crewai.tools import tool",
      "",
      ...PY_CALL(sb),
      ...pyTools(picked, (n) => `@tool(${JSON.stringify(n)})`),
      ...pyToolsList(picked, more),
      'support = Agent(role="Support agent", goal="Resolve the task",',
      '                backstory="You help Acme customers.", tools=TOOLS,',
      '                llm="gpt-4.1-mini")  # the model it calls (its key: step 2)',
      "# {input} is the task's instruction column (INPUT_COLUMN in the script)",
      'task = Task(description="Resolve this support task: {input}",',
      '            expected_output="What you did and the reply", agent=support)',
      "crew = Crew(agents=[support], tasks=[task])",
    ];
  return null;
}

// -------------------------------------------------------------- TypeScript
const TS_CALL = (sb) => [
  `const SANDBOX = process.env.${sb.envVar}!; // the sandbox step starts it`,
  "// every tool is one POST to the sandbox",
  "const call = async (tool: string, args: object) =>",
  "  (await fetch(`${SANDBOX}/tools/${tool}`, {",
  '    method: "POST",',
  '    headers: { "Content-Type": "application/json" },',
  "    body: JSON.stringify(args),",
  "  })).json();",
];

const zodOf = (args) =>
  `z.object({ ${args.map((a) => `${a}: z.string()`).join(", ")} })`;

function tsTools(picked, shape) {
  const out = [];
  picked.forEach((name) => {
    const [args, doc] = sigOf(name);
    out.push("");
    out.push(...shape(name, args, doc));
  });
  return out;
}

/** A target(row) that reads the dataset's columns (TypeScript). */
function tsTarget(columns, sb) {
  const cols = columns.length ? columns : ["input"];
  const idCol = cols.find((c) => /_id$/.test(c));
  return [
    `type Row = { id: string; ${cols.map((c) => `${c}: string`).join("; ")} };`,
    "",
    "export async function target(row: Row) {",
    "  // one task in, one reply out: call your own agent here",
    `  const { ${cols.join(", ")} } = row;`,
    ...(sb && idCol && /order/.test(idCol)
      ? [
          `  const order = await call("lookup_order", { ${idCol === "order_id" ? "order_id" : `order_id: ${idCol}`} }); // a tool`,
        ]
      : []),
    `  return { output: \`Working on it: \${${cols[0]}}\` };`,
    "}",
    "export default target;",
  ];
}

/** TypeScript sample lines for an entry, or null (runLocal keeps its own). */
export function tsSample(id, { sandbox: sb, columns = [] } = {}) {
  if (id === "custom_typescript") {
    if (!sb && !columns.length) return null;
    return [...(sb ? [...TS_CALL(sb), ""] : []), ...tsTarget(columns, sb)];
  }
  if (!sb?.tools?.length) return null;
  const { picked, more } = sampleTools(sb.tools);
  const list = [
    "",
    `const tools = { ${picked.join(", ")} };`,
    ...[moreLine(more, "//")].filter(Boolean),
  ];
  if (id === "mastra")
    return [
      'import { Agent } from "@mastra/core/agent";',
      'import { createTool } from "@mastra/core/tools";',
      'import { openai } from "@ai-sdk/openai";',
      'import { z } from "zod";',
      "",
      ...TS_CALL(sb),
      ...tsTools(picked, (name, args, doc) => [
        `const ${name} = createTool({`,
        `  id: "${name}",`,
        `  description: "${doc}",`,
        `  inputSchema: ${zodOf(args)},`,
        `  execute: async ({ context }) => call("${name}", context),`,
        "});",
      ]),
      ...list,
      "",
      "export const agent = new Agent({",
      '  name: "support-agent",',
      '  instructions: "Resolve the task with the tools.",',
      '  model: openai("gpt-4o-mini"),',
      "  tools,",
      "});",
      "export default agent;",
    ];
  if (id === "vercel_ai")
    return [
      'import { generateText, tool } from "ai";',
      'import { openai } from "@ai-sdk/openai";',
      'import { z } from "zod";',
      "",
      ...TS_CALL(sb),
      ...tsTools(picked, (name, args, doc) => [
        `const ${name} = tool({`,
        `  description: "${doc}",`,
        `  parameters: ${zodOf(args)},`,
        `  execute: (args) => call("${name}", args),`,
        "});",
      ]),
      ...list,
      "",
      "export async function run(text: string) {",
      "  const { text: reply } = await generateText({",
      '    model: openai("gpt-4o-mini"),',
      "    tools,",
      "    maxSteps: 8,",
      "    prompt: text,",
      "  });",
      "  return reply;",
      "}",
      "export default run;",
    ];
  if (id === "langchain")
    return [
      'import { createReactAgent } from "@langchain/langgraph/prebuilt";',
      'import { tool } from "@langchain/core/tools";',
      'import { ChatOpenAI } from "@langchain/openai";',
      'import { z } from "zod";',
      "",
      ...TS_CALL(sb),
      ...tsTools(picked, (name, args, doc) => [
        `const ${name} = tool((args) => call("${name}", args), {`,
        `  name: "${name}",`,
        `  description: "${doc}",`,
        `  schema: ${zodOf(args)},`,
        "});",
      ]),
      "",
      `const tools = [${picked.join(", ")}];`,
      ...[moreLine(more, "//")].filter(Boolean),
      "",
      "export const agent = createReactAgent({",
      '  llm: new ChatOpenAI({ model: "gpt-4o-mini" }),',
      "  tools,",
      "});",
      "export default agent;",
    ];
  return null;
}

/**
 * The models a sample agent file calls (L3 G4 C2), read from its text
 * (`ChatOpenAI(model="gpt-4o-mini")` → `gpt-4o-mini`), in order of
 * appearance; [] when it names none (an OpenTelemetry framework's
 * `agent = ...`). `known` = the model ids to look for
 * (declaredUses.MODEL_PRICES keys). Pure.
 */
export function modelsInText(text, known = []) {
  const t = String(text ?? "");
  const hits = [];
  [...known]
    .sort((a, b) => b.length - a.length)
    .forEach((m) => {
      // `"gpt-4o-mini"`, also provider-prefixed (`"openai/gpt-4o-mini"`,
      // `"openai:gpt-4o-mini"`)
      const re = new RegExp(
        `["'\`](?:[\\w-]+[/:])?${m.replace(/[.]/g, "\\.")}["'\`]`,
      );
      const at = t.search(re);
      if (at >= 0) hits.push({ m, at });
    });
  return hits.sort((a, b) => a.at - b.at).map((h) => h.m);
}
