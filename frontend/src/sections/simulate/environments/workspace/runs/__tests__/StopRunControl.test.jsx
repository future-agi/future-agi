import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
const { enqueueSnackbar } = await import("notistack");

// The shared cancel hook (POST test-executions/{id}/cancel/) — mocked so mutate
// resolves synchronously through the callbacks it is handed.
let cancelOutcome = "success";
const cancelMutate = vi.fn((id, opts) =>
  cancelOutcome === "success" ? opts?.onSuccess?.() : opts?.onError?.(new Error("nope")),
);
vi.mock("src/sections/common/simulation/hooks/useCancelExecution", () => ({
  useCancelExecution: () => ({ mutate: cancelMutate, isPending: false }),
}));

const { default: StopRunControl } = await import("../StopRunControl");

let client;
const renderControl = (props, wrap = (node) => node) => {
  client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>{wrap(<StopRunControl {...props} />)}</QueryClientProvider>,
  );
};

beforeEach(() => {
  cancelOutcome = "success";
  cancelMutate.mockClear();
  enqueueSnackbar.mockClear();
});

describe("StopRunControl", () => {
  it("renders nothing for a run that can't be stopped", () => {
    const { container } = renderControl({ executionId: "ex-1", stoppable: false });
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing without an execution id", () => {
    const { container } = renderControl({ executionId: undefined, stoppable: true });
    expect(container).toBeEmptyDOMElement();
  });

  it("confirms first, then cancels the execution and refreshes the run queries", () => {
    renderControl({ executionId: "ex-1", stoppable: true });
    const spy = vi.spyOn(client, "invalidateQueries");

    fireEvent.click(screen.getByRole("button", { name: "Stop simulation" }));
    expect(cancelMutate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Stop run" }));
    expect(cancelMutate).toHaveBeenCalledWith("ex-1", expect.any(Object));
    expect(spy).toHaveBeenCalledWith({ queryKey: ["run-test-executions"] });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["simulation-run-results-v3", "ex-1"] });
    expect(enqueueSnackbar).toHaveBeenCalledWith("Cancelling the run", { variant: "success" });
  });

  it("surfaces a failed stop", () => {
    cancelOutcome = "error";
    renderControl({ executionId: "ex-1", stoppable: true });
    fireEvent.click(screen.getByRole("button", { name: "Stop simulation" }));
    fireEvent.click(screen.getByRole("button", { name: "Stop run" }));
    expect(enqueueSnackbar).toHaveBeenCalledWith(expect.stringMatching(/couldn.t stop/i), { variant: "error" });
  });

  it("does not open the surrounding row when clicked", () => {
    const onRowClick = vi.fn();
    renderControl({ executionId: "ex-1", stoppable: true }, (node) => (
      <div onClick={onRowClick} role="presentation">{node}</div>
    ));
    fireEvent.click(screen.getByRole("button", { name: "Stop simulation" }));
    fireEvent.click(screen.getByRole("button", { name: "Stop run" }));
    expect(onRowClick).not.toHaveBeenCalled();
  });
});
