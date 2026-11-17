import { describe, expect, it } from "vitest";
import {
  agentNameFor,
  cardOfKind,
  changedParams,
  clampParam,
  connectedNowOf,
  defaultFormOf,
  draftReadiness,
  limitsSummary,
  opensLevel3,
  pickCard,
  pickerBack,
  pickerFromParams,
  pickerToParams,
  reviewLine,
  runConfigFromDraft,
  runNameFor,
  sideEffectRule,
  usesOf,
  usesSummary,
  limitsWithSlot,
  boundExecutionOf,
  experimentYaml,
  yamlStartLines,
} from "../sourceModel";
import { catalogGroups } from "../CatalogGrid";

const SCHEMA = [
  { name: "model", type: "model", default: "gpt-4.1-mini", source: "code" },
  {
    name: "system_prompt",
    type: "prompt",
    default: "You are…",
    source: "code",
  },
  { name: "temperature", type: "float", default: 0.2, min: 0, max: 1 },
  { name: "retriever_k", type: "int", default: 5, min: 1, max: 20 },
];
const AGENT = {
  id: "ca_support_agent",
  name: "support_agent",
  kind: "code",
  entry_id: "langgraph",
  params_schema: SCHEMA,
  tools: [
    "lookup_order",
    "read_policy",
    "search_kb",
    "check_stock",
    "issue_refund",
    "offer_replacement",
    "escalate",
  ],
  side_effect_tools: ["issue_refund", "offer_replacement"],
  models: ["gpt-4.1-mini"],
  concurrency: 4,
  git: { commit: "9f2c1a7", dirty: true, files: ["agent.py", "tools.py"] },
};
const MACHINE = (o) => ({
  id: "cm_alice_mbp14",
  name: "alice@mbp-14",
  owner: { id: "u_alice", name: "Alice Park" },
  status: "online",
  may_use: true,
  agents: [AGENT],
  ...o,
});

describe("picker state and URL", () => {
  // dataset integration FE-0 (owner Q12): Your code, Agent harness and Voice agent are
  // absent, so their deep links and in-drawer keys land on step 1.
  it("a deep link to an absent source opens step 1", () => {
    const s = pickerFromParams(
      new URLSearchParams(
        "tab=experiments&new_experiment=1&source=code&entry=langgraph&agent=ca_support_agent&mode=local&form=connect",
      ),
    );
    expect(s).toMatchObject({ level: 1, card: null, entryId: null });
    expect(s.form).toBe("upload");
    expect(pickerFromParams({ entry: "vapi" }).level).toBe(1);
    expect(pickerFromParams({ entry: "claude_code" }).level).toBe(1);
  });

  it("src=running opens its catalog; absent cards and nothing open Level 1", () => {
    expect(pickerFromParams({ src: "running" })).toMatchObject({
      level: 2,
      card: "running",
    });
    ["code", "harness", "voice"].forEach((src) =>
      expect(pickerFromParams({ src }).level, src).toBe(1),
    );
    expect(pickerFromParams({}).level).toBe(1);
    expect(pickerFromParams({ src: "prompt" }).level).toBe(1);
  });

  it("no offered entry opens Level 3", () => {
    ["http", "a2a", "mcp_tool", "traces", "langgraph", "livekit"].forEach(
      (id) => expect(opensLevel3(id), id).toBe(false),
    );
  });

  it("back goes 2 → 1", () => {
    const s2 = pickCard({ level: 1 }, "running");
    expect(s2).toMatchObject({ level: 2, card: "running" });
    expect(pickerBack(s2)).toMatchObject({ level: 1, card: null });
  });

  it("cardOfKind maps the Already running kinds", () => {
    expect(cardOfKind("http")).toBe("running");
    expect(cardOfKind("upload")).toBe("running");
    expect(cardOfKind("voice")).toBe(null);
    expect(cardOfKind("code")).toBe(null);
    expect(cardOfKind("nope")).toBe(null);
  });
});

