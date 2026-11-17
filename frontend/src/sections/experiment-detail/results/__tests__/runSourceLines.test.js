import { describe, expect, it } from "vitest";
import { harnessLiveLine, sourceLine, tracesLine } from "../runSourceLines";

const base = { trials: 3, progress: { done: 30, total: 30 } };
const run = (config, extra = {}) => ({ ...base, config, ...extra });

describe("sourceLine (§5.3.7)", () => {
  it("prompt + tools", () => {
    const r = run({
      kind: "prompt",
      prompt: { name: "Support Ops Reply", version: "V1" },
      model: { id: "gpt-4o-mini" },
      tools: Array.from({ length: 6 }, (_, i) => ({
        name: `t${i}`,
        mode: "mock",
      })),
      max_tool_turns: 8,
    });
    expect(sourceLine(r)).toBe(
      "Prompt + tools · Support Ops Reply V1 × gpt-4o-mini · 6 tools (mock) · max 8 tool turns · Runs in Future AGI",
    );
  });
  it("agent", () => {
    const r = run({
      kind: "agent",
      agent: {
        name: "Support Agent",
        version: "v3",
        status: "active",
        nodes: [1, 2, 3, 4, 5, 6],
      },
      tools: [{ name: "lookup_order", mode: "live" }],
    });
    expect(sourceLine(r, { taskSuite: true })).toBe(
      "Agent · Support Agent v3 (active) · 6 nodes · tools live in sandbox · Runs in Future AGI",
    );
  });
  it("code: SDK · Preview and works today", () => {
    const sdk = run(
      {
        kind: "code",
        code: {
          mode: "sdk_preview",
          host: "alice@mbp-14",
          commit: "3fa91c2",
          dirty: { files: [{ path: "agent.py" }] },
          command: "an old recorded command",
          sdk: "an old recorded SDK version",
        },
      },
      { label: "#2C" },
    );
    // dataset integration FE-0 (review must-fix 3): no command and no SDK version.
    expect(sourceLine(sdk)).toBe(
      "Your code · alice@mbp-14 · git 3fa91c2 (dirty)",
    );
    const today = run({ kind: "code", code: { mode: "upload" } });
    expect(sourceLine(today, { lastUploadText: "2 min ago" })).toBe(
      "Your code · uploaded 90 results · traceAI · last upload 2 min ago",
    );
  });
  it("code: an SDK run prints no made-up command (dsx-sdk FE-0)", () => {
    const id = "a6000000-0000-4000-8000-00000000003c";
    const withEntry = run(
      { kind: "code", code: { mode: "sdk_preview", entry: "agent.py:graph" } },
      { run_id: id },
    );
    expect(sourceLine(withEntry)).toBe("Your code");
    // No importable entry (`agent.py` names no attribute): no command.
    const bare = run(
      { kind: "code", code: { mode: "sdk_preview", entry: "agent.py" } },
      { run_id: id },
    );
    expect(sourceLine(bare)).toBe("Your code");
  });
  it("traces", () => {
    const r = run({
      kind: "traces",
      traces: {
        project: "acme-support-agent · local",
        version: "nightly-2026-10-14",
        match: { by: "metadata", key: "row_id" },
      },
    });
    expect(sourceLine(r, { matchedRows: 28, totalRows: 30 })).toBe(
      "Traces I already have · acme-support-agent · local › nightly-2026-10-14 · matched by row_id · 28 of 30 rows",
    );
    expect(tracesLine(r, { matchedRows: 28 })).toBe(
      'Traces: 28 · in "acme-support-agent · local" · each cell\'s trace chip opens one',
    );
  });
  it("harness", () => {
    const r = run({
      kind: "harness",
      harness: {
        name: "Claude Code",
        model: "claude-sonnet-4-5",
        sandbox: "Docker · acme-support-sandbox:1.4",
        max_turns: 30,
        max_cost_usd: 0.5,
        instructions_file: "CLAUDE.md (412 words)",
      },
    });
    expect(sourceLine(r)).toBe(
      "Claude Code × claude-sonnet-4-5 · Docker acme-support-sandbox:1.4 · 30 turns · $0.50 per try · CLAUDE.md 412 words",
    );
  });
  it("http and upload", () => {
    expect(
      sourceLine({
        target_type: "http",
        target: { method: "POST", url: "https://api.acme.ai/v1/support/reply" },
      }),
    ).toBe("Endpoint · POST https://api.acme.ai/v1/support/reply");
    expect(
      sourceLine({
        target_type: "external",
        target: { branch: "main", commit: "a1f3c9" },
      }),
    ).toBe("Upload · main@a1f3c9 · results uploaded by your code");
  });
});

