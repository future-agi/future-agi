import { describe, expect, it } from "vitest";

import {
  analysisToMarkdown,
  hasStoredAnalysis,
  missingAnalysisReason,
  SAMPLE_TRACE_LIMIT,
} from "../issueAnalysis";

const issue = (overrides = {}) => ({
  cluster_id: "S-1A2B3C4D",
  source: "scanner",
  error: { name: "Agent refunds the wrong amount for two-item orders" },
  trace_count: 92,
  first_seen: "2026-11-01T09:00:00Z",
  last_seen: "2026-11-18T10:30:00Z",
  description: "The agent refunds the first item only.",
  fix_hypothesis: "Sum every line item before calling refund_order.",
  falsifier: "Two-item refunds stay wrong after the agent reads both items.",
  ...overrides,
});

const trace = (id, overrides = {}) => ({
  id,
  timestamp: "2026-11-18T10:00:00Z",
  evidence: {
    input: "Refund order 42",
    output: "Refunded $10",
    judge_reason: "Refunded one item of two",
    score: 0.2,
  },
  ...overrides,
});

const overview = (overrides = {}) => ({
  pattern_summary: {
    insights: [
      {
        title: "Common tool",
        value: "17 / 38",
        caption: "**refund_order** runs in most failing traces",
      },
    ],
  },
  representative_traces: [trace("t-1"), trace("t-2")],
  representative_total: 92,
  ...overrides,
});

const section = (markdown, heading) =>
  markdown.split(`## ${heading}\n\n`)[1]?.split("\n\n## ")[0];

describe("issue analysis", () => {
  it("knows which issues carry a stored analysis", () => {
    expect(hasStoredAnalysis(issue())).toBe(true);
    expect(
      hasStoredAnalysis(issue({ fix_hypothesis: null, falsifier: null })),
    ).toBe(false);
  });

  it("says why an eval or a scan issue has no stored analysis", () => {
    expect(missingAnalysisReason({ source: "eval" })).toMatch(/eval results/);
    expect(missingAnalysisReason({ source: "scanner" })).toBe(
      "The Error Feed stored no likely cause or suggested change for this issue.",
    );
  });
});

describe("analysisToMarkdown", () => {
  it("writes the stored analysis, the pattern and a sample of traces", () => {
    const md = analysisToMarkdown(issue(), overview());

    expect(md.startsWith("# Agent refunds the wrong amount")).toBe(true);
    expect(md).toContain("S-1A2B3C4D · 92 traces");
    expect(section(md, "Likely cause")).toBe(
      "The agent refunds the first item only.",
    );
    expect(section(md, "Suggested change")).toBe(
      "Sum every line item before calling refund_order.",
    );
    expect(section(md, "Wrong if")).toBe(
      "Two-item refunds stay wrong after the agent reads both items.",
    );
    expect(section(md, "Pattern")).toBe(
      "- **Common tool**: 17 / 38. **refund_order** runs in most failing traces",
    );
    expect(md).toContain("The 2 traces seen most recently.");
    expect(md).toContain("### t-1");
    expect(md).toContain("### t-2");
    expect(md).toContain("Judge reason: Refunded one item of two");
  });

  it("keeps trace text intact inside a fence longer than its backticks", () => {
    const input = "Run this:\n```\nrm -rf /\n```";
    const md = analysisToMarkdown(
      issue(),
      overview({
        representative_traces: [trace("t-1", { evidence: { input } })],
      }),
    );

    expect(md).toContain(`\`\`\`\`text\n${input}\n\`\`\`\``);
  });

  it("samples at most the limit of traces", () => {
    const many = Array.from({ length: SAMPLE_TRACE_LIMIT + 5 }, (_, i) =>
      trace(`t-${i}`),
    );
    const md = analysisToMarkdown(
      issue(),
      overview({ representative_traces: many, representative_total: 200 }),
    );

    expect(md).toContain(
      `The ${SAMPLE_TRACE_LIMIT} traces seen most recently.`,
    );
    expect(md.match(/^### /gm)).toHaveLength(SAMPLE_TRACE_LIMIT);
  });

  it("keeps the heading, counts and pattern lines well formed", () => {
    const md = analysisToMarkdown(
      issue({
        error: { name: "First line of a mechanism.\nSecond line." },
        trace_count: 1,
      }),
      overview({
        pattern_summary: {
          insights: [
            { title: "", value: "82%", caption: "" },
            { title: "Empty", value: "", caption: "" },
          ],
        },
        representative_traces: [trace("t-1")],
      }),
    );

    expect(md.split("\n")[0]).toBe("# First line of a mechanism.");
    expect(md).toContain("S-1A2B3C4D · 1 trace ·");
    expect(section(md, "Pattern")).toBe("- 82%");
    expect(md).toContain("The 1 trace seen most recently.");
  });

  it("exports what an eval issue has and says why the rest is missing", () => {
    const md = analysisToMarkdown(
      issue({
        source: "eval",
        description: "The reply promised a refund outside the policy.",
        fix_hypothesis: null,
        falsifier: null,
      }),
      overview({ pattern_summary: { insights: [] } }),
    );

    expect(md).not.toContain("## Likely cause");
    expect(md).not.toContain("## Suggested change");
    expect(md).not.toContain("## Wrong if");
    expect(section(md, "Eval explanation")).toBe(
      "The reply promised a refund outside the policy.",
    );
    expect(md).toContain(missingAnalysisReason({ source: "eval" }));
    expect(md).not.toContain("## Pattern");
  });
});
