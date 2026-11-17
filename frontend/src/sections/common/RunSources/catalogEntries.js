// Run-source catalog data (R4-SPEC D2, D3, D5, D12, D20, D23).
// Pure data: catalog.js builds the API on top.
//
// Facts:
// - traceAI package / import names are copied from unified-docs
//   `src/pages/docs/integrations/traceai/*.mdx` (checked Oct 17). LiveKit and
//   Pipecat expose `enable_http_attribute_mapping` (the brief's `enable` is
//   not in the docs).
// - graph quality is filled only from verified facts (V18): no traceAI
//   instrumentor is verified to emit `graph.node.*`, so auto-traced
//   frameworks are `parent_child`; OTel ones are `unknown`; only
//   code that sets `graph.node.*` itself (Custom Python / TypeScript) is
//   `full`. Harnesses without a transcript are `model_calls` (gateway only).
// - harness CLI lines: Claude Code and Codex CLI flags are the documented
//   headless ones; the rest are `approximate` (check your CLI version).
// - logos: `logo.mark` names the brand's own mark, bundled in brandLogos.js
//   (official source + licence per mark there; nothing fetched at runtime).
//   `color` is a one-colour mark's official colour when it reaches 3:1 on
//   white and on dark, else null (= text.primary); multi-colour marks keep
//   their colours. `monogram` shows only for an entry with no bundled mark.

const DOCS = "https://docs.futureagi.com/docs/integrations/traceai";

/** How a code entry is traced (catalog badge). */
export const BADGES = Object.freeze({
  auto: "Traced automatically", // IT-R1 G1 P3 (was `Auto-traced`)
  // options-truth L1 C11: the badge keeps the fact, not a status.
  otel_preview: "OTel",
  manual: "Manual spans",
  otel: "OTel",
});

/** The OTel badge's tooltip: how such a framework is traced. */
export const OTEL_PREVIEW_REASON =
  "Traced through OpenTelemetry (no traceAI package)";

/** Execution modes (R4-SPEC D5; ids frozen). */
export const EXECUTION_MODES = Object.freeze([
  { id: "platform", label: "Future AGI", short: "Runs in Future AGI" },
  { id: "local", label: "This machine", short: "Runs on this machine" },
  { id: "ci", label: "CI", short: "Runs in CI" },
  {
    id: "sandbox",
    label: "Future AGI sandbox",
    short: "Runs in the Future AGI sandbox",
  },
  {
    id: "remote",
    label: "Remote endpoint",
    short: "Runs at a remote endpoint",
  },
]);

// The modes each kind can run in (D5 table), in `Change` menu order.
export const KIND_MODES = Object.freeze({
  prompt: ["platform"],
  agent: ["platform"],
  code: ["local", "ci", "sandbox", "remote"],
  harness: ["local", "ci", "sandbox"],
  http: ["remote"],
  a2a: ["remote"],
  mcp_tool: ["remote"],
  traces: [],
  upload: [],
});

const py = (pkg, cls, mod = pkg.toLowerCase().replace(/-/g, "_")) => ({
  pip: pkg,
  import: `from ${mod} import ${cls}`,
  call: `${cls}().instrument(tracer_provider=trace_provider)`,
});
const autoGraph = (pkg) => ({
  quality: "parent_child",
  source: `${pkg} · parent → child spans`,
});
const OTEL_GRAPH = {
  quality: "unknown",
  source: "framework OpenTelemetry spans",
};

const CODE_MODES = {
  modes: KIND_MODES.code,
  defaultMode: "local",
  primaryModes: ["local", "ci"],
};

const framework = (e) => ({
  kind: "code",
  custom: false,
  status: "today",
  languages: ["python"],
  snippetKey: "python",
  ...CODE_MODES,
  ...e,
});

