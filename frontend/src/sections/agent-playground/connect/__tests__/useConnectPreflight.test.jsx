import { describe, it, expect, vi, beforeEach } from "vitest";
import { useEffect } from "react";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { preflightHarnessJob } from "src/api/harness/harness";
import { prepareSourceForBuild } from "src/sections/simulate/environments/hooks/prepareSourceForBuild";
import useConnectPreflight from "../useConnectPreflight";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/pages/dashboard/harness/harnessShared", () => ({
  errorMessage: (e) => String(e),
}));
vi.mock("src/api/harness/harness", () => ({ preflightHarnessJob: vi.fn() }));
vi.mock("src/api/simulate-environments/preflightPayload", () => ({
  draftToPreflightPayload: () => ({ payload: {} }),
}));
vi.mock(
  "src/sections/simulate/environments/hooks/prepareSourceForBuild",
  () => ({ prepareSourceForBuild: vi.fn() }),
);

beforeEach(() => {
  preflightHarnessJob.mockReset();
  preflightHarnessJob.mockResolvedValue({ ready_to_submit: true });
  prepareSourceForBuild.mockReset();
  prepareSourceForBuild.mockResolvedValue({ draft: {}, credentialValues: {} });
});

const wrapper = ({ children }) => (
  <QueryClientProvider
    client={
      new QueryClient({ defaultOptions: { mutations: { retry: false } } })
    }
  >
    {children}
  </QueryClientProvider>
);
wrapper.propTypes = { children: PropTypes.node };

function useFlowCheck(checkedInputs) {
  const check = useConnectPreflight();
  const resetCheck = check.reset;
  useEffect(() => {
    resetCheck();
  }, [checkedInputs, resetCheck]);
  return check;
}

describe("useConnectPreflight inside a flow that resets on input changes", () => {
  it("sends the check once and is ready when it passes", async () => {
    const { result } = renderHook(({ inputs }) => useFlowCheck(inputs), {
      wrapper,
      initialProps: { inputs: "repo@main" },
    });

    await act(() => result.current.run({}));

    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(preflightHarnessJob).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("done");
  });

  it("clears a passed check when an input changes", async () => {
    const { result, rerender } = renderHook(
      ({ inputs }) => useFlowCheck(inputs),
      { wrapper, initialProps: { inputs: "repo@main" } },
    );
    await act(() => result.current.run({}));
    await waitFor(() => expect(result.current.ready).toBe(true));

    rerender({ inputs: "repo@dev" });

    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(result.current.ready).toBe(false);
  });
});
