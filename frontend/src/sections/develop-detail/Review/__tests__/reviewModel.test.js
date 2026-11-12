import { describe, expect, it } from "vitest";
import {
  blindLeft,
  choiceText,
  choiceValue,
  compareUrl,
  defaultQueueName,
  exitLabelFor,
  matchingReviewQueues,
  normalizePreference,
  openRegressionQueue,
  orderSort,
  PAIRWISE_LABEL,
  pairwiseOptions,
  primaryMetricKey,
  queueMetadata,
  reviewItems,
  reviewPageUrl,
  regressionQueueName,
  regressionReviewLabel,
  reviewContextButtons,
  reviewMinutes,
  savedChoiceText,
  reviewQueueFor,
  seededShuffle,
  shortDate,
  valueChoice,
  winRateLine,
} from "../reviewModel";

const A = "RUN_3A";
const B = "RUN_4A";
const queues = [
  {
    id: "Q2",
    created_at: "2026-10-12T09:00:00Z",
    metadata: {
      kind: "experiment_review",
      mode: "pairwise",
      run_a: A,
      run_b: B,
    },
  },
  {
    id: "Q3",
    created_at: "2026-10-12T10:00:00Z",
    metadata: { kind: "experiment_review", mode: "rubric", run_id: B },
  },
  { id: "Q1", metadata: { kind: "dataset_qa" } },
];

describe("queue matching", () => {
  it("matches a pair in either order and a rubric run", () => {
    expect(
      reviewQueueFor(queues, { mode: "pairwise", runA: B, runB: A }).id,
    ).toBe("Q2");
    expect(reviewQueueFor(queues, { mode: "rubric", runId: B }).id).toBe("Q3");
    expect(reviewQueueFor(queues, { mode: "rubric", runId: A })).toBeNull();
    expect(
      matchingReviewQueues(queues, { mode: "pairwise", runA: A, runB: "X" }),
    ).toEqual([]);
  });
});

describe("blind sides and order", () => {
  it("is deterministic per seed and uses both sides", () => {
    expect(blindLeft("ROW_57|me")).toBe(blindLeft("ROW_57|me"));
    const sides = new Set(
      Array.from({ length: 20 }, (_, i) => blindLeft(`row-${i}|me`)),
    );
    expect(sides).toEqual(new Set(["A", "B"]));
  });

  it("shuffles with a seed, keeping every id", () => {
    const ids = ["a", "b", "c", "d", "e", "f"];
    const one = seededShuffle(ids, "Review · #4A vs #3A · 14 Oct");
    expect(one).toEqual(seededShuffle(ids, "Review · #4A vs #3A · 14 Oct"));
    expect([...one].sort()).toEqual(ids);
    expect(ids).toEqual(["a", "b", "c", "d", "e", "f"]);
  });

  it("maps Order to the R3 sort", () => {
    expect(
      orderSort("regressions", {
        runA: A,
        runB: B,
        metric: "eval:task_completion",
      }),
    ).toBe("regressed");
    expect(
      orderSort("regressions", {
        metric: "eval:quality",
        runB: B,
        mode: "rubric",
      }),
    ).toBe("order");
    expect(orderSort("dataset", { metric: "x", runB: B })).toBe("order");
    expect(orderSort("random", {})).toBe("order");
  });

  it("picks the primary metric from a pill, then task completion, then the first eval", () => {
    const metrics = [
      { key: "eval:correctness" },
      { key: "eval:task_completion" },
    ];
    expect(
      primaryMetricKey(metrics, [
        { type: "movement", metric: "eval:tone", dir: "regressed" },
      ]),
    ).toBe("eval:tone");
    expect(primaryMetricKey(metrics, [])).toBe("eval:task_completion");
    expect(
      primaryMetricKey([{ key: "ops:latency" }, { key: "eval:correctness" }]),
    ).toBe("eval:correctness");
    expect(primaryMetricKey([])).toBeNull();
  });
});

