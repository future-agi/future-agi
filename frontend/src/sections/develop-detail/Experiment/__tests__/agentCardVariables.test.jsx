// Row 19 (restore): New experiment > Agent run card > the agent's variables
// and their mapping status. Production checks the agent version's global
// variables against the dataset's column names and shows how many are not
// mapped. The experiment runner fills each graph input from the row's column
// with the same name, so the card shows the variables and the count again.
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useForm } from "react-hook-form";
import AgentPromptRenderer from "../StepperComponentExperiment/AgentPromptRenderer";

// One stable query result per test, as react-query hands back between renders.
const agent = vi.hoisted(() => ({ query: { data: undefined }, columns: [] }));

vi.mock("src/api/develop/prompt", () => ({
  usePromptVersions: () => ({ data: undefined, isPending: false }),
}));
vi.mock("src/api/experiment/use-get-agents.js", () => ({
  useGetAgentVersions: () => agent.query,
}));
vi.mock("../ExperimentFormContext", () => ({
  useExperimentForm: () => ({ mappingColumns: agent.columns }),
}));
vi.mock(
  "src/components/custom-model-dropdown/CustomModelDropdownControl",
  () => ({
    default: () => null,
  }),
);
vi.mock("../ToolModesBlock", () => ({ default: () => null }));
vi.mock("../TryOnOneRow", () => ({ default: () => null }));
vi.mock("src/sections/common/RunSettings/RowLimits", () => ({
  default: () => null,
}));
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: undefined, isSuccess: false }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useVoiceOptions: () => ({ data: null, isLoading: false }),
}));
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() => {
      throw new Error("no network in this test");
    }),
  },
  endpoints: { develop: { modelParams: "/unused" } },
}));
vi.mock("src/components/image", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/custom-model-dropdown/CustomModelDropdown", () => ({
  default: () => null,
}));
vi.mock("../../CustomAudioDialog", () => ({ default: () => null }));
vi.mock("../VoiceSelectorPopover", () => ({ default: () => null }));
vi.mock("../ModelParamsPopover", () => ({ default: () => null }));

const setVersion = (globals) => {
  agent.query = {
    data: {
      pages: [
        {
          result: {
            versions: [
              {
                id: "graph-version-2",
                version_number: 2,
                status: "active",
                commit_message: "",
                global_variables: globals,
              },
            ],
          },
        },
      ],
    },
    isPending: false,
  };
};

function Card() {
  const form = useForm({ defaultValues: { promptConfig: [{}] } });
  return (
    <AgentPromptRenderer
      prompt={{ agentId: "agent-a", name: "Support agent" }}
      index={0}
      control={form.control}
      setValue={form.setValue}
      getValues={form.getValues}
      watch={form.watch}
      unregister={form.unregister}
      fieldPrefix="promptConfig.0"
      type="agent"
    />
  );
}

afterEach(() => {
  cleanup();
  agent.query = { data: undefined };
  agent.columns = [];
});

describe("Agent run card: global variables and mapping status (row 19)", () => {
  it("lists the agent's variables and how many have no column", async () => {
    setVersion(["question", "customer_tier"]);
    agent.columns = [
      { id: "col-1", name: "question", data_type: "text" },
      { id: "col-2", name: "answer", data_type: "text" },
    ];
    render(<Card />);
    const block = await waitFor(() => screen.getByTestId("agent-variables"));
    expect(block).toHaveTextContent("question, customer_tier");
    expect(block).toHaveTextContent("1/2 variables mapped to dataset columns");
    expect(block).toHaveTextContent(
      "Update global variables in agent to match dataset columns.",
    );
  });

  it("says all are mapped when every variable has a column", async () => {
    setVersion(["question"]);
    agent.columns = [{ id: "col-1", name: "question", data_type: "text" }];
    render(<Card />);
    const block = await waitFor(() => screen.getByTestId("agent-variables"));
    expect(block).toHaveTextContent("1/1 variables mapped to dataset columns");
    expect(block).not.toHaveTextContent("Update global variables");
  });

  it("shows nothing for an agent version without global variables", async () => {
    setVersion([]);
    agent.columns = [{ id: "col-1", name: "question", data_type: "text" }];
    render(<Card />);
    await waitFor(() =>
      expect(screen.getByText("Support agent")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("agent-variables")).toBeNull();
  });
});
