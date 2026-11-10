import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";

import RunTestsContent from "../RunTestsContent";
import { SIMULATION_TYPE } from "../common";

const mocks = vi.hoisted(() => ({ get: vi.fn(), delete: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, delete: mocks.delete },
  endpoints: {
    runTests: {
      list: "/simulate/run-tests/",
      detail: (id) => `/simulate/run-tests/${id}/`,
    },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/sections/develop-detail/Common/CustomDialog/CustomDialog", () => ({
  default: () => null,
}));

const runTest = (id, agentType) => ({
  id,
  name: `Run ${id}`,
  source_type: agentType ? "agent_definition" : "prompt",
  agent_definition_detail: agentType
    ? { id: `agent-${id}`, agent_name: `Agent ${id}`, agent_type: agentType }
    : null,
  scenarios_detail: [],
  evals_detail: [],
  last_run_at: null,
});

const listResponse = (results, count = results.length) => ({
  data: { count, results },
});

const lastParams = () => mocks.get.mock.calls.at(-1)[1].params;

const renderRunTests = (props = {}) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <RunTestsContent
        onCreateClick={vi.fn()}
        emptyTitle="Create your first simulation"
        {...props}
      />
    </QueryClientProvider>,
  );

const typeFilter = () => screen.getByRole("combobox", { name: "Agent type" });

const pickType = (label) => {
  fireEvent.mouseDown(typeFilter());
  fireEvent.click(screen.getByRole("option", { name: label }));
};

describe("Run Simulation agent type filter", () => {
  beforeEach(() => vi.clearAllMocks());

  it("starts on All types and sends no agent_type", async () => {
    mocks.get.mockResolvedValue(
      listResponse([runTest("a", "voice"), runTest("b", "text")]),
    );
    renderRunTests();

    expect(await screen.findByText("Run a")).toBeInTheDocument();
    expect(typeFilter()).toHaveTextContent("All types");
    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(Object.keys(lastParams())).not.toContain("agent_type");
  });

  it.each([
    ["Voice", "voice"],
    ["Chat", "text"],
  ])(
    "picking %s requests agent_type=%s from page 1",
    async (label, value) => {
      mocks.get.mockResolvedValue(listResponse([runTest("a", "voice")], 60));
      renderRunTests();
      await screen.findByText("Run a");

      fireEvent.click(screen.getByRole("button", { name: "Next page" }));
      await waitFor(() => expect(lastParams().page).toBe(2));

      pickType(label);

      await waitFor(() =>
        expect(lastParams()).toMatchObject({
          page: 1,
          limit: 25,
          search: "",
          summary: true,
          agent_type: value,
        }),
      );
    },
  );

  it("sends the type together with the search text", async () => {
    mocks.get.mockResolvedValue(listResponse([runTest("a", "voice")]));
    renderRunTests();
    await screen.findByText("Run a");

    pickType("Voice");
    fireEvent.change(screen.getByPlaceholderText("Search"), {
      target: { value: "refund" },
    });

    await waitFor(
      () =>
        expect(lastParams()).toMatchObject({
          page: 1,
          search: "refund",
          agent_type: "voice",
        }),
      { timeout: 2000 },
    );
  });

  it("keeps the toolbar and shows the table's empty message when no run matches", async () => {
    mocks.get.mockImplementation((url, { params }) =>
      Promise.resolve(
        params.agent_type
          ? listResponse([])
          : listResponse([runTest("a", "text")]),
      ),
    );
    renderRunTests();
    await screen.findByText("Run a");

    pickType("Voice");

    expect(await screen.findByText("No tests found")).toBeInTheDocument();
    expect(typeFilter()).toHaveTextContent("Voice");
    expect(
      screen.getByRole("button", { name: "Create a Simulation" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Create your first simulation")).toBeNull();
  });

  it("has no type filter on a prompt's simulations, which are all chats", async () => {
    mocks.get.mockResolvedValue(listResponse([runTest("a", null)]));
    renderRunTests({
      simulationType: SIMULATION_TYPE.PROMPT,
      promptTemplateId: "prompt-1",
      showHeader: false,
    });
    await screen.findByText("Run a");

    expect(
      screen.queryByRole("combobox", { name: "Agent type" }),
    ).not.toBeInTheDocument();
    expect(Object.keys(lastParams())).not.toContain("agent_type");
  });
});
