import React from "react";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ExecutionsList from "./ExecutionsList";

vi.mock("src/hooks/use-scroll-end", () => ({
  useScrollEnd: () => null,
}));

const makeExecution = (id) => ({
  id,
  status: "success",
  startedAt: "2026-10-01T10:00:00.000Z",
  completedAt: "2026-10-01T10:00:01.000Z",
});

const renderList = (props) =>
  render(
    <ExecutionsList
      executions={[]}
      selectedExecutionId={null}
      onExecutionChange={() => {}}
      isFetchingNextPage={false}
      fetchNextPage={() => {}}
      hasNextPage={false}
      {...props}
    />,
  );

describe("ExecutionsList selection", () => {
  it("automatically selects the newest execution when nothing is selected", () => {
    const onExecutionChange = vi.fn();

    renderList({
      executions: [makeExecution("newest"), makeExecution("older")],
      selectedExecutionId: null,
      onExecutionChange,
    });

    expect(onExecutionChange).toHaveBeenCalledTimes(1);
    expect(onExecutionChange).toHaveBeenCalledWith("newest");
  });

  it("preserves the selected older execution when a newer one arrives", () => {
    const onExecutionChange = vi.fn();
    const { rerender } = renderList({
      executions: [makeExecution("older"), makeExecution("oldest")],
      selectedExecutionId: "older",
      onExecutionChange,
    });

    rerender(
      <ExecutionsList
        executions={[
          makeExecution("newest"),
          makeExecution("older"),
          makeExecution("oldest"),
        ]}
        selectedExecutionId="older"
        onExecutionChange={onExecutionChange}
        isFetchingNextPage={false}
        fetchNextPage={() => {}}
        hasNextPage={false}
      />,
    );

    expect(onExecutionChange).not.toHaveBeenCalled();
  });
});
