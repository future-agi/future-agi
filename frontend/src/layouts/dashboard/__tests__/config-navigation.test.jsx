import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import axios from "src/utils/axios";

import { useNavData } from "../config-navigation";

const authState = { user: null };

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => authState,
}));

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));

vi.mock("src/utils/axios", () => ({ default: { get: vi.fn() } }));

let queryClient;
afterEach(() => {
  cleanup();
  queryClient?.clear();
});

function renderNavigation(capabilities = { metrics_enabled: true }) {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retryDelay: 0 } },
  });
  if (capabilities !== null) {
    queryClient.setQueryData(CAPABILITIES_QUERY_KEY, { data: capabilities });
  }
  return renderHook(() => useNavData(), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    ),
  });
}

function simulateTitles() {
  const { result } = renderNavigation();
  const section = result.current.find((s) => s.subheader === "Simulate");
  return section.items.map((item) => item.title);
}

describe("Simulate menu", () => {
  beforeEach(() => {
    authState.user = { organization_role: "Owner" };
    axios.get.mockReset();
  });

  it("keeps Evals in Build and places Metrics directly above Dashboards in Observe", () => {
    const { result } = renderNavigation();
    expect(result.current.find((s) => s.subheader === "BUILD").items).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          title: "Evals",
          path: "/dashboard/evaluations",
        }),
      ]),
    );
    const observe = result.current.find((s) => s.subheader === "OBSERVE");
    expect(
      observe.items.some((item) => /derived.metrics/i.test(item.title)),
    ).toBe(false);
    expect(observe.items.map((item) => item.title)).toEqual([
      "Tracing",
      "Error Feed",
      "Tasks",
      "Users",
      "Annotations",
      "Alerts",
      "Metrics",
      "Dashboards",
    ]);
    const metrics = observe.items.find((item) => item.title === "Metrics");
    expect(metrics.path).toBe("/dashboard/metrics");
    expect(metrics.icon.props.icon).toBe("mdi:chart-line");
    expect(
      result.current
        .flatMap((section) => section.items)
        .filter((item) => item.title === "Metrics"),
    ).toHaveLength(1);
  });

  it.each([
    ["OFF", { metrics_enabled: false }],
    ["missing", {}],
  ])("omits Metrics when availability is %s", (_label, capabilities) => {
    const { result } = renderNavigation(capabilities);
    const observe = result.current.find((s) => s.subheader === "OBSERVE");
    expect(observe.items.map((item) => item.title)).toEqual([
      "Tracing",
      "Error Feed",
      "Tasks",
      "Users",
      "Annotations",
      "Alerts",
      "Dashboards",
    ]);
    expect(
      result.current
        .flatMap((section) => section.items)
        .some((item) => item.path === "/dashboard/metrics"),
    ).toBe(false);
  });

  it("omits Metrics while the capability request is loading", async () => {
    let resolveCapabilities;
    axios.get.mockReturnValue(
      new Promise((resolve) => {
        resolveCapabilities = resolve;
      }),
    );
    const { result } = renderNavigation(null);
    const titles = () =>
      result.current
        .find((s) => s.subheader === "OBSERVE")
        .items.map((item) => item.title);
    expect(queryClient.getQueryState(CAPABILITIES_QUERY_KEY).status).toBe(
      "pending",
    );
    expect(titles()).not.toContain("Metrics");
    await act(async () => {
      resolveCapabilities({ data: { metrics_enabled: true } });
    });
    await waitFor(() =>
      expect(titles().slice(-2)).toEqual(["Metrics", "Dashboards"]),
    );
  });

  it("omits Metrics on a capability error even with cached ON data", async () => {
    axios.get.mockRejectedValue(new Error("Capabilities unavailable"));
    const { result } = renderNavigation();
    const titles = () =>
      result.current
        .find((s) => s.subheader === "OBSERVE")
        .items.map((item) => item.title);
    expect(titles()).toContain("Metrics");
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: CAPABILITIES_QUERY_KEY });
    });
    expect(queryClient.getQueryState(CAPABILITIES_QUERY_KEY).status).toBe(
      "error",
    );
    expect(queryClient.getQueryData(CAPABILITIES_QUERY_KEY)).toEqual({
      data: { metrics_enabled: true },
    });
    await waitFor(() => expect(titles()).not.toContain("Metrics"));
  });

  it("updates navigation when the shared capability cache changes", async () => {
    const { result } = renderNavigation({ metrics_enabled: false });
    const titles = () =>
      result.current
        .find((s) => s.subheader === "OBSERVE")
        .items.map((item) => item.title);
    expect(titles()).not.toContain("Metrics");
    act(() => {
      queryClient.setQueryData(CAPABILITIES_QUERY_KEY, {
        data: { metrics_enabled: true },
      });
    });
    await waitFor(() =>
      expect(titles().slice(-2)).toEqual(["Metrics", "Dashboards"]),
    );
    act(() => {
      queryClient.setQueryData(CAPABILITIES_QUERY_KEY, {
        data: { metrics_enabled: false },
      });
    });
    await waitFor(() => expect(titles()).not.toContain("Metrics"));
  });

  it("shows only Environment to an org restricted to Environments", () => {
    authState.user.restrict_simulate_to_environments = true;
    expect(simulateTitles()).toEqual(["Environment"]);
  });

  it.each([false, undefined])(
    "shows every Simulate page when the flag is %s",
    (flag) => {
      authState.user.restrict_simulate_to_environments = flag;
      expect(simulateTitles()).toEqual([
        "Environment",
        "Scenarios",
        "Run Simulation",
      ]);
    },
  );
});
