// The row drawer's score card for a failed evaluation. It
// showed no reason at all; it now shows one plain sentence for the stored
// reason's kind under Reason. The raw stored text (launcher log lines,
// `runChild():486`) never shows; with no usable reason there is no Reason.
/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: vi.fn(), post: vi.fn() },
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({ trackEvent: vi.fn(), Events: {} }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { name: "Tester" } }),
}));
vi.mock("src/sections/common/Runs/RunChip", () => ({ default: () => null }));
vi.mock("src/sections/common/Metrics/DirectionIcon", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/EvalTypes/TypedScoreDetails", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/DecisionJudges/DecisionCallPanel", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/EvalHandoffs/OpenInEvalsLink", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/SafeMarkdown/ReasonText", () => ({
  default: ({ text }) => <div data-testid="reason-text">{text}</div>,
}));

import ScoreCard from "../ScoreCard";

const KEY = "metric-code-eval";
const EXECUTOR_DOWN =
  "Code executor unavailable: the code-executor service could not be reached";
const LAUNCHER_RAW =
  "Code eval input validation failed: No output. Exit code: 255.\n" +
  "[E][2026-11-11T05:40:12+0000][1] runChild():486 Launching child process failed";
const def = { key: KEY, name: "Code eval", output_type: "score" };
const run = { run_id: "r1", experiment_id: "x1", dataset_id: "d1" };

const resultWith = (explanation) => ({
  result_id: "r1:e1",
  scores: {
    [KEY]: {
      value: null,
      status: "not_assessed",
      n: 0,
      error_trials: 1,
      ...(explanation ? { explanation } : {}),
    },
  },
  trials_detail: [
    {
      trial_number: 1,
      status: "completed",
      scores: {
        [KEY]: {
          value: null,
          status: "error",
          passed: null,
          evaluator_version_id: "v1",
          explanation: explanation ?? null,
        },
      },
    },
  ],
});

const renderCard = (result) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ScoreCard def={def} run={run} result={result} trial={1} readOnly />
    </QueryClientProvider>,
  );

describe("Row drawer score card of a failed evaluation", () => {
  it("says under Reason that the sandbox could not be reached", () => {
    renderCard(resultWith(EXECUTOR_DOWN));
    expect(screen.getByText("Reason")).toBeInTheDocument();
    expect(screen.getByTestId("reason-text")).toHaveTextContent(
      "The code sandbox could not be reached; ask your administrator.",
    );
    expect(document.body.innerHTML).not.toContain("code-executor service");
  });

  it("never shows a raw multi-line launcher error", () => {
    renderCard(resultWith(LAUNCHER_RAW));
    expect(screen.getByTestId("reason-text")).toHaveTextContent(
      "The code sandbox could not start; ask your administrator.",
    );
    expect(document.body.innerHTML).not.toContain("runChild()");
    expect(document.body.innerHTML).not.toContain("Exit code: 255");
  });

  it("shows no Reason for raw text of any other kind", () => {
    renderCard(resultWith('Traceback (most recent call last):\n  File "x.py"'));
    expect(screen.queryByText("Reason")).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain("Traceback");
  });

  it("shows no Reason when none is stored", () => {
    renderCard(resultWith(null));
    expect(screen.queryByText("Reason")).not.toBeInTheDocument();
    expect(screen.queryByTestId("reason-text")).not.toBeInTheDocument();
  });
});
