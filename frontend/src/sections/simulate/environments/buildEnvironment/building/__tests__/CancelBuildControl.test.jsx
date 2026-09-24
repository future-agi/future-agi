import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const enqueueSnackbar = vi.fn();
vi.mock("notistack", () => ({ useSnackbar: () => ({ enqueueSnackbar }) }));

vi.mock("src/api/harness/harness", () => ({
  cancelHarnessJob: vi.fn(() => Promise.resolve({ status: { stage: "canceled" } })),
}));
const { cancelHarnessJob } = await import("src/api/harness/harness");
const { default: CancelBuildControl } = await import("../CancelBuildControl");

const renderControl = (props) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CancelBuildControl {...props} />
    </QueryClientProvider>,
  );

beforeEach(() => {
  cancelHarnessJob.mockClear();
  enqueueSnackbar.mockClear();
});

describe("CancelBuildControl", () => {
  it("renders nothing when not in the building state", () => {
    const { container } = renderControl({ envId: "env-1", building: false });
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing without an env id", () => {
    const { container } = renderControl({ envId: undefined, building: true });
    expect(container).toBeEmptyDOMElement();
  });

  it("confirms, then cancels the build through the mutation", async () => {
    renderControl({ envId: "env-1", building: true });

    // The bare button does not fire the cancel — a confirm gates it.
    fireEvent.click(screen.getByRole("button", { name: /Cancel build/ }));
    expect(cancelHarnessJob).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Stop build/ }));
    await waitFor(() => expect(cancelHarnessJob).toHaveBeenCalledWith("env-1", undefined));
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith("Build canceled", { variant: "success" }),
    );
  });
});
