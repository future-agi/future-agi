import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";

import Scenarios from "../Scenarios";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: { scenarios: { list: "/simulate/scenarios/" } },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
vi.mock("src/components/scenarios/DeleteScenarioDialog", () => ({
  default: () => null,
}));
vi.mock("src/components/scenarios/EditScenarioDialog", () => ({
  default: () => null,
}));
vi.mock("src/components/scenarios/ScenariosActionMenu", () => ({
  default: () => null,
}));
vi.mock("../SimulationScenarioEmptyScreen", () => ({
  default: () => <div>Create your first scenario</div>,
}));

const scenario = (id, agentType) => ({
  id,
  name: `Scenario ${id}`,
  agent_type: agentType,
  dataset_rows: 3,
  status: "completed",
  scenario_type: "dataset",
  created_at: "2026-11-01T00:00:00Z",
});

const listResponse = (results, count = results.length) => ({
  data: { count, results },
});

const lastParams = () => mocks.get.mock.calls.at(-1)[1].params;

const renderScenarios = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Scenarios />
    </QueryClientProvider>,
  );

const typeFilter = () => screen.getByRole("combobox", { name: "Agent type" });

const pickType = (label) => {
  fireEvent.mouseDown(typeFilter());
  fireEvent.click(screen.getByRole("option", { name: label }));
};

describe("Scenarios agent type filter", () => {
  beforeEach(() => vi.clearAllMocks());

  it("starts on All types and sends no agent_type", async () => {
    mocks.get.mockResolvedValue(
      listResponse([scenario("a", "voice"), scenario("b", "text")]),
    );
    renderScenarios();

    expect(await screen.findByText("Scenario a")).toBeInTheDocument();
    expect(typeFilter()).toHaveTextContent("All types");
    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(mocks.get).toHaveBeenCalledWith("/simulate/scenarios/", {
      params: { page: 1, limit: 25, search: "" },
    });
  });

  it.each([
    ["Voice", "voice"],
    ["Chat", "text"],
  ])(
    "picking %s requests agent_type=%s from page 1",
    async (label, value) => {
      mocks.get.mockResolvedValue(listResponse([scenario("a", "voice")], 60));
      renderScenarios();
      await screen.findByText("Scenario a");

      fireEvent.click(screen.getByRole("button", { name: "Next page" }));
      await waitFor(() => expect(lastParams().page).toBe(2));

      pickType(label);

      await waitFor(() =>
        expect(lastParams()).toEqual({
          page: 1,
          limit: 25,
          search: "",
          agent_type: value,
        }),
      );
    },
  );

  it("sends the type together with the search text", async () => {
    mocks.get.mockResolvedValue(listResponse([scenario("a", "voice")]));
    renderScenarios();
    await screen.findByText("Scenario a");

    pickType("Voice");
    fireEvent.change(screen.getByPlaceholderText("Search"), {
      target: { value: "refund" },
    });

    await waitFor(
      () =>
        expect(lastParams()).toEqual({
          page: 1,
          limit: 25,
          search: "refund",
          agent_type: "voice",
        }),
      { timeout: 2000 },
    );
  });

  it("drops agent_type again on All types", async () => {
    mocks.get.mockResolvedValue(listResponse([scenario("a", "voice")]));
    renderScenarios();
    await screen.findByText("Scenario a");

    pickType("Voice");
    await waitFor(() => expect(lastParams().agent_type).toBe("voice"));

    pickType("All types");

    expect(typeFilter()).toHaveTextContent("All types");
    await waitFor(() =>
      expect(Object.keys(lastParams())).not.toContain("agent_type"),
    );
  });

  it("keeps the toolbar and shows the table's empty message when no row matches", async () => {
    mocks.get.mockImplementation((url, { params }) =>
      Promise.resolve(
        params.agent_type
          ? listResponse([])
          : listResponse([scenario("a", "text")]),
      ),
    );
    renderScenarios();
    await screen.findByText("Scenario a");

    pickType("Voice");

    expect(await screen.findByText("No scenarios found")).toBeInTheDocument();
    expect(typeFilter()).toHaveTextContent("Voice");
    expect(screen.queryByText("Create your first scenario")).toBeNull();
  });
});
