import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

const { default: axios } = await import("src/utils/axios");
const { useReadiness, useReadinessCases, getReadinessCases } = await import(
  "../readiness"
);

let client;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
wrapper.propTypes = { children: PropTypes.node };

const report = (state) => ({ validation: { state }, rows: [], areas: [] });

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  axios.get.mockReset();
  axios.post.mockReset();
});

afterEach(() => vi.useRealTimers());

describe("useReadiness", () => {
  it("asks for the picked versions: the whole suite by GET, a selection by POST", async () => {
    axios.get.mockResolvedValue({ data: report("ready") });
    axios.post.mockResolvedValue({ data: report("ready") });
    const picks = { agent: "a2", suite: "s1" };

    renderHook(() => useReadiness("env-1", picks), { wrapper });
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    expect(axios.get.mock.calls[0][0]).toContain(
      "/harness-environments/env-1/capabilities/",
    );
    expect(axios.get.mock.calls[0][1].params).toEqual({
      agent_version_id: "a2",
      suite_version_id: "s1",
    });

    renderHook(
      () => useReadiness("env-1", picks, { keys: ["k1", "k2"], trials: 3 }),
      { wrapper },
    );
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(axios.post.mock.calls[0][1]).toEqual({
      scenario_ids: ["k1", "k2"],
      trials: 3,
      agent_version_id: "a2",
      suite_version_id: "s1",
    });
  });

  it("asks again every 15 seconds while the pair is being checked, then stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    axios.get
      .mockResolvedValueOnce({ data: report("validating") })
      .mockResolvedValue({ data: report("ready") });

    const { result } = renderHook(() => useReadiness("env-1", {}), {
      wrapper,
    });
    await waitFor(() =>
      expect(result.current.data?.validation.state).toBe("validating"),
    );

    await act(() => vi.advanceTimersByTimeAsync(15000));
    await waitFor(() =>
      expect(result.current.data?.validation.state).toBe("ready"),
    );
    await act(() => vi.advanceTimersByTimeAsync(30000));
    expect(axios.get).toHaveBeenCalledTimes(2);
  });

  it("asks for one page of a bucket's scenarios by name", async () => {
    axios.get.mockResolvedValue({
      data: { items: [{ key: "a", name: "Refund over limit" }], next: "50" },
    });

    const items = await getReadinessCases("env-1", { agent: "a2" }, "stale", [
      "a",
      "b",
    ]);

    expect(items.map((item) => item.name)).toEqual(["Refund over limit"]);
    expect(axios.get).toHaveBeenCalledTimes(1);
    expect(axios.get.mock.calls[0][1].params).toEqual({
      agent_version_id: "a2",
      bucket: "stale",
      keys: ["a", "b"],
    });

    await getReadinessCases("env-1", {}, null, null, "tool_routing");
    expect(axios.get.mock.calls[1][1].params).toEqual({ row: "tool_routing" });
  });

  it("keeps asking while the agent's start check is still running", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const pending = {
      ...report("not_run"),
      rows: [{ id: "start", reason_code: "preflight_pending" }],
    };
    axios.get
      .mockResolvedValueOnce({ data: pending })
      .mockResolvedValue({ data: report("not_run") });

    renderHook(() => useReadiness("env-1", {}), { wrapper });
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    await act(() => vi.advanceTimersByTimeAsync(15000));
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
    await act(() => vi.advanceTimersByTimeAsync(30000));
    expect(axios.get).toHaveBeenCalledTimes(2);
  });

  it("asks for the names again when the report they belong to changes", async () => {
    axios.get.mockResolvedValue({ data: { items: [], next: null } });
    const { rerender } = renderHook(
      ({ report }) => useReadinessCases("env-1", { bucket: "stale", report }),
      { wrapper, initialProps: { report: "t1" } },
    );
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    rerender({ report: "t2" });
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
  });
});