/** Frameworks (20): code card. */
export const FRAMEWORKS = Object.freeze([
  framework({
    id: "langgraph",
    group: "python_auto",
    name: "LangGraph",
    badge: "auto",
    logo: { mark: "langgraph", monogram: "LG", color: null },
    instrument: py("traceAI-langchain", "LangChainInstrumentor"),
    docs: `${DOCS}/langgraph`,
    graph: autoGraph("traceAI-langchain"),
    agent: {
      spec: "agent.py:graph",
      var: "app",
      call: 'app.invoke({"messages": [("user", text)]})',
      output: 'out["messages"][-1].content',
      source: "docs.futureagi.com/docs/integrations/traceai/langgraph",
    },
    aliases: ["langchain graph", "stategraph"],
  }),
  framework({
    id: "langchain",
    group: "python_auto",
    name: "LangChain",
    badge: "auto",
    languages: ["python", "typescript"],
    logo: { mark: "langchain", monogram: "LC", color: null },
    instrument: py("traceAI-langchain", "LangChainInstrumentor"),
    docs: `${DOCS}/langchain`,
    graph: autoGraph("traceAI-langchain"),
    agent: {
      spec: "agent.py:agent",
      var: "agent",
      call: 'agent.invoke({"messages": [("user", text)]})',
      output: 'out["messages"][-1].content',
      source: "LangChain agents quickstart (create_agent)",
      check: true,
    },
  }),
  framework({
    id: "llamaindex",
    group: "python_auto",
    name: "LlamaIndex",
    badge: "auto",
    logo: { mark: "llamaindex", monogram: "LI", color: null },
    instrument: py("traceAI-llamaindex", "LlamaIndexInstrumentor"),
    docs: `${DOCS}/llamaindex`,
    graph: autoGraph("traceAI-llamaindex"),
    agent: {
      spec: "agent.py:agent",
      var: "agent",
      call: "asyncio.run(agent.run(user_msg=text))",
      output: "str(out)",
      imports: ["import asyncio"],
      source: "LlamaIndex FunctionAgent quickstart",
      check: true,
    },
    aliases: ["llama index", "workflows"],
  }),
  framework({
    id: "crewai",
    group: "python_auto",
    name: "CrewAI",
    badge: "auto",
    logo: { mark: "crewai", monogram: "CR", color: "#FF5A50" },
    instrument: py("traceAI-crewai", "CrewAIInstrumentor"),
    docs: `${DOCS}/crewai`,
    graph: autoGraph("traceAI-crewai"),
    agent: {
      spec: "crew.py:crew",
      var: "crew",
      call: 'crew.kickoff(inputs={"input": text})',
      output: "out.raw",
      source: "docs.futureagi.com/docs/integrations/traceai/crewai",
    },
  }),
  framework({
    id: "openai_agents",
    group: "python_auto",
    name: "OpenAI Agents SDK",
    short: "OpenAI Agents",
    badge: "auto",
    logo: { mark: "openai_agents", monogram: "OA", color: null },
    instrument: py("traceAI-openai-agents", "OpenAIAgentsInstrumentor"),
    docs: `${DOCS}/openai_agents`,
    graph: autoGraph("traceAI-openai-agents"),
    agent: {
      spec: "agent.py:agent",
      var: "agent",
      call: "Runner.run_sync(agent, text)",
      output: "out.final_output",
      imports: ["from agents import Runner"],
      source: "docs.futureagi.com/docs/integrations/traceai/openai_agents",
    },
    aliases: ["openai agents", "agents sdk", "handoffs"],
  }),
  framework({
    id: "google_adk",
    group: "python_auto",
    name: "Google ADK",
    badge: "auto",
    logo: { mark: "google_adk", monogram: "AD", color: null },
    instrument: py("traceAI-google-adk", "GoogleADKInstrumentor"),
    docs: `${DOCS}/google_adk`,
    graph: autoGraph("traceAI-google-adk"),
    agent: {
      spec: "agent.py:root_agent",
      var: "agent",
      call: "asyncio.run(run_once(agent, text))",
      output: "out",
      imports: [
        "import asyncio",
        "from google.adk.runners import InMemoryRunner",
        "from google.genai import types",
      ],
      helper: [
        "async def run_once(agent, text):",
        "    runner = InMemoryRunner(agent=agent)",
        "    session = await runner.session_service.create_session(",
        '        app_name=runner.app_name, user_id="fi"',
        "    )",
        '    msg = types.Content(role="user", parts=[types.Part(text=text)])',
        '    final = ""',
        "    async for event in runner.run_async(",
        '        user_id="fi", session_id=session.id, new_message=msg',
        "    ):",
        "        if event.is_final_response() and event.content:",
        "            final = event.content.parts[0].text",
        "    return final",
      ],
      source: "Google ADK quickstart (InMemoryRunner + session)",
      check: true,
    },
    aliases: ["adk", "agent development kit"],
  }),
  framework({
    id: "autogen",
    group: "python_auto",
    name: "AutoGen",
    badge: "auto",
    logo: { mark: "autogen", monogram: "AG", color: null },
    instrument: py("traceAI-autogen", "AutogenInstrumentor"),
    docs: `${DOCS}/autogen`,
    graph: autoGraph("traceAI-autogen"),
    agent: {
      spec: "team.py:team",
      var: "team",
      call: "asyncio.run(team.run(task=text))",
      output: "out.messages[-1].content",
      imports: ["import asyncio"],
      source: "AutoGen AgentChat quickstart",
      check: true,
    },
  }),
  framework({
    id: "smolagents",
    group: "python_auto",
    name: "smolagents",
    badge: "auto",
    logo: { mark: "smolagents", monogram: "SA", color: null },
    instrument: py("traceAI-smolagents", "SmolagentsInstrumentor"),
    docs: `${DOCS}/smol_agents`,
    graph: autoGraph("traceAI-smolagents"),
    agent: {
      spec: "agent.py:agent",
      var: "agent",
      call: "agent.run(text)",
      output: "str(out)",
      source: "smolagents quickstart",
      check: true,
    },
    aliases: ["hugging face", "smol"],
  }),
  framework({
    id: "dspy",
    group: "python_auto",
    name: "DSPy",
    badge: "auto",
    logo: { mark: "dspy", monogram: "DS", color: "#EF4036" },
    instrument: py("traceAI-DSPy", "DSPyInstrumentor", "traceai_dspy"),
    docs: `${DOCS}/dspy`,
    graph: autoGraph("traceAI-DSPy"),
    agent: {
      spec: "program.py:program",
      var: "program",
      call: "program(question=text)",
      output: "out.answer",
      source: "DSPy quickstart (dspy.Predict)",
      check: true,
    },
  }),
  framework({
    id: "haystack",
    group: "python_auto",
    name: "Haystack",
    badge: "auto",
    logo: { mark: "haystack", monogram: "HS", color: null },
    instrument: py("traceAI-haystack", "HaystackInstrumentor"),
    docs: `${DOCS}/haystack`,
    graph: autoGraph("traceAI-haystack"),
    agent: {
      spec: "pipeline.py:pipeline",
      var: "pipeline",
      call: 'pipeline.run({"prompt_builder": {"question": text}})',
      output: 'out["llm"]["replies"][0]',
      source: "Haystack pipelines quickstart",
      check: true,
    },
  }),
  framework({
    id: "mastra",
    group: "ts_auto",
    name: "Mastra",
    badge: "auto",
    languages: ["typescript"],
    snippetKey: "typescript",
    logo: { mark: "mastra", monogram: "MA", color: null },
    instrument: {
      npm: "@traceai/mastra @mastra/observability @mastra/otel-exporter",
      import: 'import { createFIObservability } from "@traceai/mastra";',
      call: 'const observability = createFIObservability({ serviceName: "support-agent" });',
    },
    docs: `${DOCS}/mastra`,
    graph: autoGraph("@traceai/mastra"),
    agent: {
      spec: "src/agent.ts:agent",
      var: "agent",
      call: "await agent.generate(text)",
      output: "out.text",
      source: "docs.futureagi.com/docs/integrations/traceai/mastra",
    },
  }),
  framework({
    id: "vercel_ai",
    group: "ts_auto",
    name: "Vercel AI SDK",
    short: "Vercel AI",
    badge: "auto",
    languages: ["typescript"],
    snippetKey: "typescript",
    logo: { mark: "vercel_ai", monogram: "VA", color: null },
    instrument: {
      npm: "@traceai/vercel @vercel/otel",
      import: 'import { FISimpleSpanProcessor } from "@traceai/vercel";',
      call: 'registerOTel({ serviceName: "support-agent", spanProcessors: [new FISimpleSpanProcessor({ exporter })] });',
    },
    docs: `${DOCS}/vercel`,
    graph: autoGraph("@traceai/vercel"),
    agent: {
      spec: "src/agent.ts:run",
      var: "run",
      call: "await run(text)",
      output: "out.text",
      source: "AI SDK generateText with experimental_telemetry",
      check: true,
    },
    aliases: ["ai sdk", "generatetext"],
  }),
  // OTel: traced through OpenTelemetry, no traceAI package (V11).
  framework({
    id: "pydantic_ai",
    group: "own_tracing",
    name: "Pydantic AI",
    badge: "otel_preview",
    logo: { mark: "pydantic_ai", monogram: "PY", color: "#E620E9" },
    instrument: {
      import: "from pydantic_ai import Agent",
      call: "Agent.instrument_all()",
      check: true,
    },
    docs: null,
    graph: OTEL_GRAPH,
    agent: {
      spec: "agent.py:agent",
      var: "agent",
      call: "agent.run_sync(text)",
      output: "out.output",
      source: "Pydantic AI agents docs (run_sync)",
    },
  }),
  framework({
    id: "agno",
    group: "own_tracing",
    name: "Agno",
    badge: "otel_preview",
    logo: { mark: "agno", monogram: "AN", color: null },
    instrument: null,
    docs: null,
    graph: OTEL_GRAPH,
    agent: {
      spec: "agent.py:agent",
      var: "agent",
      call: "agent.run(text)",
      output: "out.content",
      source: "Agno agents quickstart",
      check: true,
    },
  }),
  framework({
    id: "strands",
    group: "own_tracing",
    name: "Strands Agents",
    short: "Strands",
    badge: "otel_preview",
    logo: { mark: "strands", monogram: "ST", color: null },
    instrument: {
      import: "from strands.telemetry import StrandsTelemetry",
      call: "StrandsTelemetry().setup_otlp_exporter()",
      check: true,
    },
    docs: null,
    graph: OTEL_GRAPH,
    agent: {
      spec: "agent.py:agent",
      var: "agent",
      call: "agent(text)",
      output: "str(out)",
      source: "Strands Agents quickstart",
      check: true,
    },
    aliases: ["aws strands"],
  }),
  framework({
    id: "claude_agent_sdk",
    group: "own_tracing",
    name: "Claude Agent SDK",
    badge: "otel_preview",
    logo: { mark: "claude_agent_sdk", monogram: "CA", color: "#D97757" },
    instrument: null,
    docs: null,
    graph: OTEL_GRAPH,
    agent: {
      spec: "agent.py:options",
      var: "options",
      call: "asyncio.run(collect(query(prompt=text, options=options)))",
      output: "out[-1].result",
      imports: ["import asyncio", "from claude_agent_sdk import query"],
      helper: [
        "async def collect(stream):",
        "    return [message async for message in stream]",
      ],
      source: "Claude Agent SDK quickstart (query)",
      check: true,
    },
  }),
  framework({
    id: "ag2",
    group: "own_tracing",
    name: "AG2",
    badge: "otel_preview",
    logo: { mark: "ag2", monogram: "A2", color: null },
    instrument: null,
    docs: null,
    graph: OTEL_GRAPH,
    agent: {
      spec: "agent.py:agent",
      var: "agent",
      call: "agent.run(message=text, max_turns=6).process()",
      output: "str(out)",
      source: "AG2 quickstart (ConversableAgent.run)",
      check: true,
    },
    aliases: ["autogen 0.2", "ag 2"],
  }),
  framework({
    id: "custom_python",
    group: "own_tracing",
    name: "Custom Python",
    badge: "manual",
    custom: true,
    logo: { mark: "custom_python", monogram: "PY", color: null },
    instrument: {
      pip: "fi-instrumentation-otel",
      import: "from opentelemetry import trace",
      call: 'tracer = trace.get_tracer("support-agent")',
    },
    docs: null,
    graph: { quality: "full", source: "you set graph.node.*" },
    agent: {
      spec: "agent.py:target",
      var: "target",
      call: "target(row)",
      output: "out",
      source: "your function: target(row) -> output",
    },
  }),
  framework({
    id: "custom_typescript",
    group: "own_tracing",
    name: "Custom TypeScript",
    badge: "manual",
    custom: true,
    languages: ["typescript"],
    snippetKey: "typescript",
    logo: { mark: "custom_typescript", monogram: "TS", color: null },
    instrument: {
      npm: "@traceai/fi-core",
      import: 'import { register, ProjectType } from "@traceai/fi-core";',
      call: 'const tracerProvider = register({ projectType: ProjectType.EXPERIMENT, projectName: "support-agent" });',
    },
    docs: null,
    graph: { quality: "full", source: "you set graph.node.*" },
    agent: {
      spec: "src/agent.ts:target",
      var: "target",
      call: "await target(row)",
      output: "out",
      source: "your function: target(row) => output",
    },
  }),
  framework({
    id: "otel_app",
    group: "own_tracing",
    name: "Any OpenTelemetry app",
    short: "OpenTelemetry",
    badge: "otel",
    custom: true,
    languages: ["python", "typescript"],
    logo: { mark: "otel_app", monogram: "OT", color: null },
    instrument: {
      pip: "fi-instrumentation-otel",
      import: "from fi_instrumentation import register",
      call: 'trace_provider = register(project_type=ProjectType.EXPERIMENT, project_name="support-agent")',
    },
    docs: null,
    graph: { quality: "unknown", source: "your spans" },
    agent: {
      spec: "app.py:target",
      var: "target",
      call: "target(row)",
      output: "out",
      source: "your function: target(row) -> output",
    },
    aliases: ["otel", "opentelemetry", "otlp"],
  }),
]);

