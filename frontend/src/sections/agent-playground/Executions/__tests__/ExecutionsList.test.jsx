import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import ExecutionsList from "../ExecutionsList";

vi.mock("src/hooks/use-scroll-end", () => ({
  useScrollEnd: () => vi.fn(),
}));

const execution = (id, startedAt) => ({
  id,
  startedAt,
  status: "success",
});

const baseProps = {
  isFetchingNextPage: false,
  fetchNextPage: vi.fn(),
  hasNextPage: false,
};

describe("ExecutionsList", () => {
  it("selects the newest execution when there is no current selection", () => {
    const onExecutionChange = vi.fn();

    render(
      <ExecutionsList
        {...baseProps}
        executions={[execution("newest", "2026-09-04T10:00:00Z")]}
        selectedExecutionId={null}
        onExecutionChange={onExecutionChange}
      />,
    );

    expect(onExecutionChange).toHaveBeenCalledExactlyOnceWith("newest");
  });

  it("keeps a user-selected execution when a newer execution refreshes the list", () => {
    const onExecutionChange = vi.fn();
    const older = execution("older", "2026-09-03T10:00:00Z");
    const selected = execution("selected", "2026-09-03T11:00:00Z");
    const { rerender } = render(
      <ExecutionsList
        {...baseProps}
        executions={[selected, older]}
        selectedExecutionId="selected"
        onExecutionChange={onExecutionChange}
      />,
    );

    fireEvent.click(screen.getByText("Sep 03, 2026, 3:00 PM"));
    expect(onExecutionChange).toHaveBeenCalledExactlyOnceWith("older");

    rerender(
      <ExecutionsList
        {...baseProps}
        executions={[
          execution("newest", "2026-09-04T10:00:00Z"),
          selected,
          older,
        ]}
        selectedExecutionId="older"
        onExecutionChange={onExecutionChange}
      />,
    );

    expect(onExecutionChange).toHaveBeenCalledExactlyOnceWith("older");
  });
});
