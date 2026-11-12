import { describe, expect, it } from "vitest";
import {
  BADGES,
  CATALOG,
  OTEL_PREVIEW_REASON,
  RUN_SOURCE_CARDS,
  STEP1_COPY,
  allEntries,
  cardLogos,
  catalogCount,
  catalogEntry,
  entryOfConfig,
  graphQualityOf,
  modesFor,
  kindRecommendation,
  moreCount,
  isOffered,
  previewReason,
  searchCatalog,
} from "../catalog";
import { FRAMEWORKS, HARNESSES } from "../catalogEntries";

describe("catalog counts (dsx-sdk FE-0: only sources with a real path)", () => {
  // Owner Q12: an option is absent until its real row is green. Your code,
  // Agent harness, Voice agent and Upload results printed generated runners
  // for routes that do not exist, and Traces I already have waits for
  // SRV-13 (DESIGN rev2 §5.1), so the picker offers none of them.
  it("offers the 3 endpoint entries", () => {
    expect(allEntries().map((e) => e.id)).toEqual(["http", "a2a", "mcp_tool"]);
    expect(catalogCount()).toBe(3);
    expect(Object.keys(CATALOG)).toEqual(["endpoints", "running"]);
    expect(CATALOG.endpoints).toHaveLength(3);
    expect(CATALOG.running).toEqual([]);
    expect(isOffered("traces")).toBe(false);
    expect(isOffered("upload")).toBe(false);
  });

  it("keeps framework and harness entries as lookups only", () => {
    expect(catalogEntry("langgraph").name).toBe("LangGraph");
    expect(catalogEntry("claude_code").name).toBe("Claude Code");
    expect(isOffered("langgraph")).toBe(false);
    expect(isOffered("claude_code")).toBe(false);
    expect(isOffered("http")).toBe(true);
    ["vapi", "retell", "bland", "elevenlabs", "phone_sip", "livekit"].forEach(
      (id) => expect(catalogEntry(id), id).toBeNull(),
    );
  });

  it("names no logos on the cards and counts what is behind them", () => {
    expect(cardLogos("running")).toEqual({ logos: [], more: 0, total: 3 });
    const card = (id) => RUN_SOURCE_CARDS.find((c) => c.id === id);
    expect(moreCount(card("running"), 0)).toBe(3);
  });

  it("marks no offered entry custom", () => {
    expect(allEntries().filter((e) => e.custom)).toEqual([]);
  });
});

describe("step 1 copy (D2 verbatim)", () => {
  it("has the three cards with their lines", () => {
    expect(STEP1_COPY.title).toBe("What are you testing?");
    expect(STEP1_COPY.subtitle).toBe(
      "Add one or more ways to run. Add a second to compare them.",
    );
    expect(STEP1_COPY.disambiguation).toBe(
      "Prompt and Agent graph run in Future AGI. Already running = an endpoint, an A2A agent or an MCP tool you host.",
    );
    // L7c g2 (picker:search): Prompt, Agent graph and the 3 running entries.
    expect(STEP1_COPY.searchPlaceholder(catalogCount() + 2)).toBe(
      "Search 5 ways to run, prompts, graphs, models",
    );
    expect(STEP1_COPY.truncated(12, 20)).toBe("12 of 20 · keep typing");
    expect(RUN_SOURCE_CARDS.map((c) => [c.id, c.title, c.line])).toEqual([
      ["prompt", "Prompt", "A prompt version and a model, with tools."],
      ["agent", "Agent graph", "A graph you built in Agent Playground."],
      [
        "running",
        "Already running",
        "An endpoint, an A2A agent, or an MCP tool.",
      ],
    ]);
    expect(RUN_SOURCE_CARDS.find((c) => c.id === "running").subTiles).toEqual([
      "http",
      "a2a",
      "mcp_tool",
    ]);
  });

  // options-truth L1 B: the first card of the kind's order is the
  // recommendation.
  it("recommends at most one card by dataset kind", () => {
    expect(kindRecommendation("task_suite").card).toBe("agent");
    expect(kindRecommendation("scenarios").card).toBe("prompt");
    expect(kindRecommendation("rows").card).toBe("prompt");
  });
});

