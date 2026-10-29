import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import {
  buildConnectedAgentsParams,
  connectedAgentsQueryKey,
  mapConnectedAgent,
  useConnectedAgents,
} from "../connectedAgents";
import { connectedAgentsPage } from "../_fixtures/connectedAgents";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    agentDefinitions: { list: "/simulate/agent-definitions/" },
  },
}));

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

const load = async (params) => {
  const { result } = renderHook(() => useConnectedAgents(params), {
    wrapper: makeWrapper(),
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  return result.current.data;
};

beforeEach(() => {
  axios.get.mockReset();
  axios.get.mockImplementation((_, { params }) =>
    Promise.resolve({ data: connectedAgentsPage(params) }),
  );
});

describe("buildConnectedAgentsParams", () => {
  it("sends a 1-based page and the page size as limit", () => {
    expect(
      buildConnectedAgentsParams({ page: 0, pageSize: 25, search: "" }),
    ).toEqual({ page: 1, limit: 25 });
  });

  it("sends a trimmed search only when there is one", () => {
    expect(
      buildConnectedAgentsParams({ page: 2, pageSize: 50, search: "  uber " }),
    ).toEqual({ page: 3, limit: 50, search: "uber" });
    expect(
      buildConnectedAgentsParams({ page: 0, pageSize: 25, search: "   " }),
    ).toEqual({ page: 1, limit: 25 });
  });
});

describe("useConnectedAgents", () => {
  it("calls the agent definitions list with exactly the contract's params", async () => {
    await load({ page: 1, pageSize: 25, search: "voice" });
    expect(axios.get).toHaveBeenCalledWith("/simulate/agent-definitions/", {
      params: { page: 2, limit: 25, search: "voice" },
    });
  });

  it("returns mapped rows and the server's total", async () => {
    const data = await load({ page: 0, pageSize: 25, search: "" });
    expect(data.items).toHaveLength(25);
    expect(data.total).toBe(37);
    expect(data.items[0]).toMatchObject({
      id: "ca-01",
      name: "Customer support line",
    });
  });

  it("keys the query by page, size and search", () => {
    expect(
      connectedAgentsQueryKey({ page: 2, pageSize: 50, search: "a" }),
    ).toEqual(["agents", "connected", { page: 2, pageSize: 50, search: "a" }]);
  });
});

describe("mapConnectedAgent", () => {
  it("maps a code agent row from the contract", () => {
    expect(
      mapConnectedAgent({
        id: "a48511b2",
        agent_name: "Uber Voice Agent",
        agent_type: "voice",
        inbound: true,
        description: "# ROLE\nYou are the Uber phone-booking assistant.",
        created_at: "2026-10-14T11:50:22.093543Z",
        latest_version: 1,
        latest_version_id: "93956ae6",
        agent_kind: "source",
        source: "github",
        version_count: 1,
        environment_count: 1,
        environments: [{ id: "e5391b8a", name: "ride-voice-agent" }],
      }),
    ).toEqual({
      id: "a48511b2",
      name: "Uber Voice Agent",
      description: "# ROLE\nYou are the Uber phone-booking assistant.",
      modality: "voice",
      direction: "inbound",
      source: "github",
      latestLabel: "v1",
      versionCount: 1,
      environments: [{ id: "e5391b8a", name: "ride-voice-agent" }],
      environmentCount: 1,
      createdAt: "2026-10-14T11:50:22.093543Z",
    });
  });

  it("gives a voice agent its direction from inbound", () => {
    expect(
      mapConnectedAgent({ agent_type: "voice", inbound: false }).direction,
    ).toBe("outbound");
  });

  it("gives a text agent no direction", () => {
    expect(
      mapConnectedAgent({ agent_type: "text", inbound: true }).direction,
    ).toBeNull();
  });

  it("has no version label when the agent has no versions", () => {
    const a = mapConnectedAgent({ latest_version: null, version_count: 0 });
    expect(a.latestLabel).toBeNull();
    expect(a.versionCount).toBe(0);
  });

  it("counts environments from environment_count, not the capped list", () => {
    const a = mapConnectedAgent({
      environment_count: 7,
      environments: [{ id: "e1", name: "A" }],
    });
    expect(a.environmentCount).toBe(7);
  });
});
