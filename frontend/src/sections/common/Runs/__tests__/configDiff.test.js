import { describe, expect, it } from "vitest";
import { configDiff, configDiffSentence } from "../configDiff";

const base = {
  kind: "prompt",
  prompt: {
    id: "P1",
    name: "Support Reply",
    version: "V5",
    messages: [{ role: "system", content: "Be brief and kind." }],
    text_hash: "9c1e",
  },
  model: { id: "gpt-4o-mini", params: { temperature: 0.2, max_tokens: 600 } },
  tools: [
    { name: "lookup_order", schema_hash: "a41f", mode: "mock" },
    { name: "apply_credit", schema_hash: "b2", mode: "mock" },
  ],
  max_tool_turns: 8,
  dataset: { version: "v5", split: null, rows: 238 },
  evals: [{ id: "task_success", version: "v1" }],
  trials: 3,
};
const labels = (chips) => chips.map((c) => c.label);
const run = (config, label = "#2B") => ({ run_id: label, label, config });

describe("configDiff labels (R2-SPEC §1.1)", () => {
  it("prompt version with word counts", () => {
    const words = (n) => Array.from({ length: n }, (_, i) => `w${i}`).join(" ");
    const a = {
      ...base,
      prompt: {
        ...base.prompt,
        messages: [{ role: "system", content: words(20) }],
      },
    };
    const b = {
      ...base,
      prompt: {
        ...base.prompt,
        version: "V6",
        text_hash: "x",
        messages: [
          {
            role: "system",
            content: `${words(8)} ${Array.from({ length: 38 }, (_, i) => `n${i}`).join(" ")}`,
          },
        ],
      },
    };
    expect(labels(configDiff(a, b))).toEqual([
      "prompt V5 → V6 (+38 −12 words)",
    ]);
  });

  it("model and params", () => {
    const b = {
      ...base,
      model: {
        id: "claude-sonnet-4-5",
        params: { temperature: 0.9, max_tokens: 600 },
      },
    };
    expect(labels(configDiff(base, b))).toEqual([
      "model gpt-4o-mini → claude-sonnet-4-5",
      "temperature 0.2 → 0.9",
    ]);
  });

  it("tools added / removed and mode changes", () => {
    const b = {
      ...base,
      tools: [
        { name: "lookup_order", schema_hash: "a41f", mode: "recorded" },
        { name: "schedule_callback", schema_hash: "c3", mode: "mock" },
      ],
    };
    expect(labels(configDiff(base, b))).toEqual([
      "tools +schedule_callback",
      "tools −apply_credit",
      "lookup_order mock → recorded",
    ]);
  });

  it("agent version with the one changed node", () => {
    const agent = {
      graph_id: "G",
      name: "Support Agent",
      version: "v3",
      status: "active",
      nodes: [
        {
          id: "classify_intent",
          model: "gpt-4o-mini",
          prompt: "Extract the order number.",
        },
        { id: "reply", model: "gpt-4o-mini", prompt: "Reply." },
      ],
      limits: { max_iterations: 12 },
    };
    const a = {
      kind: "agent",
      agent,
      dataset: base.dataset,
      evals: base.evals,
      trials: 3,
    };
    const b = {
      ...a,
      agent: {
        ...agent,
        version: "v3.1",
        status: "draft",
        nodes: [
          {
            id: "classify_intent",
            model: "gpt-4o-mini",
            prompt: "Extract the order number as digits only.",
          },
          agent.nodes[1],
        ],
      },
    };
    const chips = configDiff(a, b);
    expect(labels(chips)).toEqual([
      "agent Support Agent v3 → v3.1 (draft): classify_intent prompt changed",
    ]);
    expect(chips[0].detail.nodes[0].id).toBe("classify_intent");
  });

  it("code commit with file stats", () => {
    const a = {
      kind: "code",
      code: { mode: "sdk_preview", commit: "3fa91c2" },
      trials: 3,
    };
    const b = {
      kind: "code",
      code: {
        mode: "sdk_preview",
        commit: "91bc0e4",
        dirty: { files: [{ path: "agent.py", plus: 9, minus: 2 }] },
      },
      trials: 3,
    };
    expect(labels(configDiff(a, b))).toEqual([
      "code 3fa91c2 → 91bc0e4 (agent.py +9 −2)",
    ]);
  });

  it("harness name, model and max turns", () => {
    const h = {
      name: "Claude Code",
      model: "claude-sonnet-4-5",
      sandbox: "Docker · acme-support-sandbox:1.4",
      max_turns: 30,
      timeout_s: 600,
      max_cost_usd: 0.5,
    };
    const a = { kind: "harness", harness: h, trials: 3 };
    const b = {
      kind: "harness",
      harness: { ...h, name: "Codex CLI", model: "gpt-5-codex", max_turns: 40 },
      trials: 3,
    };
    expect(labels(configDiff(a, b))).toEqual([
      "model claude-sonnet-4-5 → gpt-5-codex",
      "harness Claude Code → Codex CLI",
      "max turns 30 → 40",
    ]);
  });

  it("dataset version with rows only in one version", () => {
    const b = { ...base, dataset: { version: "v6", split: null, rows: 248 } };
    expect(labels(configDiff(base, b))).toEqual([
      "dataset v5 → v6 (+10 rows only in v6, excluded)",
    ]);
  });

  it("different kinds give the kind chip first, then the shared axes", () => {
    const b = {
      kind: "agent",
      agent: { name: "Support Agent", version: "v3" },
      dataset: base.dataset,
      evals: base.evals,
      trials: 3,
    };
    const chips = configDiff(run(base, "#2A"), run(b, "#2B"));
    expect(labels(chips)).toEqual([
      "kind Prompt + tools → Agent graph",
      "tools −lookup_order",
      "tools −apply_credit",
    ]);
    expect(chips[0].detail.differ).toBeGreaterThan(1);
    expect(configDiffSentence(chips)).toBe(
      "Changed: kind (Prompt → Agent graph), tools (−lookup_order, −apply_credit).",
    );
  });

  it("sentences: several changes vs one", () => {
    const three = {
      ...base,
      model: { id: "x", params: { temperature: 0.9, max_tokens: 600 } },
      trials: 1,
    };
    const chips = configDiff(base, three);
    expect(chips).toHaveLength(3);
    expect(configDiffSentence(chips)).toBe(
      "Changed: model (gpt-4o-mini → x), parameters (temperature 0.2 → 0.9), tries (3 → 1).",
    );
    expect(configDiffSentence(configDiff(base, { ...base, trials: 1 }))).toBe(
      "Changed: tries (3 → 1).",
    );
    expect(configDiffSentence([])).toBeNull();
  });

  it("hides unchanged axes and returns [] without configs", () => {
    expect(configDiff(base, { ...base })).toEqual([]);
    expect(configDiff({ run_id: "a" }, run(base))).toEqual([]);
    expect(configDiff(null, base)).toEqual([]);
    expect(configDiff(base, { ...base }, { all: true })).toHaveLength(21);
  });
});
