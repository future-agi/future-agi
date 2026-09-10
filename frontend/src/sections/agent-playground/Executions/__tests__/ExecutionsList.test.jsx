import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import ExecutionsList from "../ExecutionsList";

vi.mock("src/hooks/use-scroll-end", () => ({
  useScrollEnd: vi.fn(() => ({ current: null })),
}));

const theme = createTheme();

const executions = [
  {
    id: "execution-newest",
    status: "success",
    startedAt: "2026-09-10T09:00:00.000Z",
  },
  {
    id: "execution-older",
    status: "failed",
    startedAt: "2026-09-10T08:00:00.000Z",
  },
];

const renderExecutionsList = (props) =>
  render(
    <ThemeProvider theme={theme}>
      <ExecutionsList
        executions={executions}
        selectedExecutionId={null}
        onExecutionChange={vi.fn()}
        isFetchingNextPage={false}
        fetchNextPage={vi.fn()}
        hasNextPage={false}
        {...props}
      />
    </ThemeProvider>,
  );

describe("ExecutionsList selection", () => {
  it("auto-selects the newest execution on first open", () => {
    const onExecutionChange = vi.fn();
    const { rerender } = renderExecutionsList({
      onExecutionChange,
    });

    expect(onExecutionChange).toHaveBeenCalledOnce();
    expect(onExecutionChange).toHaveBeenCalledWith("execution-newest");

    rerender(
      <ThemeProvider theme={theme}>
        <ExecutionsList
          executions={[
            {
              id: "execution-newer",
              status: "running",
              startedAt: "2026-09-10T10:00:00.000Z",
            },
            ...executions,
          ]}
          selectedExecutionId="execution-newest"
          onExecutionChange={onExecutionChange}
          isFetchingNextPage={false}
          fetchNextPage={vi.fn()}
          hasNextPage={false}
        />
      </ThemeProvider>,
    );

    expect(onExecutionChange).toHaveBeenCalledOnce();
  });

  it("does not replace a user-selected older execution when the list refreshes", () => {
    const onExecutionChange = vi.fn();
    const { rerender } = renderExecutionsList({
      selectedExecutionId: "execution-older",
      onExecutionChange,
    });

    expect(onExecutionChange).not.toHaveBeenCalled();

    rerender(
      <ThemeProvider theme={theme}>
        <ExecutionsList
          executions={[
            {
              id: "execution-newer",
              status: "running",
              startedAt: "2026-09-10T10:00:00.000Z",
            },
            ...executions,
          ]}
          selectedExecutionId="execution-older"
          onExecutionChange={onExecutionChange}
          isFetchingNextPage={false}
          fetchNextPage={vi.fn()}
          hasNextPage={false}
        />
      </ThemeProvider>,
    );

    expect(onExecutionChange).not.toHaveBeenCalled();
  });
});