describe("defaults", () => {
  it("every code run opens on its script: there is no live connection", () => {
    expect(defaultFormOf({})).toBe("upload");
    expect(defaultFormOf({ arrivedForm: "connect" })).toBe("upload");
    expect(defaultFormOf({ sdk: { connect: "released" } })).toBe("upload");
  });
  it("names: agent + catalog name; experiment untouched", () => {
    expect(agentNameFor({ datasetName: "Support ops tasks · sandbox" })).toBe(
      "support_agent",
    );
    expect(agentNameFor({ knownAgent: { name: "billing_bot" } })).toBe(
      "billing_bot",
    );
    expect(
      runNameFor({ agentName: "support_agent", entryId: "langgraph" }),
    ).toBe("support_agent · LangGraph");
    expect(runNameFor({ agentName: "x", entryId: "claude_code" })).toBe(
      "Claude Code",
    );
  });
});

describe("connected now", () => {
  // dataset integration FE-0: there is no live connection, and Your code / Agent
  // harness are absent, so no connected agent is ever offered.
  it("offers no connected agent", () => {
    expect(connectedNowOf([MACHINE()], { datasetKind: "task_suite" })).toBe(
      null,
    );
    expect(connectedNowOf([MACHINE({ may_use: false })], {})).toBe(null);
    expect(connectedNowOf([MACHINE({ status: "stale" })], {})).toBe(null);
    expect(connectedNowOf([MACHINE()], { datasetKind: "scenarios" })).toBe(
      null,
    );
  });
});

describe("what it uses", () => {
  it("summarises the declared agent", () => {
    const u = usesOf({ agent: AGENT });
    expect(usesSummary(u)).toBe("Uses gpt-4.1-mini · 7 tools · 4 parameters");
    expect(u.tools.find((t) => t.name === "issue_refund").side_effect).toBe(
      true,
    );
    expect(u.tools.every((t) => t.declared && !t.seen)).toBe(true);
  });
  it("marks seen tools and the declared-but-unseen ones", () => {
    const u = usesOf({
      agent: AGENT,
      observed: { tools: [{ name: "lookup_order", called: true }], models: [] },
    });
    expect(u.tools.find((t) => t.name === "lookup_order")).toMatchObject({
      declared: true,
      seen: true,
    });
    expect(u.tools.find((t) => t.name === "search_kb").seen).toBe(false);
  });
  it("nothing known → no summary", () => {
    expect(usesSummary(usesOf({}))).toBe(null);
  });
  it("params: changed, clamped", () => {
    expect(changedParams(SCHEMA, { temperature: 0, retriever_k: 5 })).toEqual([
      "temperature",
    ]);
    expect(clampParam(SCHEMA[3], 99)).toBe(20);
    expect(clampParam(SCHEMA[3], 2.6)).toBe(3);
    expect(clampParam(SCHEMA[2], -1)).toBe(0);
  });
  it("limits summary", () => {
    expect(limitsSummary({ tries: 3, concurrency: 4 })).toBe(
      "3 tries per task · 4 at a time",
    );
    expect(limitsSummary({ tries: 1, concurrency: 1, rowNoun: "row" })).toBe(
      "1 try per row · 1 at a time",
    );
  });
});

