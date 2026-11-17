import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock only the axios default instance; the endpoints stay real.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn() } };
});

const axios = (await import("src/utils/axios")).default;
const { useRunDetail } = await import("../runDetail");

const wrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

const execution = (over = {}) => ({
  id: "ex-failed",
  ordinal: 5,
  agent_version: "v2",
  agent_type: "VOICE",
  status: "failed",
  started_at: "2026-09-10T09:00:00.000Z",
  completed_at: "2026-09-10T09:01:00.000Z",
  selected_scenario_keys: [],
  trials: 1,
  resume: { resumable: false, finished: null, unfinished: null, history: [] },
  summary: {
    total: 0,
    measured: 0,
    pass_rate: 0,
    outcomes: { passed: 0, failed: 0, error: 0, inconclusive: 0 },
  },
  fix_run: null,
  ...over,
});

const identityFor = async (payload) => {
  axios.get.mockResolvedValue({ data: { execution: payload } });
  const { result } = renderHook(() => useRunDetail("rt1", "ex-failed"), {
    wrapper: wrapper(),
  });
  await waitFor(() => expect(result.current.identity).not.toBeNull());
  return result.current.identity;
};

describe("useRunDetail on a failed run", () => {
  beforeEach(() => {
    axios.get.mockReset();
  });

  it("carries the run's typed failure reason", async () => {
    const identity = await identityFor(
      execution({
        failure: { domain: "environment", code: "github_private_unavailable" },
      }),
    );

    expect(identity.status).toBe("failed");
    expect(identity.failure).toEqual({
      domain: "environment",
      code: "github_private_unavailable",
    });
  });

  it("carries no reason from a server that sends none", async () => {
    const identity = await identityFor(execution());

    expect(identity.status).toBe("failed");
    expect(identity.failure ?? null).toBeNull();
  });
});