describe("options-truth L2 R1/R2: what ran", () => {
  const code = (framework, extra = {}) =>
    run({ kind: "code", framework, code: { mode: "trace_upload" }, ...extra });
  it("the tracing word follows the framework (tile badge)", () => {
    const one = { trials: 2, progress: { done: 30, total: 30 } };
    expect(
      sourceLine({ ...code({ id: "langgraph", tracing: "auto" }), ...one }),
    ).toBe("Your code · uploaded 60 results · traceAI");
    [
      "pydantic_ai",
      "agno",
      "strands",
      "claude_agent_sdk",
      "ag2",
      "otel_app",
    ].forEach((id) => {
      // No tracing field: the catalog badge decides.
      expect(sourceLine({ ...code({ id }), ...one }), id).toBe(
        "Your code · uploaded 60 results · OpenTelemetry",
      );
    });
    expect(sourceLine({ ...code({ id: "custom_python" }), ...one })).toBe(
      "Your code · uploaded 60 results · manual spans",
    );
    expect(
      sourceLine({ ...code({ id: "strands", tracing: "otel" }), ...one }),
    ).toBe("Your code · uploaded 60 results · OpenTelemetry");
  });
  it("code: sandbox and changed params", () => {
    const r = code(
      { id: "langgraph", tracing: "auto" },
      {
        sandbox: {
          source: "repo",
          repo: "acme/support-agents",
          branch: "fix-refunds",
          entry: "agent.py:graph",
          network: "off",
          resources: { cpu: 1, memory_gb: 2 },
        },
        params: {
          schema: [
            { name: "temperature", default: 0.2 },
            { name: "retriever_k", default: 4 },
            { name: "model", default: "gpt-4.1-mini" },
          ],
          values: { temperature: 0.9, retriever_k: 9, model: "gpt-4o-mini" },
        },
      },
    );
    expect(sourceLine(r)).toBe(
      "Your code · uploaded 90 results · traceAI · Future AGI sandbox · acme/support-agents@fix-refunds · agent.py:graph · network Off · 1 vCPU · model gpt-4o-mini · temperature 0.9 · retriever_k 9",
    );
  });
  it("harness: engine model, per-try cap and time limit", () => {
    const r = run({
      kind: "harness",
      harness: { name: "Codex CLI", model: "gpt-5-codex", max_cost_usd: 1 },
      engine: { model: "gpt-5-mini" },
      limits: { max_cost_per_try_usd: 0.02, timeout_s: 10 },
    });
    expect(sourceLine(r)).toBe(
      "Codex CLI × gpt-5-mini · $0.02 per try · timeout 10 s",
    );
  });
  it("prompt: per-tool modes, live host and replay", () => {
    const r = run({
      kind: "prompt",
      prompt: { name: "Escalation reply", version: "V2" },
      model: { id: "gpt-4.1" },
      tools: [
        { name: "a", mode: "mock" },
        { name: "b", mode: "mock" },
        {
          name: "c",
          mode: "live",
          endpoint: { url: "https://tools.acme.ai/x" },
        },
      ],
      max_tool_turns: 3,
      replay: { mode: "score_only" },
    });
    expect(sourceLine(r)).toBe(
      "Prompt + tools · Escalation reply V2 × gpt-4.1 · 3 tools: 2 mock, 1 live · live tools.acme.ai · max 3 tool turns · replay · score only · Runs in Future AGI",
    );
  });
});

it("tracesLine: the all-runs line", () => {
  const r = run({ kind: "harness", harness: { name: "Claude Code" } });
  expect(tracesLine(r, { datasetName: "Support ops tasks · sandbox" })).toBe(
    'Traces: 90 · in "Support ops tasks · sandbox · runs" · each cell\'s trace chip opens one',
  );
});

it("harnessLiveLine: starting, running, done", () => {
  const h = { config: { kind: "harness" }, concurrency: 10 };
  expect(
    harnessLiveLine({
      ...h,
      status: "Queued",
      status_detail: "Starting sandboxes",
      progress: { done: 0, total: 30 },
    }),
  ).toBe("Starting sandboxes 5 of 5 · 30 rows queued");
  expect(
    harnessLiveLine(
      {
        ...h,
        status: "Running",
        progress: { done: 15, scored: 12, total: 30 },
      },
      { infra: 1 },
    ),
  ).toBe(
    "Starting sandboxes 5 of 5 · Running 10 rows · Checking 3 · 1 setup failed (not counted)",
  );
  expect(harnessLiveLine({ ...h, status: "Completed" })).toBe(null);
});