describe("queue payloads", () => {
  it("names queues like the existing review queues", () => {
    const d = new Date("2026-10-14T10:00:00");
    expect(
      defaultQueueName("pairwise", { labelA: "#3A", labelB: "#4A" }, d),
    ).toBe("Review · #4A vs #3A · Oct 14");
    expect(defaultQueueName("rubric", { label: "#4A" }, d)).toBe(
      "Quality · #4A · Oct 14",
    );
    expect(shortDate(d)).toBe("Oct 14");
  });

  it("start here: regressions queue label, name, time and reuse", () => {
    expect(reviewMinutes(0)).toBe(0);
    expect(reviewMinutes(1)).toBe(1);
    expect(reviewMinutes(14)).toBe(10);
    expect(reviewMinutes(248)).toBe(165);
    expect(regressionReviewLabel(14)).toBe(
      "Review the 14 regressions (about 10 min)",
    );
    expect(regressionReviewLabel(1)).toBe(
      "Review the 1 regression (about 1 min)",
    );
    const d = new Date("2026-10-16T10:00:00");
    expect(regressionQueueName({ labelA: "#2B", labelB: "#2D" }, d)).toBe(
      "Regressions · #2D vs #2B · Oct 16",
    );
    const q = {
      id: "QR",
      item_count: 3,
      completed_count: 1,
      metadata: { focus: "regressions", run_a: A, run_b: B },
    };
    expect(openRegressionQueue([q], { runA: A, runB: B })).toBe(q);
    expect(
      openRegressionQueue([{ ...q, completed_count: 3 }], { runA: A, runB: B }),
    ).toBeNull();
    expect(openRegressionQueue([q], { runA: B, runB: A })).toBeNull();
    expect(PAIRWISE_LABEL.settings.kind).toBe("pairwise");
  });

  it("builds item and queue metadata", () => {
    const [item] = reviewItems(["ROW_57"], {
      mode: "pairwise",
      runA: A,
      runB: B,
      blind: true,
      seed: "q",
    });
    expect(item).toMatchObject({
      source_type: "dataset_row",
      source_id: "ROW_57",
      metadata: {
        kind: "experiment_review",
        mode: "pairwise",
        run_a: A,
        run_b: B,
        blind: true,
      },
    });
    expect(["A", "B"]).toContain(item.metadata.left);
    expect(
      reviewItems(["ROW_1"], {
        mode: "pairwise",
        runA: A,
        runB: B,
        blind: false,
      })[0].metadata.left,
    ).toBe("A");
    expect(
      reviewItems(["ROW_1"], { mode: "rubric", runId: B })[0].metadata,
    ).toEqual({
      kind: "experiment_review",
      mode: "rubric",
      run_id: B,
    });
    expect(
      queueMetadata({ mode: "rubric", runId: B, datasetId: "DS" }),
    ).toEqual({
      kind: "experiment_review",
      mode: "rubric",
      run_id: B,
      blind: false,
      dataset_id: "DS",
    });
  });
});

