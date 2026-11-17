import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ExperimentSourceGate from "../ExperimentSourceGate";

vi.mock("../RunExperimentFormSkeleton", () => ({
  default: () => <span>Loading experiment</span>,
}));
afterEach(cleanup);
const ready = { data: { results: [] } };

describe("experiment source loading", () => {
  it.each([401, 403, 404])(
    "shows an actionable unavailable state for %s without mounting incomplete cards",
    (statusCode) => {
      const refetch = vi.fn();
      render(
        <ExperimentSourceGate
          detail={ready}
          configs={{ isError: true, error: { statusCode }, refetch }}
        >
          <input aria-label="Draft" />
        </ExperimentSourceGate>,
      );
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Experiment unavailable",
      );
      expect(screen.queryByLabelText("Draft")).not.toBeInTheDocument();
      expect(screen.queryByText("Loading experiment")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(refetch).toHaveBeenCalledTimes(1);
    },
  );

  it("reports a missing detail even before the dataset id is known", () => {
    render(
      <ExperimentSourceGate
        detail={{ isError: true, error: { statusCode: 404 }, refetch: vi.fn() }}
        configs={{}}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Experiment unavailable",
    );
  });

  it("keeps the draft mounted after a failed background refresh and its retry", () => {
    const view = render(
      <ExperimentSourceGate detail={ready} configs={ready}>
        <input aria-label="Draft" defaultValue="Saved name" />
      </ExperimentSourceGate>,
    );
    const input = screen.getByLabelText("Draft");
    fireEvent.change(input, { target: { value: "Unsaved name" } });
    for (const isFetching of [false, true]) {
      view.rerender(
        <ExperimentSourceGate
          detail={ready}
          configs={{
            ...ready,
            isError: true,
            error: { statusCode: 503 },
            refetch: vi.fn(),
            isFetching,
          }}
        >
          <input aria-label="Draft" defaultValue="Saved name" />
        </ExperimentSourceGate>,
      );
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Automatic retries have stopped",
      );
      expect(screen.getByLabelText("Draft")).toBe(input);
      expect(input).toHaveValue("Unsaved name");
      expect(screen.getByRole("button", { name: "Retry" }).disabled).toBe(
        isFetching,
      );
    }
  });

  it("waits for both source reads and leaves new experiments independent of them", () => {
    const view = render(
      <ExperimentSourceGate detail={ready} configs={{}}>
        <input aria-label="Draft" />
      </ExperimentSourceGate>,
    );
    expect(screen.getByText("Loading experiment")).toBeInTheDocument();
    expect(screen.queryByLabelText("Draft")).not.toBeInTheDocument();
    view.rerender(
      <ExperimentSourceGate enabled={false} detail={{}} configs={{}}>
        <input aria-label="Draft" />
      </ExperimentSourceGate>,
    );
    expect(screen.getByLabelText("Draft")).toBeInTheDocument();
  });
});