describe("entries", () => {
  it("every entry has the frozen fields", () => {
    allEntries().forEach((e) => {
      expect(e.id, e.name).toMatch(/^[a-z0-9_]+$/);
      // options-truth L1 C11: no Preview or Coming soon entries.
      expect(e.status).toBe("today");
      expect(e.previewReason).toBeUndefined();
      expect(e.todayInstead).toBeUndefined();
      expect(typeof e.custom).toBe("boolean");
      expect(e.logo.monogram, e.name).toMatch(/^[A-Z0-9]{2}$/);
      expect(Array.isArray(e.modes)).toBe(true);
      expect(Array.isArray(e.primaryModes)).toBe(true);
      expect(["full", "parent_child", "model_calls", "unknown"]).toContain(
        e.graph.quality,
      );
      expect(e.graph.source).toBeTruthy();
    });
  });

  it("states graph quality only from verified facts (D12, V18)", () => {
    const q = (id) => catalogEntry(id).graph;
    expect(q("custom_python")).toEqual({
      quality: "full",
      source: "you set graph.node.*",
    });
    expect(q("custom_typescript").quality).toBe("full");
    expect(q("langgraph")).toEqual({
      quality: "parent_child",
      source: "traceAI-langchain · parent → child spans",
    });
    FRAMEWORKS.filter((e) => e.badge === "auto").forEach((e) =>
      expect(e.graph.quality, e.name).toBe("parent_child"),
    );
    FRAMEWORKS.filter((e) => e.badge === "otel_preview").forEach((e) =>
      expect(e.graph.quality, e.name).toBe("unknown"),
    );
  });

  it("splits AutoGen (auto-traced) from AG2 (OTel)", () => {
    expect(catalogEntry("autogen")).toMatchObject({
      badge: "auto",
      status: "today",
      instrument: { pip: "traceAI-autogen" },
    });
    expect(catalogEntry("ag2")).toMatchObject({
      badge: "otel_preview",
      status: "today",
    });
    // The badge keeps the fact in neutral words; there is no status reason.
    expect(BADGES.otel_preview).toBe("OTel");
    expect(OTEL_PREVIEW_REASON).toBe(
      "Traced through OpenTelemetry (no traceAI package)",
    );
    expect(previewReason(catalogEntry("ag2"))).toBeNull();
  });

  it("uses the traceAI names from the docs", () => {
    const imp = (id) => catalogEntry(id).instrument.import;
    expect(imp("langgraph")).toBe(
      "from traceai_langchain import LangChainInstrumentor",
    );
    expect(imp("openai_agents")).toBe(
      "from traceai_openai_agents import OpenAIAgentsInstrumentor",
    );
    expect(imp("crewai")).toBe("from traceai_crewai import CrewAIInstrumentor");
    expect(imp("dspy")).toBe("from traceai_dspy import DSPyInstrumentor");
    expect(imp("google_adk")).toBe(
      "from traceai_google_adk import GoogleADKInstrumentor",
    );
    expect(imp("smolagents")).toBe(
      "from traceai_smolagents import SmolagentsInstrumentor",
    );
    expect(imp("mastra")).toContain("createFIObservability");
    expect(imp("vercel_ai")).toContain("FISimpleSpanProcessor");
    expect(catalogEntry("llamaindex").instrument.pip).toBe(
      "traceAI-llamaindex",
    );
    expect(catalogEntry("haystack").instrument.pip).toBe("traceAI-haystack");
  });

  it("gives every framework complete agent code (D23)", () => {
    FRAMEWORKS.forEach((e) => {
      expect(e.agent, e.name).toBeTruthy();
      ["spec", "var", "call", "output", "source"].forEach((k) =>
        expect(e.agent[k], `${e.name}.${k}`).toBeTruthy(),
      );
      expect(e.agent.call, e.name).toContain(
        e.agent.var === "target" ? "target(row)" : e.agent.var,
      );
    });
    expect(catalogEntry("langgraph").agent).toMatchObject({
      spec: "agent.py:graph",
      call: 'app.invoke({"messages": [("user", text)]})',
      output: 'out["messages"][-1].content',
    });
    expect(catalogEntry("openai_agents").agent.call).toBe(
      "Runner.run_sync(agent, text)",
    );
    expect(catalogEntry("crewai").agent.call).toBe(
      'crew.kickoff(inputs={"input": text})',
    );
    expect(catalogEntry("pydantic_ai").agent.call).toBe("agent.run_sync(text)");
    expect(catalogEntry("mastra").agent.call).toBe(
      "await agent.generate(text)",
    );
  });

  it("gives every harness a CLI line and a capture mode", () => {
    HARNESSES.forEach((h) => {
      expect(h.status).toBe("today");
      expect(h.cli.command).toContain("{task}");
      expect(["transcript", "final_only"]).toContain(h.capture_upload);
    });
    expect(catalogEntry("claude_code")).toMatchObject({
      capture: { model_calls: "gateway", steps: "transcript" },
      capture_upload: "transcript",
      cli: {
        command:
          "claude -p {task} --output-format stream-json --verbose --permission-mode acceptEdits --max-turns 40",
      },
    });
    expect(catalogEntry("codex_cli").cli.command).toBe(
      "codex exec --json {task}",
    );
    // options-truth L2 G6: OpenHands' trajectory is its step record; a
    // custom command records steps only through its trace file.
    expect(catalogEntry("openhands").capture).toEqual({
      model_calls: "gateway",
      steps: "transcript",
    });
    expect(catalogEntry("openhands").graph.quality).toBe("parent_child");
    expect(catalogEntry("custom_command").capture).toEqual({
      model_calls: "gateway",
      steps: null,
      record: "trace_file",
    });
    expect(catalogEntry("custom_command").graph.quality).toBe("model_calls");
  });

  // options-truth L1 C: A2A and MCP tool are real run kinds.
  it("makes A2A and MCP tool their own run kinds", () => {
    expect(catalogEntry("a2a")).toMatchObject({ kind: "a2a", status: "today" });
    expect(catalogEntry("mcp_tool")).toMatchObject({
      kind: "mcp_tool",
      status: "today",
    });
    ["a2a", "mcp_tool"].forEach((id) =>
      expect(catalogEntry(id).graph.source).not.toMatch(/not built/),
    );
    expect(previewReason(catalogEntry("upload"))).toBeNull();
    expect(entryOfConfig({ kind: "a2a" }).id).toBe("a2a");
    expect(entryOfConfig({ kind: "mcp_tool" }).id).toBe("mcp_tool");
  });
});

