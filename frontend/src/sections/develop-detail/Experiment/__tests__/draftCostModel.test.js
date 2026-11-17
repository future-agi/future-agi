// IT-R5 G1 P4: one cost line in every source footer.
import { describe, expect, it } from "vitest";
import { draftCostLine } from "../draftCostModel";

describe("draftCostLine (IT-R5 G1 P4)", () => {
  it("a harness reads rows × tries · cost · time at concurrency", () => {
    const out = draftCostLine({
      draft: {
        kind: "harness",
        entryId: "claude_code",
        mode: "local",
        runName: "Claude Code",
        limits: { concurrency: 2 },
        slot: {
          harness: { model: "claude-sonnet-4-5", budget: { max_cost_usd: 1 } },
        },
      },
      rows: 30,
      tries: 1,
      rowNoun: "task",
    });
    expect(out.text).toMatch(
      /^30 tasks × 1 try · ~\$[\d.]+ · ~\d+(–\d+)? min at 2 at a time$/,
    );
    expect(out.needsModel).toBe(false);
    expect(out.assumption).toContain("claude-sonnet-4-5 at list price");
  });

  it("a code run with no model asks for it", () => {
    const out = draftCostLine({
      draft: {
        kind: "code",
        entryId: "langgraph",
        mode: "local",
        runName: "support_agent · LangGraph",
        limits: { concurrency: 4 },
        declared: null,
      },
      rows: 24,
      tries: 1,
      rowNoun: "task",
    });
    expect(out.text).toContain(
      "24 tasks × 1 try · ~$ — add your model to estimate",
    );
    expect(out.needsModel).toBe(true);
  });

  it("a declared model is priced", () => {
    const out = draftCostLine({
      draft: {
        kind: "code",
        entryId: "langgraph",
        mode: "local",
        limits: { concurrency: 4 },
        declared: { model: "gpt-4.1", tools: [], mcp: [] },
      },
      rows: 24,
      tries: 3,
      rowNoun: "task",
    });
    expect(out.text).toMatch(/^24 tasks × 3 tries · ~\$2\.1/);
  });

  it("a voice run reads calls × tries", () => {
    const out = draftCostLine({
      draft: {
        kind: "voice",
        entryId: "livekit",
        mode: "local",
        runName: "Acme voice v2 · LiveKit",
        slot: { voice: { depth: "full_voice", tries: 1, concurrency: 5 } },
      },
      rows: 24,
      tries: 1,
      rowNoun: "row",
    });
    expect(out.text).toMatch(
      /^24 calls × 1 try · ~\$[\d.]+ · ~\d+ min at 5 at a time$/,
    );
  });

  it("a voice run's footer time uses its Concurrency field, not a hidden limits default", () => {
    const out = draftCostLine({
      draft: {
        kind: "voice",
        entryId: "vapi",
        mode: "hosted",
        runName: "Acme Support v1 · Vapi",
        limits: { concurrency: 4 },
        slot: { voice: { depth: "full_voice", tries: 1, concurrency: 5 } },
      },
      rows: 24,
      tries: 1,
      rowNoun: "row",
    });
    expect(out.text).toMatch(/ min at 5 at a time$/);
  });

  it("nothing without rows", () => {
    expect(draftCostLine({ draft: { kind: "code" }, rows: 0 })).toBeNull();
  });
});

