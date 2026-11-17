import { describe, expect, it } from "vitest";
import {
  HARNESS_SUBTABS,
  NO_FILES_TEXT,
  collectedFiles,
  collectedSummary,
  containerLine,
  costLine,
  endStateRow,
  evalRows,
  money,
  seconds,
  transcriptLine,
  transcriptMessages,
} from "../artifactsModel";

describe("artifactsModel", () => {
  it("end-state rows: two columns with →, amounts as $129.00", () => {
    expect(
      endStateRow({
        path: "refunds",
        before: null,
        after: { order: "A-8830", amount: 129 },
        ok: true,
      }),
    ).toEqual({
      path: "refunds",
      change: "+1 row (A-8830, $129.00)",
      ok: true,
    });
    expect(
      endStateRow({
        path: "orders.A-8830.refund_status",
        before: null,
        after: "issued",
      }).change,
    ).toBe("none → issued");
    expect(
      endStateRow({
        path: "tickets.T-5521.status",
        before: "open",
        after: "solved",
      }).change,
    ).toBe("open → solved");
    expect(
      endStateRow({
        path: "orders.A-7781.refunded_total",
        before: 0,
        after: 129,
      }).change,
    ).toBe("$0.00 → $129.00");
  });
  it("transcript line and messages", () => {
    expect(transcriptLine({ turns: 14, tool_calls: 7 }, 74000)).toBe(
      "14 turns · 7 tool calls · 74 s",
    );
    expect(
      transcriptMessages([
        { role: "user", text: "hi" },
        { role: "tool", tool: "lookup_order", text: "x" },
      ]),
    ).toEqual([
      { role: "user", content: "hi" },
      { role: "tool", name: "lookup_order", content: "x" },
    ]);
  });
  it("cost line", () => {
    expect(
      costLine(
        {
          total_usd: 0.21,
          model_calls: 41,
          tokens_in: 38120,
          tokens_out: 4906,
          user_sim_usd: 0.004,
          setup_ms: 9000,
          agent_ms: 61000,
          evals_ms: 4000,
        },
        74000,
      ),
    ).toBe(
      "$0.21 · 41 model calls · 38,120 in / 4,906 out tokens · simulated customer $0.004 · wall 74 s (setup 9 s · agent 61 s · evals 4 s)",
    );
    expect(costLine(null)).toBe(null);
  });
  it("evals: checks and the judge's reason", () => {
    const rows = evalRows(
      [
        { key: "order_found", name: "Order found", passed: true },
        { key: "refund_once", passed: null },
        { key: "reply_rubric", name: "Task reply rubric v1", passed: true },
      ],
      { "eval:reply_rubric": { value: 1, reason: "Clear, correct timeline" } },
    );
    expect(rows.map((r) => `${r.key} ${r.glyph} ${r.type}`)).toEqual([
      "order_found ✓ Check",
      "refund_once – Check",
      "reply_rubric ✓ Judge",
    ]);
    expect(rows[2].reason).toBe("Clear, correct timeline");
  });
  it("container, money, seconds", () => {
    expect(
      containerLine({
        image: "ghcr.io/acme/acme-support-sandbox:1.4",
        exit_code: 0,
      }),
    ).toBe("acme-support-sandbox:1.4 · exit 0");
    expect(money(0.004)).toBe("$0.004");
    expect(money(129)).toBe("$129.00");
    expect(seconds(2900)).toBe("2.9 s");
    expect(seconds(150000)).toBe("3 min");
  });
});

describe("collectedFiles (options-truth L2 G6: the Files sub-tab)", () => {
  it("lists what Collect gathered, labelled, with line counts", () => {
    const art = {
      collected: [
        {
          kind: "git_diff",
          name: "changes.diff",
          text: "+ issue_refund A-7781\n+ send_email A-7781",
        },
        {
          kind: "final_message",
          name: "final_message.txt",
          text: "Refunded $84.00.",
        },
        { kind: "log", name: "run.log", text: "" },
        { kind: "files", name: "reports/*.md", text: "2 changed" },
      ],
    };
    expect(collectedFiles(art)).toEqual([
      {
        kind: "git_diff",
        label: "Git diff",
        name: "changes.diff",
        text: "+ issue_refund A-7781\n+ send_email A-7781",
        lines: 2,
      },
      {
        kind: "final_message",
        label: "Final message",
        name: "final_message.txt",
        text: "Refunded $84.00.",
        lines: 1,
      },
      {
        kind: "log",
        label: "Terminal log",
        name: "run.log",
        text: "",
        lines: 0,
      },
      {
        kind: "files",
        label: "Files",
        name: "reports/*.md",
        text: "2 changed",
        lines: 1,
      },
    ]);
  });
  it("is empty without a collection; the tab says how to turn it on", () => {
    expect(collectedFiles(null)).toEqual([]);
    expect(collectedFiles({ collected: [] })).toEqual([]);
    expect(NO_FILES_TEXT).toMatch(/More settings › Collect/);
    expect(HARNESS_SUBTABS.map((t) => t.label)).toContain("Files");
  });
});

describe("collectedSummary (options-truth L3 G6 fix 10)", () => {
  it("counts matched paths and names a pattern that matched nothing", () => {
    const files = collectedFiles({
      collected: [
        { kind: "git_diff", name: "changes.diff", text: "(no changes)" },
        { kind: "log", name: "run.log", text: "[user] hi" },
        { kind: "files", name: "reports/*.json", paths: [] },
      ],
    });
    expect(collectedSummary(files)).toBe(
      "2 collected · changes.diff · run.log · reports/*.json: no match",
    );
    const two = collectedFiles({
      collected: [
        {
          kind: "files",
          name: "reports/*.json",
          paths: ["reports/a.json", "reports/b.json"],
        },
      ],
    });
    expect(collectedSummary(two)).toBe(
      "2 collected · reports/*.json (2 files)",
    );
  });
});
