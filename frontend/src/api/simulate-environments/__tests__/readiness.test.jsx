import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

const { default: axios } = await import("src/utils/axios");
const {
  getReadiness,
  getReadinessCases,
  useReadiness,
  useReadinessCases,
  useRereadWhenDefaultsMove,
} = await import("../readiness");

const PATH = "/simulate/api/harness-environments/env-1/capabilities/";

let client;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
wrapper.propTypes = { children: PropTypes.node };

const report = (state, rows = []) => ({ validation: { state }, rows });

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  axios.get.mockReset().mockResolvedValue({ data: { items: [] } });
  axios.post.mockReset().mockResolvedValue({ data: {} });
});

afterEach(() => vi.useRealTimers());

describe("readiness requests", () => {
  it("sends the trials with the whole suite's GET and a named selection's POST", async () => {
    await getReadiness("env-1", { agent: "a2" }, null, 3);
    expect(axios.get).toHaveBeenCalledWith(PATH, {
      params: { agent_version_id: "a2", trials: 3 },
    });
    await getReadiness("env-1", { agent: "a2" }, ["k1"], 3);
    expect(axios.post).toHaveBeenCalledWith(PATH, {
      agent_version_id: "a2",
      scenario_ids: ["k1"],
      trials: 3,
    });
  });

  it("asks for the cases one row holds back, keys repeated", async () => {
    await getReadinessCases(
      "env-1",
      {},
      { keys: ["k1", "k2"], row: "tool_routing", trials: 2 },
    );
    expect(axios.get).toHaveBeenCalledWith(`${PATH}cases/`, {
      params: { trials: 2, keys: ["k1", "k2"], row: "tool_routing" },
      paramsSerializer: { indexes: null },
    });
  });
});

describe("reading readiness again", () => {
  it.each([
    ["the pair's check", report("validating")],
    [
      "the agent's start check",
      report("not_run", [{ id: "start", reason_code: "preflight_pending" }]),
    ],
  ])("polls while %s runs, then stops", async (_, checking) => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    axios.get
      .mockResolvedValueOnce({ data: checking })
      .mockResolvedValue({ data: report("ready") });

    renderHook(() => useReadiness("env-1", {}), { wrapper });
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    await act(() => vi.advanceTimersByTimeAsync(5000));
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
    await act(() => vi.advanceTimersByTimeAsync(30000));
    expect(axios.get).toHaveBeenCalledTimes(2);
  });

  it("asks for the names again when the report they belong to changes", async () => {
    const { rerender } = renderHook(
      ({ report }) => useReadinessCases("env-1", { row: "reset", report }),
      { wrapper, initialProps: { report: "t1" } },
    );
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    rerender({ report: "t2" });
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
  });

  it("reads again when a default moves, not when the defaults first load", async () => {
    const { rerender } = renderHook(
      ({ agent }) => {
        useReadiness("env-1", {});
        useRereadWhenDefaultsMove("env-1", agent && { agent: { id: agent } });
      },
      { wrapper, initialProps: { agent: null } },
    );
    rerender({ agent: "a1" });
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    rerender({ agent: "a2" });
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
  });
});
