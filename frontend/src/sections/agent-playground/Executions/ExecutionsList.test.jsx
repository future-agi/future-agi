import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import ExecutionsList from "./ExecutionsList";

const renderList = (executions) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <ExecutionsList
        executions={executions}
        selectedExecutionId={null}
        onExecutionChange={vi.fn()}
        isFetchingNextPage={false}
        fetchNextPage={vi.fn()}
        hasNextPage={false}
      />
    </ThemeProvider>,
  );

describe("ExecutionsList", () => {
  it("renders a proper Skipped label without changing other status labels", () => {
    renderList([
      { id: "skipped", status: "skipped", startedAt: "2026-01-01T00:00:00Z" },
      { id: "success", status: "success", startedAt: "2026-01-01T00:00:00Z" },
      { id: "failed", status: "failed", startedAt: "2026-01-01T00:00:00Z" },
    ]);

    expect(screen.getByText("Skipped")).toBeInTheDocument();
    expect(screen.getByText("Success")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.queryByText("skipped")).not.toBeInTheDocument();
  });
});
