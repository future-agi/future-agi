import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock the shared cancel call so the hook is tested without a real request.
vi.mock("src/api/harness/harness", () => ({
  cancelHarnessJob: vi.fn(() => Promise.resolve({ status: { stage: "canceled" } })),
}));
const { cancelHarnessJob } = await import("src/api/harness/harness");
const { useCancelHarnessJob } = await import("../cancelBuild");

let client;
function Wrapper({ children }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
Wrapper.propTypes = { children: PropTypes.node };

beforeEach(() => cancelHarnessJob.mockClear());

describe("useCancelHarnessJob", () => {
  it("POSTs the cancel with the reason and invalidates the build poll on success", async () => {
    client = new QueryClient();
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useCancelHarnessJob("env-1"), { wrapper: Wrapper });

    result.current.mutate("over budget");

    await waitFor(() =>
      expect(cancelHarnessJob).toHaveBeenCalledWith("env-1", "over budget"),
    );
    // The build view polls ["harness-job", id]; invalidating it flips it to Failed.
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith({ queryKey: ["harness-job", "env-1"] }),
    );
    expect(spy).toHaveBeenCalledWith({ queryKey: ["harness-environment", "env-1"] });
  });
});
