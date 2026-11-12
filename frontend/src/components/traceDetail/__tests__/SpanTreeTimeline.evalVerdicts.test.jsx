import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import SpanTreeTimeline from "../SpanTreeTimeline";

// Expose each icon's name so a test can tell the red shield from the others.
vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: ({ icon }) => <span data-testid="iconify" data-icon={icon} />,
}));

const START = "2026-03-29T10:00:00.000Z";

// One LLM span under the root, carrying `evalScores`.
const traceWith = (evalScores) => [
  {
    observation_span: {
      id: "root",
      name: "root-agent",
      observation_type: "chain",
      start_time: START,
      latency_ms: 1000,
      status: "OK",
    },
    children: [
      {
        observation_span: {
          id: "llm",
          name: "llm-call",
          observation_type: "llm",
          start_time: START,
          latency_ms: 100,
          status: "OK",
        },
        eval_scores: evalScores,
        children: [],
      },
    ],
  },
];

const renderTimeline = (evalScores) =>
  render(
    <SpanTreeTimeline
      spans={traceWith(evalScores)}
      selectedSpanId="root"
      onSelectSpan={vi.fn()}
    />,
  );

const icons = () =>
  screen.getAllByTestId("iconify").map((el) => el.getAttribute("data-icon"));

// SW5-31: the timeline reads verdicts the way the Trace view does.
describe("SpanTreeTimeline eval verdicts", () => {
  it("does not count a choice result or an eval that has not run as a failure", () => {
    renderTimeline([
      { eval_name: "Accuracy", score: 90 },
      { eval_name: "Tone", score: null, result: ["friendly"] },
      { eval_name: "Groundedness", score: null, result: null },
    ]);

    // Root roll-up: one verdict, and it passed.
    expect(screen.getByText("1/1")).toBeInTheDocument();
    expect(screen.queryByText("1/3")).not.toBeInTheDocument();
    // No red shield anywhere; the root badge and the span chip are green.
    expect(icons()).not.toContain("solar:shield-warning-linear");
    expect(
      icons().filter((icon) => icon === "solar:shield-check-linear"),
    ).toHaveLength(2);
  });

  it("shows a neutral shield, no count and no root badge when no eval has a verdict", () => {
    renderTimeline([
      { eval_name: "Tone", score: null, result: ["neutral"] },
      { eval_name: "Policy", score: null, result: null, error: true },
    ]);

    expect(screen.queryByText("0/2")).not.toBeInTheDocument();
    expect(icons()).toContain("mdi:shield-outline");
    expect(icons()).not.toContain("solar:shield-warning-linear");
    expect(icons()).not.toContain("solar:shield-check-linear");
  });

  it("still flags a real failure, counted once", () => {
    renderTimeline([
      { eval_name: "Accuracy", score: 20 },
      { eval_name: "Tone", score: null, result: ["rude"] },
    ]);

    expect(screen.getByText("0/1")).toBeInTheDocument();
    expect(screen.queryByText("0/2")).not.toBeInTheDocument();
    expect(icons()).toContain("solar:shield-warning-linear");
    // The span chip's failure count.
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("takes a boolean result over the score, like the Trace view", () => {
    renderTimeline([{ eval_name: "Threshold", score: 65, result: false }]);

    expect(screen.getByText("0/1")).toBeInTheDocument();
    expect(icons()).toContain("solar:shield-warning-linear");
  });
});