describe("side effects (D21)", () => {
  it("warns and counts calls on a dataset without an Environment", () => {
    const r = sideEffectRule({
      kind: "code",
      agentName: "support_agent",
      sideEffectTools: ["issue_refund", "offer_replacement"],
      tries: 3,
      rows: 30,
    });
    expect(r.state).toBe("warn");
    expect(r.line).toBe(
      "support_agent can call issue_refund, offer_replacement.",
    );
    expect(r.reviewWarning).toBe(
      "issue_refund may be called up to 90 times (3 tries × 30 tasks).",
    );
  });
  it("numbers follow the inputs (no scripted digits)", () => {
    const r = sideEffectRule({
      kind: "code",
      agentName: "a",
      sideEffectTools: ["x"],
      tries: 2,
      rows: 7,
      rowNoun: "row",
    });
    expect(r.reviewWarning).toBe(
      "x may be called up to 14 times (2 tries × 7 rows).",
    );
  });
  it("a sandbox Environment wins", () => {
    const r = sideEffectRule({
      kind: "code",
      sideEffectTools: ["issue_refund"],
      environment: { mcp: { name: "acme-support-sandbox" } },
    });
    expect(r).toMatchObject({
      state: "sandbox",
      line: "Tools in this suite run against acme-support-sandbox; the script starts each task from its start_state and the end-state checks read the sandbox after your agent finishes.",
      reviewWarning: null,
    });
  });
  it("no warning with stubs, one try, or no side-effect tools; voice never", () => {
    const base = {
      kind: "code",
      agentName: "a",
      sideEffectTools: ["x"],
      rows: 5,
    };
    expect(sideEffectRule({ ...base, tries: 1 }).reviewWarning).toBe(null);
    expect(
      sideEffectRule({ ...base, tries: 3, safety: "stubbed" }).reviewWarning,
    ).toBe(null);
    expect(sideEffectRule({ ...base, sideEffectTools: [] }).state).toBe(null);
    expect(sideEffectRule({ ...base, kind: "voice" }).state).toBe(null);
  });
});

describe("review line", () => {
  it("formats through fmt", () => {
    expect(
      reviewLine({
        runs: 2,
        rows: 30,
        tries: 3,
        costUsd: 46,
        minutes: 12,
        localConcurrency: 4,
      }),
    ).toBe(
      "2 runs × 30 tasks × 3 tries = 180 tries · about $46.00 · about 12 min (your machine runs 4 at a time)",
    );
    expect(reviewLine({ runs: 1, rows: 6, tries: 1, rowNoun: "row" })).toBe(
      "1 run × 6 rows × 1 try = 6 tries",
    );
  });
});

describe("run config and readiness", () => {
  it("a Connect live draft records the machine, owner and mode", () => {
    const c = runConfigFromDraft({
      entryId: "langgraph",
      mode: "local",
      form: "connect",
      machine: MACHINE(),
      agent: AGENT,
      uses: usesOf({ agent: AGENT }),
      limits: { tries: 3, concurrency: 4, maxCostUsd: 5, timeoutS: 120 },
      safety: "real",
    });
    expect(c.kind).toBe("code");
    expect(c.framework).toMatchObject({
      id: "langgraph",
      graph_source: "connect_sdk",
    });
    expect(c.execution).toMatchObject({
      mode: "local",
      label: "alice@mbp-14",
      agent_id: "ca_support_agent",
      owner: { name: "Alice Park" },
    });
    expect(c.code.mode).toBe("connect_preview");
    expect(c.secrets.model_keys).toBe("machine");
    expect(c.params.values).toMatchObject({ temperature: 0.2 });
  });
  it("a Trace + upload draft has no machine", () => {
    const c = runConfigFromDraft({
      entryId: "crewai",
      mode: "local",
      form: "upload",
    });
    expect(c.code.mode).toBe("trace_upload");
    expect(c.execution.machine_id).toBe(null);
  });
  it("Connect live needs a connected agent; reasons are named", () => {
    expect(
      draftReadiness({
        entryId: "langgraph",
        runName: "x",
        mode: "local",
        form: "connect",
      }),
    ).toEqual({ ok: false, reason: "Connect your agent first · section 2" });
    expect(
      draftReadiness({ entryId: "langgraph", runName: "x", form: "upload" }).ok,
    ).toBe(true);
    expect(draftReadiness({ entryId: "langgraph", runName: " " }).reason).toBe(
      "Name this run",
    );
  });
});

describe("Rb slot reaches the run (R4 integrate)", () => {
  const limits = { tries: 3, concurrency: 4, concurrencyMax: 4 };
  it("no source owns tries any more (the voice config is gone)", () => {
    expect(
      limitsWithSlot(limits, {
        kind: "voice",
        slot: { voice: { tries: 1, concurrency: 4 } },
      }),
    ).toBe(limits);
  });
  it("leaves code and harness limits alone", () => {
    expect(limitsWithSlot(limits, { kind: "code", slot: {} })).toBe(limits);
    expect(
      limitsWithSlot(limits, {
        kind: "harness",
        slot: { voice: { tries: 9 } },
      }),
    ).toBe(limits);
  });
  it("runConfigFromDraft keeps the slot's recipe", () => {
    const harness = runConfigFromDraft({
      entryId: "claude_code",
      mode: "local",
      slot: { harness: { model: "claude-sonnet-4-5" } },
    });
    expect(harness.harness).toMatchObject({ model: "claude-sonnet-4-5" });
  });
});

