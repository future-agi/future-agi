import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import {
  mapAgentDetail,
  mapVersionDetail,
  passRateOf,
  toRun,
  toVersion,
  useAgentDetail,
  useAgentEnvironments,
  useAgentRuns,
  useAgentVersion,
  useSetObservability,
  useStoredAgent,
} from "../agentDetail";
import {
  AGENT_ENVIRONMENTS,
  CODE_AGENT_DETAIL,
  CODE_VERSION_DETAIL,
  ENVIRONMENT_RUN,
  HOSTED_AGENT_DETAIL,
  HOSTED_VERSION_DETAIL,
  SIMULATE_RUN,
  STORED_AGENT,
} from "../_fixtures/agentDetail";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), put: vi.fn() },
  endpoints: {
    agentDefinitions: {
      detail: (id) => `/simulate/agent-definitions/${id}/`,
      versionDetail: (id, v) =>
        `/simulate/agent-definitions/${id}/versions/${v}/`,
      edit: (id) => `/simulate/agent-definitions/${id}/edit/`,
      runs: (id) => `/simulate/agent-definitions/${id}/runs/`,
      storedAgent: (id, v) =>
        `/simulate/agent-definitions/${id}/versions/${v}/agent/`,
    },
  },
}));

vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  listHarnessEnvironments: vi.fn(),
}));

const { listHarnessEnvironments } = await import(
  "src/api/simulate-environments/harnessEnvironments"
);

const makeWrapper = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return { client, Wrapper };
};