const HARNESS_MODES = {
  modes: KIND_MODES.harness,
  defaultMode: "local",
  primaryModes: ["local"],
};
const harness = (e) => ({
  kind: "harness",
  group: "harness",
  custom: false,
  status: "today",
  badge: null,
  languages: [],
  snippetKey: "harness",
  docs: null,
  ...HARNESS_MODES,
  capture: { model_calls: "gateway", steps: null },
  capture_upload: "final_only",
  graph: { quality: "model_calls", source: "gateway · model calls only" },
  ...e,
});
const TRANSCRIPT = {
  capture: { model_calls: "gateway", steps: "transcript" },
  capture_upload: "transcript",
};

/** Harnesses (8): harness card. */
export const HARNESSES = Object.freeze([
  harness({
    id: "claude_code",
    name: "Claude Code",
    logo: { mark: "claude_code", monogram: "CC", color: "#D97757" },
    ...TRANSCRIPT,
    graph: {
      quality: "parent_child",
      source: "Claude Code stream-json transcript · no node ids",
    },
    cli: {
      command:
        "claude -p {task} --output-format stream-json --verbose --permission-mode acceptEdits --max-turns 40",
      approximate: false,
    },
  }),
  harness({
    id: "codex_cli",
    name: "Codex CLI",
    short: "Codex",
    logo: { mark: "codex_cli", monogram: "CX", color: null },
    ...TRANSCRIPT,
    graph: {
      quality: "parent_child",
      source: "Codex CLI --json events · no node ids",
    },
    cli: { command: "codex exec --json {task}", approximate: false },
  }),
  harness({
    id: "gemini_cli",
    name: "Gemini CLI",
    short: "Gemini",
    logo: { mark: "gemini_cli", monogram: "GE", color: null },
    ...TRANSCRIPT,
    graph: {
      quality: "parent_child",
      source: "Gemini CLI stream-json events · no node ids",
    },
    cli: {
      command: "gemini -p {task} --output-format stream-json --yolo",
      approximate: true,
    },
  }),
  harness({
    id: "openhands",
    name: "OpenHands",
    logo: { mark: "openhands", monogram: "OH", color: null },
    ...TRANSCRIPT,
    graph: {
      quality: "parent_child",
      source: "OpenHands trajectory · no node ids",
    },
    cli: {
      command:
        "python -m openhands.core.main --config-file .fi/openhands.toml -t {task}",
      approximate: true,
    },
  }),
  harness({
    id: "aider",
    name: "Aider",
    logo: { mark: "aider", monogram: "AI", color: null },
    // Aider logs each model call and the files it edits (no tool calls).
    capture: {
      model_calls: "gateway",
      steps: "transcript",
      record: "model_calls_edits",
    },
    capture_upload: "transcript",
    graph: {
      quality: "parent_child",
      source: "Aider LLM history + analytics log · model calls and edits",
    },
    cli: {
      command:
        "aider --message {task} --yes-always --no-auto-commits --llm-history-file .fi/aider.llm.history",
      approximate: true,
    },
  }),
  harness({
    id: "goose",
    name: "Goose",
    logo: { mark: "goose", monogram: "GO", color: null },
    ...TRANSCRIPT,
    graph: {
      quality: "parent_child",
      source: "Goose stream-json events · no node ids",
    },
    cli: {
      command: "goose run -t {task} --output-format stream-json",
      approximate: true,
    },
  }),
  harness({
    id: "mini_swe_agent",
    name: "mini-SWE-agent",
    logo: { mark: "mini_swe_agent", monogram: "SW", color: null },
    ...TRANSCRIPT,
    graph: {
      quality: "parent_child",
      source: "mini-SWE-agent trajectory · no node ids",
    },
    cli: {
      command: "mini -t {task} --yolo -o .fi/mini.traj.json --exit-immediately",
      approximate: true,
    },
    aliases: ["swe agent", "swe-agent"],
  }),
  harness({
    id: "custom_command",
    name: "Custom command",
    custom: true,
    logo: { mark: "custom_command", monogram: "CM", color: null },
    // Steps when the command writes $FI_TRACE_FILE; else the final output.
    capture: { model_calls: "gateway", steps: null, record: "trace_file" },
    graph: {
      quality: "model_calls",
      source: "your trace file (FI_TRACE_FILE) · else the final output",
    },
    cli: { command: "your-agent --task {task}", approximate: true },
  }),
]);