describe("IT-R2 G1 P5 · Your code: All · Python · TypeScript", () => {
  it("the TypeScript chip leaves only TypeScript entries", () => {
    // dataset integration FE-0: Your code is absent, so its catalog has no groups.
    expect(catalogGroups("code", { lang: "typescript" })).toEqual([]);
  });
});

describe("options-truth L2 G5 (N3, N5, N6)", () => {
  const conn = {
    mode: "connect",
    machine_id: "mach_alice_mbp14",
    agent_id: "ca_claude_code",
    label: "alice@mbp-14",
  };
  it("a connected harness worker binds the run to its machine (N3)", () => {
    const c = runConfigFromDraft({
      entryId: "claude_code",
      mode: "local",
      slot: { execution: conn, harness: { model: "claude-sonnet-4-5" } },
    });
    expect(c.kind).toBe("harness");
    expect(c.execution).toMatchObject({
      mode: "local",
      machine_id: "mach_alice_mbp14",
      agent_id: "ca_claude_code",
      label: "alice@mbp-14",
    });
    // not connected: nothing bound, the run waits for the script
    const u = runConfigFromDraft({ entryId: "claude_code", mode: "local" });
    expect(u.execution.machine_id).toBeNull();
    // a connection only binds a local run
    expect(boundExecutionOf("harness", { mode: "ci" }, conn)).toBeNull();
    expect(boundExecutionOf("code", { mode: "local" }, conn)).toBeNull();
    expect(boundExecutionOf("voice", { mode: "local" }, conn)).toBeNull();
  });
  it("view=code is dropped below Level 3 (N5)", () => {
    const s = pickerFromParams(
      new URLSearchParams(
        "src=code&entry=langgraph&mode=local&form=connect&view=code",
      ),
    );
    expect(s.level).toBe(1);
    expect(pickerToParams(s).view).toBeNull();
  });
  it("experiment.yml starts per mode and omits undeclared lists (N6)", () => {
    const local = runConfigFromDraft({ entryId: "langgraph", mode: "local" });
    expect(local.models).toBeUndefined();
    expect(local.tools).toBeUndefined();
    const yl = experimentYaml(local, { runId: "r-1" });
    expect(yl).toContain(
      "#   python run_and_upload.py --config experiment.yml",
    );
    expect(yl).toContain('agent: "agent.py:graph"');
    expect(yl).not.toMatch(/models: \[\]|tools: \[\]/);
    const sb = runConfigFromDraft({ entryId: "langgraph", mode: "sandbox" });
    const ys = experimentYaml(sb, { runId: "r-1" });
    expect(yamlStartLines(sb)[0]).toBe(
      "Future AGI starts this in the sandbox; inside it runs",
    );
    expect(ys).not.toContain("agent:");
    expect(ys).toContain('mode: "sandbox"');
    const ci = runConfigFromDraft({ entryId: "langgraph", mode: "ci" });
    expect(yamlStartLines(ci)).toEqual([
      "Runs in .github/workflows/fi-experiment.yml",
    ]);
    const remote = runConfigFromDraft({
      entryId: "langgraph",
      mode: "remote",
      slot: {
        endpoint: {
          url: "https://agent.zephyr.test/reply",
          body_template: '{"message": "{{instruction}}"}',
        },
      },
    });
    const yr = experimentYaml(remote, { runId: "r-1" });
    expect(yr).toContain("#   Future AGI calls your endpoint for each row");
    expect(yr).not.toContain("run_and_upload");
    expect(yr).not.toContain("agent:");
    expect(yr).toContain('endpoint:\n  url: "https://agent.zephyr.test/reply"');
  });
});
