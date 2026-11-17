import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import SessionHistory from "./SessionHistory";

vi.mock("../SessionsView/ConversationCard", () => ({
  default: ({ value }) => <span>{JSON.stringify(value.cellValue)}</span>,
}));
vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: () => ({}),
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("react-router", () => ({ useParams: () => ({}) }));
vi.mock("../SessionsView/EvaluationsContent", () => ({
  default: () => <span>Evaluation results</span>,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("./SessionSkeleton", () => ({ default: () => null }));
vi.mock("../SessionsView/MetadataContent", () => ({
  default: ({ metadata }) => (
    <pre data-testid="metrics">{JSON.stringify(metadata)}</pre>
  ),
}));
afterEach(cleanup);

const trace = (id, answer) => ({
  trace_id: id,
  input: { secret: "not selected" },
  output: { answer },
  system_metrics: {
    project_id: "actual-project",
    total_cost: 0,
    total_tokens: 0,
  },
  evals_metrics: {},
});
const fields = [
  { kind: "value", label: "Answer", path: "output.answer" },
  { kind: "metric", metric: "total_cost" },
];

describe("session trajectory selected fields", () => {
  it("applies selected fields to every paginated turn and keeps evaluations and View Trace", () => {
    const { rerender } = render(
      <SessionHistory traceDetail={[trace("t1", false)]} turnFields={fields} />,
    );
    expect(screen.getByText("false")).toBeTruthy();
    expect(screen.queryByText(/not selected/)).toBeNull();
    rerender(
      <SessionHistory
        traceDetail={[trace("t1", false), trace("t2", 0)]}
        turnFields={fields}
      />,
    );
    expect(screen.getAllByText("Answer")).toHaveLength(2);
    expect(screen.getAllByText("Evaluation results")).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "View Trace" })).toHaveLength(
      2,
    );
    screen
      .getAllByRole("tab", { name: /Metadata/ })
      .forEach((tab) => fireEvent.click(tab));
    expect(
      screen.getAllByTestId("metrics").map((el) => JSON.parse(el.textContent)),
    ).toEqual([{ total_cost: 0 }, { total_cost: 0 }]);
    expect(
      screen.getByText("t1").compareDocumentPosition(screen.getByText("t2")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("uses trace identity rather than list position when turns change", () => {
    const { rerender } = render(
      <SessionHistory
        traceDetail={[trace("t1", 1), trace("t2", 2)]}
        turnFields={fields}
      />,
    );
    fireEvent.click(screen.getAllByRole("tab", { name: /Metadata/ })[1]);
    rerender(
      <SessionHistory traceDetail={[trace("t2", 2)]} turnFields={fields} />,
    );
    expect(
      screen
        .getByRole("tab", { name: /Metadata/ })
        .getAttribute("aria-selected"),
    ).toBe("true");
  });

  it("preserves zero cost, token counts and latency in selected metrics", () => {
    const row = trace("zero", null);
    row.system_metrics = {
      ...row.system_metrics,
      total_latency_ms: 0,
      input_tokens: 0,
      output_tokens: 0,
    };
    render(
      <SessionHistory
        traceDetail={[row]}
        turnFields={[
          "total_latency_ms",
          "total_tokens",
          "input_tokens",
          "output_tokens",
          "total_cost",
        ].map((metric) => ({ kind: "metric", metric }))}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: /Metadata/ }));
    expect(JSON.parse(screen.getByTestId("metrics").textContent)).toEqual({
      total_latency_ms: "0ms",
      total_token_count: 0,
      input_tokens: 0,
      output_tokens: 0,
      total_cost: 0,
    });
  });

  it("renders no selected values or metrics for an intentional empty selection", () => {
    render(<SessionHistory traceDetail={[trace("t1", 0)]} turnFields={[]} />);
    expect(screen.queryByText("Answer")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: /Metadata/ }));
    expect(screen.getByTestId("metrics").textContent).toBe("{}");
    expect(screen.getByRole("button", { name: "View Trace" })).toBeTruthy();
  });
});