describe("options-truth L2 R3 · one estimator", () => {
  const code = (extra = {}) => ({
    kind: "code",
    entryId: "crewai",
    mode: "local",
    runName: "crew · CrewAI",
    limits: { concurrency: 4 },
    ...extra,
  });
  const judges = [{ type: "llm" }, { type: "llm" }];

  it("(b) a typed model with no price: ~$— and the judge calls, no history claim", () => {
    const out = draftCostLine({
      draft: code({ declared: { model: "my-custom-llm", tools: [], mcp: [] } }),
      rows: 30,
      tries: 1,
      rowNoun: "task",
      evals: judges,
    });
    expect(out.text).toMatch(
      /^30 tasks × 1 try · ~\$— · no price for my-custom-llm · judge calls ~\$0\.036 · ~\d/,
    );
    expect(out.needsModel).toBe(false);
    expect(out.assumption).not.toMatch(/last finished run/);
    expect(out.assumption).toContain("my-custom-llm has no list price");
  });

  it("(c) the cost cap clamps expected and says where it stops", () => {
    const at = (cap) =>
      draftCostLine({
        draft: code({
          declared: { model: "gpt-4.1", tools: [], mcp: [] },
          limits: { concurrency: 4, maxCostUsd: cap },
        }),
        rows: 30,
        tries: 2,
        rowNoun: "task",
      }).text;
    const low = at(0.5);
    const high = at(20);
    expect(low).toContain("~$0.50 · stops at $0.50");
    expect(high).not.toContain("stops at");
    expect(low).not.toBe(high);
  });

  it("(a) a changed model param beats the declared model", () => {
    const out = draftCostLine({
      draft: code({
        declared: { model: "gpt-4.1", tools: [], mcp: [] },
        uses: { params: [{ name: "model", default: "gpt-4.1" }] },
        params: { model: "gpt-4.1-mini" },
      }),
      rows: 10,
      tries: 1,
      rowNoun: "task",
    });
    expect(out.assumption).toContain("gpt-4.1-mini at list price");
  });

  it("(f) sandbox vCPU changes the duration; (e) rows is the caller's scope", () => {
    const sandbox = (cpu) =>
      draftCostLine({
        draft: code({
          mode: "sandbox",
          declared: { model: "gpt-4.1", tools: [], mcp: [] },
          limits: { concurrency: 1 },
          slot: { sandbox: { resources: { cpu } } },
        }),
        rows: 30,
        tries: 3,
        rowNoun: "task",
      }).text;
    const slow = sandbox(1).match(/~(\d+)(?:–(\d+))? min/);
    const fast = sandbox(8).match(/~(\d+)(?:–(\d+))? min/);
    expect(Number(slow[2] ?? slow[1])).toBeGreaterThan(
      Number(fast[2] ?? fast[1]),
    );
    const one = draftCostLine({
      draft: code({ declared: { model: "gpt-4.1", tools: [], mcp: [] } }),
      rows: 1,
      tries: 1,
      rowNoun: "task",
    });
    // gpt-4.1: 7000 × $2 + 1900 × $8 per 1M = $0.0292 for the one try.
    // MONEY-FMT e9f0a2d0dd rounds the display, not the estimate.
    expect(one.text).toMatch(/^1 task × 1 try · ~\$0\.0292 /);
  });

  it("(d) a harness try is priced at its chosen model and capped per try", () => {
    const h = (model, cap) =>
      draftCostLine({
        draft: {
          kind: "harness",
          entryId: "codex_cli",
          mode: "local",
          limits: { concurrency: 2 },
          slot: { harness: { model, max_cost_usd: cap } },
        },
        rows: 30,
        tries: 3,
        rowNoun: "task",
      });
    const mini = h("gpt-5-mini", 1);
    const big = h("gpt-5-codex", 1);
    expect(mini.text).not.toBe(big.text);
    // gpt-5-codex at 7000 in + 1900 out = $0.02775 a try > a $0.02 cap.
    const capped = h("gpt-5-codex", 0.02);
    expect(capped.text).toContain("~$1.80 · stops at $0.02 per try");
  });

  it("(e) a harness run never passes its own run cap (Stop if cost goes over)", () => {
    const h = (maxCostUsd) =>
      draftCostLine({
        draft: {
          kind: "harness",
          entryId: "codex_cli",
          mode: "local",
          limits: { concurrency: 2, ...(maxCostUsd ? { maxCostUsd } : {}) },
          slot: { harness: { model: "gpt-5-codex", max_cost_usd: 1 } },
        },
        rows: 30,
        tries: 3,
        rowNoun: "task",
      });
    // 90 tries × $0.02775 = $2.50 expected; a $2 run cap stops it there.
    expect(h(null).text).toMatch(/^30 tasks × 3 tries · ~\$2\.4975 · ~/);
    expect(h(2).text).toMatch(
      /^30 tasks × 3 tries · ~\$2 · stops at \$2 per run · ~/,
    );
    // A run cap above the expected cost changes nothing in the footer.
    expect(h(5).text).toBe(h(null).text);
  });
});