/** Endpoint protocols (3): Already running card. */
export const ENDPOINTS = Object.freeze([
  {
    id: "http",
    kind: "http",
    group: "endpoint",
    name: "HTTP endpoint",
    short: "HTTP",
    languages: [],
    badge: null,
    status: "today",
    custom: false,
    logo: { mark: "http", monogram: "HT", color: null },
    modes: KIND_MODES.http,
    defaultMode: "remote",
    primaryModes: ["remote"],
    snippetKey: "http",
    docs: null,
    graph: {
      quality: "unknown",
      source: "your endpoint's spans, if it sends any",
    },
    aliases: ["rest", "api", "url", "webhook"],
  },
  {
    id: "a2a",
    kind: "a2a",
    group: "endpoint",
    name: "A2A agent card",
    short: "A2A",
    languages: [],
    badge: null,
    status: "today",
    custom: false,
    logo: { mark: "a2a", monogram: "A2", color: null },
    modes: KIND_MODES.a2a,
    defaultMode: "remote",
    primaryModes: ["remote"],
    snippetKey: null,
    docs: null,
    graph: {
      quality: "parent_child",
      source: "A2A task events: message/send, task states, artifacts",
    },
    aliases: ["agent2agent", "agent card", "a2a protocol"],
  },
  {
    id: "mcp_tool",
    kind: "mcp_tool",
    group: "endpoint",
    name: "MCP server tool",
    short: "MCP tool",
    languages: [],
    badge: null,
    status: "today",
    custom: false,
    logo: { mark: "mcp_tool", monogram: "MC", color: null },
    modes: KIND_MODES.mcp_tool,
    defaultMode: "remote",
    primaryModes: ["remote"],
    snippetKey: null,
    docs: null,
    graph: {
      quality: "parent_child",
      source: "one MCP tools/call span per row: arguments and result",
    },
    aliases: ["model context protocol", "mcp", "tools/call"],
  },
]);

/** Already running (2): traces you already sent, and uploaded results. */
export const RUNNING = Object.freeze([
  {
    id: "traces",
    kind: "traces",
    group: "running",
    name: "Traces I already have",
    short: "Traces",
    languages: [],
    badge: null,
    status: "today",
    custom: false,
    logo: { mark: "traces", monogram: "TR", color: null },
    modes: [],
    defaultMode: null,
    primaryModes: [],
    snippetKey: "traces",
    docs: null,
    graph: { quality: "unknown", source: "the traces you already sent" },
  },
  {
    id: "upload",
    kind: "upload",
    group: "running",
    name: "Upload results",
    short: "Upload",
    languages: [],
    badge: null,
    status: "today",
    custom: false,
    logo: { mark: "upload", monogram: "UP", color: null },
    modes: [],
    defaultMode: null,
    primaryModes: [],
    snippetKey: "upload",
    docs: null,
    graph: { quality: "unknown", source: "no trace unless you link one" },
  },
]);
