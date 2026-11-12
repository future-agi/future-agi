// W1-9 B3: step badges, token split, graph node subtitles and eval steps.
import { describe, expect, it } from "vitest";
import {
  badgeLabel,
  failingByNode,
  stepBadgesOf,
  tokenSplitText,
  verdictKind,
} from "../stepBadges";
import { evalStepLine, evalStepsOf } from "../evalSteps";

const e = (o) => ({ eval_name: "Correctness", ...o });
const entry = (id, scores, span = {}) => ({
  observation_span: { id, name: id, observation_type: "tool", ...span },
  eval_scores: scores,
  children: [],
});

describe("step badges", () => {
  it("reads the scorer's verdict, never re-scores", () => {
    expect(verdictKind(e({ score: 65, result: false }))).toBe("fail");
    expect(verdictKind(e({ score: 40, result: true }))).toBe("pass");
    expect(verdictKind(e({ score: null, result: null, status: "na" }))).toBe(
      "na",
    );
    expect(verdictKind(e({ score: null, error: true }))).toBe("error");
    expect(verdictKind(e({ score: null, status: "not_reached" }))).toBe(
      "not_reached",
    );
    expect(verdictKind(e({ score: null }))).toBe("skipped");
  });

  it("labels failing first: ✕ check, then score errors, N/A, skipped", () => {
    const ent = entry("t", [
      e({ status: "skipped", score: null }),
      e({ check_key: "order_found", score: 100, result: true }),
      e({ status: "na", score: null, explanation: "not a question" }),
      e({
        check_key: "refund_amount",
        score: 0,
        result: false,
        explanation: "$40 not $45",
        run_label: "#2H",
      }),
      e({ error: true, status: "error", score: null }),
    ]);
    expect(stepBadgesOf(ent).map((b) => b.label)).toEqual([
      "✕ refund_amount",
      "Score error · Correctness",
      "N/A · Correctness",
      "Skipped · Correctness",
    ]);
    // several runs judged the step: each badge names its run
    const two = entry("t", [
      e({ score: 0, result: false, run_label: "#2A" }),
      e({ score: 0, result: false, run_label: "#2A·2" }),
    ]);
    expect(stepBadgesOf(two).map((b) => b.label)).toEqual([
      "✕ Correctness · #2A",
      "✕ Correctness · #2A·2",
    ]);
    // never-ran verdicts fold into one quiet badge
    const nr = entry("t", [
      e({
        check_key: "reply_rubric",
        status: "not_reached",
        score: null,
        explanation: "earlier check failed",
      }),
      e({ check_key: "timeline_stated", status: "not_reached", score: null }),
    ]);
    expect(stepBadgesOf(nr)).toEqual([
      {
        kind: "not_reached",
        label: "2 not reached",
        reason:
          "reply_rubric: earlier check failed\ntimeline_stated: not reached",
      },
    ]);
    expect(stepBadgesOf(ent)[0].reason).toBe("$40 not $45");
    expect(stepBadgesOf(ent, { withPass: true }).pop().label).toBe(
      "✓ order_found",
    );
    expect(badgeLabel(e({ status: "not_reached", score: null }))).toBe(
      "Not reached · Correctness",
    );
  });

  it("an LLM step reads prompt · completion · cached (cached only when > 0)", () => {
    const llm = {
      observation_type: "llm",
      prompt_tokens: 1212,
      completion_tokens: 96,
      span_attributes: { "llm.token_count.prompt_details.cache_read": 1024 },
    };
    expect(tokenSplitText(llm)).toBe(
      "prompt 1,212 · completion 96 · cached 1,024",
    );
    expect(
      tokenSplitText({
        ...llm,
        span_attributes: { "llm.token_count.prompt_details.cache_read": 0 },
      }),
    ).toBe("prompt 1,212 · completion 96");
    expect(tokenSplitText({ ...llm, observation_type: "tool" })).toBeNull();
    expect(tokenSplitText({ observation_type: "llm" })).toBeNull();
  });

  it("graph nodes carry their spans' failing checks", () => {
    const tree = [
      {
        observation_span: { id: "root" },
        children: [
          entry("s1", [
            e({ check_key: "order_found", score: 0, result: false }),
          ]),
          entry("s2", [e({ score: 100, result: true })]),
          entry("s3", [
            e({ check_key: "refund_amount", score: 0, result: false }),
          ]),
        ],
      },
    ];
    expect(failingByNode(tree, { n1: ["s1", "s3"], n2: ["s2"] })).toEqual({
      n1: "✕ order_found, refund_amount",
    });
  });
});

describe("eval steps (E39)", () => {
  const tree = [
    {
      observation_span: { id: "root", name: "run" },
      eval_scores: [
        {
          eval_name: "Task success",
          score: 0,
          result: false,
          run_id: "c",
          explanation: "Address updated failed",
        },
      ],
      children: [
        {
          observation_span: { id: "llm", name: "respond" },
          eval_scores: [
            {
              eval_name: "Correctness",
              score: 0,
              result: false,
              run_id: "c",
              explanation: "The reply promises a refund the tool never issued.",
              judge_model: "gpt-4o",
              judge_latency_ms: 1200,
              judge_cost_usd: 0.0004,
              eval_trace_id: "7d000000-0001-4000-8000-000000000001",
            },
            { eval_name: "Tone", score: 100, result: true, run_id: "c" },
            {
              eval_name: "Groundedness",
              score: null,
              result: null,
              status: "na",
              run_id: "c",
              skipped_reason: "no tool output to ground",
            },
            { eval_name: "Correctness", score: 100, result: true, run_id: "p" },
          ],
          children: [],
        },
      ],
    },
  ];

  it("lists one step per verdict of this run, failing first", () => {
    const steps = evalStepsOf(tree, { runId: "c" });
    expect(steps.map((s) => `${s.name}:${s.kind}`)).toEqual([
      "Task success:fail",
      "Correctness:fail",
      "Groundedness:na",
      "Tone:pass",
    ]);
    expect(steps[1]).toMatchObject({
      spanName: "respond",
      judgeModel: "gpt-4o",
      evalTraceId: "7d000000-0001-4000-8000-000000000001",
    });
  });

  it("reads the cell's judge, time and cost; never invents a missing part", () => {
    const [, correct, na, tone] = evalStepsOf(tree, { runId: "c" });
    expect(evalStepLine(correct)).toBe(
      'Correctness · judge gpt-4o · Fail · "The reply promises a refund the tool never issued." · 1.2 s · $0.00040',
    );
    expect(evalStepLine(na)).toBe(
      "Groundedness · N/A · no tool output to ground",
    );
    expect(evalStepLine(tone)).toBe("Tone · Pass");
    expect(
      evalStepLine({
        name: "Tone",
        kind: "error",
        verdict: "Score error",
        reason: "judge returned 7",
      }),
    ).toBe("Tone · Score error · judge returned 7");
  });
});
