import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import RowRunDetail from "../RowRunDetail";

const children = vi.hoisted(() => ({
  links: vi.fn(() => null),
  comments: vi.fn(() => null),
}));

vi.mock("../RunLinks", () => ({ default: children.links }));
vi.mock("src/sections/common/Collab/RunCommentsSection", () => ({
  default: children.comments,
}));
vi.mock("src/sections/common/ValueViewer/ValueViewer", () => ({
  default: ({ value }) => (
    <pre data-testid="trial-output">{JSON.stringify(value)}</pre>
  ),
}));
vi.mock("src/sections/common/Media/RowMedia", () => ({
  default: ({ value }) => (
    <pre data-testid="trial-media">{JSON.stringify(value)}</pre>
  ),
}));
vi.mock("src/sections/common/ValueViewer/TextDiff", () => ({
  default: ({ from, to }) => (
    <pre data-testid="trial-diff">{JSON.stringify({ from, to })}</pre>
  ),
  TextDiffLegend: () => <span>Diff legend</span>,
}));

const METRIC = "33333333-3333-4333-8333-333333333333";
const VERSION = "66666666-6666-4666-8666-666666666666";
const metrics = [
  {
    key: METRIC,
    name: "Correct",
    output_type: "pass_fail",
    higher_is_better: true,
    pass_threshold: null,
  },
];

const trial = (number, output, value, explanation, traceId) => ({
  trial_number: number,
  status: "completed",
  trace_id: traceId,
  output: { text: output },
  error: null,
  scores: {
    [METRIC]: {
      value,
      status: "assessed",
      passed: value === 1,
      evaluator_version_id: VERSION,
      explanation,
    },
  },
});

