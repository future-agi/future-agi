import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import ExecutionsList, { STATUS_LABELS } from "../ExecutionsList";
import { EXECUTION_STATUS } from "../../utils/workflowExecution";

vi.mock("src/hooks/use-scroll-end", () => ({
  useScrollEnd: () => ({ current: null }),
}));

const theme = createTheme();

const renderList = (executions) =>
  render(
    <ThemeProvider theme={theme}>
      <ExecutionsList
        executions={executions}
        selectedExecutionId={executions[0]?.id}
        onExecutionChange={vi.fn()}
      />
    </ThemeProvider>,
  );

describe("ExecutionsList status labels", () => {
  it("writes skipped the same way as the other outcomes", () => {
    expect(STATUS_LABELS[EXECUTION_STATUS.SKIPPED]).toBe("Skipped");
    expect(STATUS_LABELS[EXECUTION_STATUS.SUCCESS]).toBe("Success");
    expect(STATUS_LABELS[EXECUTION_STATUS.FAILED]).toBe("Failed");
    expect(STATUS_LABELS[EXECUTION_STATUS.RUNNING]).toBe("Running");
  });

  it("renders Skipped in the history list instead of raw lowercase status", () => {
    renderList([
      {
        id: "ex-1",
        status: "skipped",
        startedAt: "2026-09-03T10:00:00.000Z",
      },
    ]);
    expect(screen.getByText("Skipped")).toBeInTheDocument();
    expect(screen.queryByText("skipped")).not.toBeInTheDocument();
  });

  it("still renders Success for a successful run", () => {
    renderList([
      {
        id: "ex-2",
        status: "success",
        startedAt: "2026-09-03T10:00:00.000Z",
      },
    ]);
    expect(screen.getByText("Success")).toBeInTheDocument();
  });
});