const run = async (hook) => {
  const { Wrapper } = makeWrapper();
  const { result } = renderHook(hook, { wrapper: Wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  return result.current.data;
};

beforeEach(() => {
  axios.get.mockReset();
  axios.put.mockReset();
  listHarnessEnvironments.mockReset();
});

describe("passRateOf", () => {
  it("is passed over everything measured, as a whole percent", () => {
    expect(passRateOf({ passed: 3, failed: 7, skipped: 0 })).toBe(30);
    expect(passRateOf({ passed: 2, failed: 0, skipped: 1 })).toBe(67);
  });

  it("is null when nothing was measured or outcomes are null", () => {
    expect(passRateOf({ passed: 0, failed: 0, skipped: 0 })).toBeNull();
    expect(
      passRateOf({ passed: null, failed: null, skipped: null }),
    ).toBeNull();
  });
});

describe("toRun", () => {
  it("maps an environment run", () => {
    expect(toRun(ENVIRONMENT_RUN)).toEqual({
      kind: "environment",
      testExecutionId: "68dedb99-42b6-46e7-8e94-b511041fa2cb",
      runTestId: "b741b14e-91c0-403d-81e5-5dd7e40d09c7",
      runTestName: "Uber Voice Agent",
      runNumber: 2,
      status: "completed",
      environment: {
        id: "e5391b8a-156e-46dd-b5a4-4761971f2ccd",
        name: "ride-voice-agent",
      },
      agentVersion: { id: "93956ae6", number: 3 },
      passed: 3,
      failed: 7,
      skipped: 0,
      passRate: 30,
      startedAt: "2026-10-14T11:58:53.368130+00:00",
      completedAt: "2026-10-14T12:27:36.569945+00:00",
    });
  });

  it("maps an old Simulate run with no outcomes", () => {
    const r = toRun(SIMULATE_RUN);
    expect(r.environment).toBeNull();
    expect(r.agentVersion).toBeNull();
    expect(r.passRate).toBeNull();
    expect(r.completedAt).toBeNull();
  });
});

describe("toVersion", () => {
  it("maps a versions-list row to what a timeline row shows", () => {
    const raw = HOSTED_AGENT_DETAIL.versions.find((v) => v.id === "v2-id");
    expect(toVersion(raw)).toEqual({
      id: "v2-id",
      number: 2,
      label: "v2",
      isActive: true,
      isLatest: true,
      commitMessage: "Shorter greeting",
      createdAt: "2026-10-15T09:00:00Z",
    });
  });
});

describe("mapAgentDetail", () => {
  it("knows the active version", () => {
    expect(mapAgentDetail(HOSTED_AGENT_DETAIL).activeVersionId).toBe("v2-id");
  });

  it("maps the header fields", () => {
    expect(mapAgentDetail(CODE_AGENT_DETAIL)).toMatchObject({
      id: CODE_AGENT_DETAIL.id,
      name: "Uber Voice Agent",
      agentKind: "source",
      source: "github",
      assistantId: null,
      environmentCount: 1,
      observabilityEnabled: false,
      createdAt: "2026-10-14T11:50:22.093543Z",
    });
  });

  it("copes with an agent that has no versions", () => {
    const a = mapAgentDetail({
      ...HOSTED_AGENT_DETAIL,
      versions: [],
      active_version: null,
      version_count: 0,
    });
    expect(a.activeVersionId).toBeNull();
    expect(a.versionCount).toBe(0);
  });
});

describe("mapVersionDetail", () => {
  it("maps a code agent's version without the stored Agent", () => {
    const v = mapVersionDetail(CODE_VERSION_DETAIL);
    expect(v.label).toBe("v1");
    expect(v.agentKind).toBe("source");
    expect(v).not.toHaveProperty("agentDetails");
  });

  it("carries what a timeline row shows", () => {
    expect(mapVersionDetail(HOSTED_VERSION_DETAIL)).toMatchObject({
      number: 2,
      isActive: true,
      isLatest: true,
      commitMessage: null,
      createdAt: "2026-10-15T09:00:00Z",
    });
  });

  it("keeps a hosted version's snapshot and masked key", () => {
    const v = mapVersionDetail(HOSTED_VERSION_DETAIL);
    expect(v.apiKey).toBe("sk-a...9f2c");
    expect(v.config.provider).toBe("vapi");
  });
});

describe("hooks", () => {
  it("useAgentDetail reads the agent detail", async () => {
    axios.get.mockResolvedValue({ data: HOSTED_AGENT_DETAIL });
    const a = await run(() => useAgentDetail("bf8f"));
    expect(axios.get).toHaveBeenCalledWith("/simulate/agent-definitions/bf8f/");
    expect(a.name).toBe("Northwind Pharmacy");
  });

  it("useAgentDetail unwraps a { result } envelope", async () => {
    axios.get.mockResolvedValue({ data: { result: HOSTED_AGENT_DETAIL } });
    const a = await run(() => useAgentDetail("bf8f"));
    expect(a.name).toBe("Northwind Pharmacy");
  });

  it("useAgentRuns reads the agent's recent runs with the limit", async () => {
    axios.get.mockResolvedValue({
      data: { results: [ENVIRONMENT_RUN, SIMULATE_RUN] },
    });
    const runs = await run(() => useAgentRuns("a1", { limit: 15 }));
    expect(axios.get).toHaveBeenCalledWith(
      "/simulate/agent-definitions/a1/runs/",
      { params: { limit: 15 } },
    );
    expect(runs).toEqual([toRun(ENVIRONMENT_RUN), toRun(SIMULATE_RUN)]);
  });

  it("useAgentVersion reads one version", async () => {
    axios.get.mockResolvedValue({ data: HOSTED_VERSION_DETAIL });
    const v = await run(() => useAgentVersion("a1", "v2-id"));
    expect(axios.get).toHaveBeenCalledWith(
      "/simulate/agent-definitions/a1/versions/v2-id/",
    );
    expect(v.apiKey).toBe("sk-a...9f2c");
  });

  it("useAgentEnvironments lists only this agent's environments", async () => {
    listHarnessEnvironments.mockResolvedValue(AGENT_ENVIRONMENTS);
    const envs = await run(() => useAgentEnvironments("a1"));
    expect(listHarnessEnvironments).toHaveBeenCalledWith({
      agentDefinitionId: "a1",
      page: 1,
      limit: 100,
    });
    expect(envs).toEqual([
      {
        id: "e5391b8a",
        name: "ride-voice-agent",
        status: "ready",
        lastRun: toRun(ENVIRONMENT_RUN),
      },
      {
        id: "e9999999",
        name: "Refund edge cases",
        status: "failed",
        lastRun: null,
      },
    ]);
  });

  it("useStoredAgent reads the version's stored Agent", async () => {
    axios.get.mockResolvedValue({ data: STORED_AGENT });
    const s = await run(() => useStoredAgent("a1", "v1", { enabled: true }));
    expect(axios.get).toHaveBeenCalledWith(
      "/simulate/agent-definitions/a1/versions/v1/agent/",
    );
    expect(s.setupStale).toBe(false);
    expect(s.agent.name).toBe("uber_voice_agent");
    expect(s.sdkVersion).toBe("0.2.3");
    expect(s.storedAt).toBe("2026-10-14T11:50:22.212003Z");
  });

  it("useStoredAgent reads a 404 as nothing stored for the version", async () => {
    axios.get.mockRejectedValue({
      statusCode: 404,
      code: "not_found",
      message: "Agent not stored for this version",
    });
    expect(await run(() => useStoredAgent("a1", "v1", { enabled: true }))).toBe(
      null,
    );
  });

  it("useStoredAgent reports any other failure", async () => {
    axios.get.mockRejectedValue({ statusCode: 500, message: "boom" });
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useStoredAgent("a1", "v1", { enabled: true }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error.message).toBe("boom");
  });

  it("useStoredAgent doesn't call when disabled", async () => {
    const { Wrapper } = makeWrapper();
    renderHook(() => useStoredAgent("a1", "v1", { enabled: false }), {
      wrapper: Wrapper,
    });
    await new Promise((r) => setTimeout(r, 20));
    expect(axios.get).not.toHaveBeenCalled();
  });

  it("useSetObservability sends only the switch and refetches the agent", async () => {
    axios.put.mockResolvedValue({ data: { message: "ok" } });
    const { client, Wrapper } = makeWrapper();
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useSetObservability("a1"), {
      wrapper: Wrapper,
    });
    await act(() => result.current.mutateAsync({ enabled: true }));
    expect(axios.put).toHaveBeenCalledWith(
      "/simulate/agent-definitions/a1/edit/",
      {
        observability_enabled: true,
      },
    );
    expect(spy).toHaveBeenCalledWith({ queryKey: ["agents", "detail", "a1"] });
  });
});