describe("modesFor (D5)", () => {
  it("lists primary modes first, the rest under More places to run", () => {
    expect(modesFor("code", "langgraph").map((m) => [m.id, m.primary])).toEqual(
      [
        ["local", true],
        ["ci", true],
        ["sandbox", false],
        ["remote", false],
      ],
    );
    expect(modesFor("code", "langgraph")[2]).toMatchObject({
      label: "Future AGI sandbox",
      status: "today",
    });
    expect(
      modesFor("harness", "claude_code").map((m) => [m.id, m.primary]),
    ).toEqual([
      ["local", true],
      ["ci", false],
      ["sandbox", false],
    ]);
  });

  it("has no voice kind and no Preview mode", () => {
    expect(modesFor("voice", "vapi")).toEqual([]);
    // options-truth L1 C11: no mode is ever Preview.
    ["code", "harness"].forEach((k) =>
      (k === "code" ? FRAMEWORKS : HARNESSES).forEach((e) =>
        modesFor(k, e.id).forEach((m) => expect(m.status).not.toBe("preview")),
      ),
    );
  });
});

describe("graphQualityOf / entryOfConfig", () => {
  it("is full for agent graphs, connect_sdk and manual graphs", () => {
    expect(graphQualityOf({ kind: "agent" })).toBe("full");
    expect(
      graphQualityOf({
        kind: "code",
        framework: { id: "langgraph", graph_source: "connect_sdk" },
      }),
    ).toBe("full");
    expect(graphQualityOf({ kind: "code", framework: { id: "crewai" } })).toBe(
      "parent_child",
    );
    expect(
      graphQualityOf({ kind: "code", framework: { id: "pydantic_ai" } }),
    ).toBe("unknown");
    expect(
      graphQualityOf({ kind: "harness", harness: { id: "custom_command" } }),
    ).toBe("model_calls");
    expect(
      graphQualityOf({ kind: "harness", harness: { id: "openhands" } }),
    ).toBe("parent_child");
    expect(
      graphQualityOf({ kind: "harness", harness: { name: "Codex CLI" } }),
    ).toBe("parent_child");
  });

  it("maps R2 configs to entries", () => {
    expect(
      entryOfConfig({ kind: "code", code: { framework: "LangGraph 0.6" } }).id,
    ).toBe("langgraph");
    expect(
      entryOfConfig({ kind: "voice", voice: { provider: "vapi" } }),
    ).toBeNull();
    expect(entryOfConfig({ kind: "prompt" })).toBeNull();
  });
});

describe("searchCatalog", () => {
  it("finds by name, alias, language and package", () => {
    expect(searchCatalog("http")[0].id).toBe("http");
    expect(searchCatalog("mcp").map((e) => e.id)).toContain("mcp_tool");
    expect(searchCatalog("a2a")[0].id).toBe("a2a");
    // FE-0: absent sources are not found (no tile to open).
    ["langgraph", "codex", "livekit", "vapi", "upload", "traces"].forEach((q) =>
      expect(searchCatalog(q), q).toEqual([]),
    );
    expect(searchCatalog("")).toHaveLength(3);
  });
});

describe("IT-R2 G1 P5 · Recommended from context only", () => {
  it("the baseline run's kind, the re-tested run first, none otherwise", async () => {
    const { recommendedFromRuns } = await import("../catalog");
    const runs = {
      baseline: { run_id: "b", label: "#2B" },
      results: [
        { run_id: "a", label: "#2A", kind: "prompt" },
        { run_id: "b", label: "#2B", kind: "agent" },
      ],
    };
    expect(recommendedFromRuns(runs)).toEqual({
      card: "agent",
      reason: "Your baseline #2B is an agent graph",
    });
    // FE-0: a run of an absent kind recommends nothing.
    expect(recommendedFromRuns(runs, { label: "#1A", kind: "voice" })).toBe(
      null,
    );
    expect(recommendedFromRuns({ results: runs.results })).toBeNull();
    expect(recommendedFromRuns(null)).toBeNull();
  });
});
