import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
});

const axios = (await import("src/utils/axios")).default;
const { FIX_RUN_FIXTURES, resetFixRunFixtures } = await import(
  "../_fixtures/fixRuns"
);
const { useEnvironmentFixRuns, useFixRun, fixPollInterval, FIX_POLL_MS } =
  await import("../fixRuns");

const makeWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

describe("fix-run reads on fixtures", () => {
  beforeEach(() => {
    axios.get.mockReset();
    resetFixRunFixtures();
  });

  it("lists the environment's fixes newest first from fixtures", async () => {
    const { result } = renderHook(() => useEnvironmentFixRuns("env-1"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    const created = result.current.fixes.map((f) => f.created_at);
    expect(created.length).toBe(FIX_RUN_FIXTURES.length);
    expect(created).toEqual([...created].sort().reverse());
    expect(result.current.count).toBe(result.current.fixes.length);
    expect(axios.get).not.toHaveBeenCalled();
  });

  it("stamps the asked environment on every fixture fix", async () => {
    const { result } = renderHook(() => useEnvironmentFixRuns("env-1"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(
      result.current.fixes.every((f) => f.environment_id === "env-1"),
    ).toBe(true);
  });

  it("reads one fix by id", async () => {
    const id = FIX_RUN_FIXTURES[0].id;
    const { result } = renderHook(() => useFixRun(id), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.fix).toBeDefined());
    expect(result.current.fix.id).toBe(id);
  });

  it.each(["queued", "running", "stopping"])("polls while %s", (state) => {
    expect(fixPollInterval({ state })).toBe(FIX_POLL_MS);
    expect(FIX_POLL_MS).toBe(5000);
  });

  it.each([
    "ready",
    "no_improvement",
    "not_reproduced",
    "out_of_budget",
    "stopped",
    "failed",
  ])("stops polling when %s", (state) => {
    expect(fixPollInterval({ state })).toBe(false);
  });

  it("covers every state the contract lists", () => {
    const states = new Set(FIX_RUN_FIXTURES.map((f) => f.state));
    [
      "queued",
      "running",
      "stopping",
      "ready",
      "no_improvement",
      "not_reproduced",
      "out_of_budget",
      "stopped",
      "failed",
    ].forEach((s) => expect(states.has(s)).toBe(true));
  });
});