const run = {
  run_id: "44444444-4444-4444-8444-444444444444",
  example_id: "77777777-7777-4777-8777-777777777777",
  label: "#1",
  name: null,
  experiment_id: null,
  experiment_name: null,
  status: "completed",
  output: { text: "Run summary output" },
  error: "Run summary error",
  trials: 2,
  evals: {
    [METRIC]: {
      value: 0.5,
      n: 2,
      status: "assessed",
      std: 0.5,
      flaky: true,
    },
  },
  latency_ms: 120,
  cost: 0.01,
  ran_at: null,
  dataset_version: { id: VERSION, content_hash: "" },
  is_dataset_baseline: false,
  row_changed_after: false,
  target_type: "external",
  trace_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  trials_detail: [
    trial(
      1,
      "Paris",
      1,
      "Matches the reference.",
      "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    ),
    trial(
      2,
      "Lyon",
      0,
      "Does not match the reference.",
      "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    ),
  ],
};

const props = {
  rowId: "22222222-2222-4222-8222-222222222222",
  datasetId: "11111111-1111-4111-8111-111111111111",
  metrics,
  expected: { name: "Expected answer", value: "Paris" },
};
const detail = (value = run) => <RowRunDetail {...props} run={value} />;
const select = (number) =>
  fireEvent.click(screen.getByRole("button", { name: `Trial ${number}` }));
const lastCommentProps = () => children.comments.mock.calls.at(-1)[0];

const expectEmptyEvidence = () => {
  expect(screen.getByText("No output recorded for this trial.")).toBeVisible();
  expect(screen.getByText("No scores recorded for this trial.")).toBeVisible();
  expect(screen.queryByTestId("trial-output")).not.toBeInTheDocument();
  expect(screen.queryByTestId("trial-diff")).not.toBeInTheDocument();
  expect(screen.queryByText("Run summary output")).not.toBeInTheDocument();
  expect(screen.queryByText("Matches the reference.")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Correct · Pass" })).toBeNull();
  expect(lastCommentProps().traceId).toBeNull();
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("row run trial evidence", () => {
  it("switches output, scores, explanations and trace identity by trial_number", () => {
    render(detail());
    expect(screen.getByRole("button", { name: "Trial 1" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByTestId("trial-output")).toHaveTextContent("Paris");
    expect(
      screen.getByRole("button", { name: "Correct · Pass" }),
    ).toBeVisible();
    expect(screen.getByText("Matches the reference.")).toBeVisible();
    expect(lastCommentProps().traceId).toBe(run.trials_detail[0].trace_id);

    select(2);
    expect(screen.getByRole("button", { name: "Trial 2" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByTestId("trial-output")).toHaveTextContent("Lyon");
    expect(screen.getByTestId("trial-output")).not.toHaveTextContent("Paris");
    expect(screen.getByTestId("trial-output")).not.toHaveTextContent(
      "Run summary output",
    );
    expect(
      screen.getByRole("button", { name: "Correct · Fail" }),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Correct · Pass" })).toBeNull();
    expect(screen.getByText("Does not match the reference.")).toBeVisible();
    expect(
      screen.queryByText("Matches the reference."),
    ).not.toBeInTheDocument();
    expect(lastCommentProps().traceId).toBe(run.trials_detail[1].trace_id);
    expect(children.links.mock.calls.at(-1)[0]).toMatchObject({
      rowId: props.rowId,
      datasetId: props.datasetId,
    });

    select(1);
    expect(screen.getByTestId("trial-output")).toHaveTextContent("Paris");
    expect(
      screen.getByRole("button", { name: "Correct · Pass" }),
    ).toBeVisible();
    expect(screen.queryByText("Does not match the reference.")).toBeNull();
  });

  it.each(["absent", "null"])(
    "shows empty evidence for a trial with %s output instead of another result",
    (shape) => {
      const empty = {
        trial_number: 2,
        status: "not_reached",
        trace_id: null,
        ...(shape === "null" ? { output: null, error: null, scores: {} } : {}),
      };
      render(detail({ ...run, trials_detail: [run.trials_detail[0], empty] }));
      fireEvent.click(
        screen.getByRole("button", { name: "Diff vs current expected" }),
      );
      expect(screen.getByTestId("trial-diff")).toHaveTextContent("Paris");
      select(2);
      expectEmptyEvidence();
      expect(screen.queryByText("Diff legend")).not.toBeInTheDocument();
      expect(screen.queryByText(/Against Expected answer/)).toBeNull();
      expect(
        screen.getByRole("button", { name: "Diff vs current expected" }),
      ).toBeDisabled();
    },
  );

  it.each([
    [
      "error",
      { message: "Attempt failed.", kind: "runtime" },
      "Attempt failed.",
    ],
    ["infra", { message: "Worker unavailable." }, "Worker unavailable."],
    ["error", { kind: "timeout" }, '{"kind":"timeout"}'],
    ["error", "Attempt stopped.", "Attempt stopped."],
    ["error", null, "No error details recorded for this trial."],
  ])(
    "renders %s trial errors without using the run error",
    (status, error, text) => {
      render(
        detail({
          ...run,
          trials_detail: [
            run.trials_detail[0],
            {
              ...run.trials_detail[1],
              status,
              error,
              output: null,
              scores: {},
              trace_id: null,
            },
          ],
        }),
      );
      select(2);
      expect(screen.getByText(text)).toBeVisible();
      expect(screen.queryByText("Run summary error")).not.toBeInTheDocument();
      expectEmptyEvidence();
    },
  );

  it("does not replace a missing selected trial with the first trial", () => {
    const { rerender } = render(detail());
    select(2);
    rerender(detail({ ...run, trials_detail: [run.trials_detail[0]] }));
    expectEmptyEvidence();
  });

  it("initializes selection from the first recorded trial identity", () => {
    render(
      detail({
        ...run,
        trials_detail: run.trials_detail.map((entry) => ({
          ...entry,
          trial_number: entry.trial_number + 2,
        })),
      }),
    );
    expect(screen.getByRole("button", { name: "Trial 3" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    select(4);
    expect(screen.getByTestId("trial-output")).toHaveTextContent("Lyon");
  });

  it.each([0, false, ""])("keeps a recorded scalar output %j", (output) => {
    render(
      detail({
        ...run,
        trials_detail: [{ ...run.trials_detail[0], output }],
      }),
    );
    expect(screen.getByTestId("trial-output").textContent).toBe(
      JSON.stringify(output),
    );
    expect(screen.queryByText("No output recorded for this trial.")).toBeNull();
  });

  it("does not invent trial evidence when the run has no trial details", () => {
    render(detail({ ...run, trials_detail: [] }));
    expectEmptyEvidence();
  });
});