describe("pairwise vocabulary", () => {
  it("normalizes R3 preferences in both shapes", () => {
    expect(
      normalizePreference(
        {
          choice: "A",
          votes: { A: 1, B: 0, tie: 0 },
          source: "human",
          judge: { choice: "B", model: "gpt-4o", reason: "Clear." },
        },
        A,
        B,
      ),
    ).toEqual({
      choice: "A",
      votes: { A: 1, B: 0, tie: 0 },
      judge: { choice: "B", model: "gpt-4o", reason: "Clear." },
    });
    // world shape: winner / votes keyed by run id; judge-only rows have no choice
    expect(
      normalizePreference(
        {
          winner: null,
          human_choice: null,
          source: "judge",
          votes: { [A]: 0, [B]: 0, tie: 0 },
          judge: { winner: B, model: "gpt-4o", reason: "r" },
        },
        A,
        B,
      ),
    ).toMatchObject({ choice: null, judge: { choice: "B" } });
    expect(normalizePreference(null, A, B).choice).toBeNull();
  });

  it("maps choices to label values and back", () => {
    expect(choiceValue("tie")).toEqual({ selected: ["Tie"] });
    expect(choiceValue("B")).toEqual({ selected: ["B"] });
    expect(choiceValue(null)).toBeNull();
    expect(valueChoice({ selected: ["Tie"] })).toBe("tie");
    expect(valueChoice({ selected: [] })).toBeNull();
  });

  it("orders the buttons by side", () => {
    const named = pairwiseOptions({
      left: "A",
      blind: false,
      labelA: "#3A",
      labelB: "#4A",
    });
    expect(named.map((o) => [o.key, o.choice, o.label])).toEqual([
      ["1", "A", "#3A is better"],
      ["2", "tie", "Tie"],
      ["3", "B", "#4A is better"],
    ]);
    const blind = pairwiseOptions({
      left: "B",
      blind: true,
      labelA: "#3A",
      labelB: "#4A",
    });
    expect(blind.map((o) => [o.choice, o.label])).toEqual([
      ["B", "Left is better"],
      ["tie", "Tie"],
      ["A", "Right is better"],
    ]);
    expect(choiceText("B", { labelA: "#3A", labelB: "#4A" })).toBe(
      "#4A is better",
    );
  });

  it("labels workspace buttons from the review context", () => {
    const ctx = {
      left: "B",
      blind: false,
      candidates: [
        { side: "B", run_label: "#4A" },
        { side: "A", run_label: "#3A" },
      ],
    };
    expect(
      reviewContextButtons(ctx).map((b) => [b.key, b.choice, b.label]),
    ).toEqual([
      ["1", "B", "#4A is better"],
      ["2", "tie", "Tie"],
      ["3", "A", "#3A is better"],
    ]);
    expect(reviewContextButtons({ ...ctx, blind: true })[0].label).toBe(
      "Response 1 is better",
    );
  });

  it("writes the win-rate line", () => {
    expect(
      winRateLine(
        {
          wins: 29,
          ties: 7,
          losses: 12,
          judged: 48,
          win_rate: 0.677,
          ci: [0.54, 0.79],
        },
        { labelB: "#4A" },
      ),
    ).toEqual({
      headline: "#4A preferred on 68% of 48 rows",
      ci: "95% CI 54–79%",
      counts: "W29 T7 L12",
      lowSample: false,
    });
    expect(winRateLine({ judged: 0 })).toBeNull();
  });
});

describe("urls", () => {
  it("builds the compare and review urls and the exit label", () => {
    expect(compareUrl("DS", A, B)).toBe(
      `/dashboard/develop/DS/compare/data?runs=${A},${B}&baseline=${A}`,
    );
    expect(
      reviewPageUrl("DS", "Q2", {
        from: "/dashboard/develop/DS/compare/data?runs=a,b",
      }),
    ).toBe(
      "/dashboard/develop/DS/review/Q2?from=%2Fdashboard%2Fdevelop%2FDS%2Fcompare%2Fdata%3Fruns%3Da%2Cb",
    );
    expect(exitLabelFor("/dashboard/develop/DS/compare/data")).toBe(
      "Back to compare runs",
    );
    expect(exitLabelFor("/dashboard/develop/experiment/E/data")).toBe(
      "Back to experiment",
    );
    expect(exitLabelFor(null)).toBe("Back to experiments");
  });
});

describe("savedChoiceText", () => {
  it("reads the vote with the item's own sides", () => {
    const ctx = { left: "B", blind: true };
    expect(savedChoiceText([{ value: { selected: ["B"] } }], ctx)).toBe(
      "Response 1 is better",
    );
    expect(savedChoiceText([{ value: { selected: ["Tie"] } }], ctx)).toBe(
      "Tie",
    );
    expect(savedChoiceText([{ value: { rating: 3 } }], ctx)).toBeNull();
  });
});
