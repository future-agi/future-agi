import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
} from "src/utils/test-utils";

const markers = {
  name: "SYNTHETIC_TEST_NAME_21ab",
  description: "SYNTHETIC_TEST_DESCRIPTION_32bc",
  scenario: "SYNTHETIC_SCENARIO_NAME_43cd",
  evalName: "SYNTHETIC_EVAL_NAME_54de",
  evalDescription: "SYNTHETIC_EVAL_DESCRIPTION_65ef",
  prompt: "SYNTHETIC_EVAL_PROMPT_76fa",
  config: "SYNTHETIC_EVAL_CONFIG_87ab",
  instructions: "SYNTHETIC_EVAL_INSTRUCTIONS_98bc",
};

// The hook's pages must be stable: a new pages object on every render makes
// the page's version auto-selection effect loop indefinitely.
const mocks = vi.hoisted(() => {
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "synthetic-project-token");
  return {
    get: vi.fn(),
    post: vi.fn(),
    mixpanel: {
      init: vi.fn(),
      track: vi.fn(),
      register: vi.fn(),
      get_config: vi.fn(),
      set_config: vi.fn(),
      stop_session_recording: vi.fn(),
    },
    agentDefinitions: {
      agentDefinitions: [
        { id: "agent-1", agent_name: "Synthetic agent", agent_type: "chat" },
      ],
      fetchNextPage: () => {},
      isLoading: false,
      isFetchingNextPage: false,
    },
    emptyVersions: {
      data: { pages: [] },
      isLoading: false,
      fetchNextPage: () => {},
      isFetchingNextPage: false,
    },
    versions: {
      data: {
        pages: [{ results: [{ id: "version-1", version_name_display: "v1" }] }],
      },
      isLoading: false,
      fetchNextPage: () => {},
      isFetchingNextPage: false,
    },
  };
});

vi.mock("mixpanel-browser", () => ({ default: mocks.mixpanel }));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    scenarios: {
      list: "/scenarios/",
      getColumns: "/scenarios/columns/",
    },
    agentDefinitions: {
      versionDetail: (agent, version) => `/agents/${agent}/${version}/`,
    },
    runTests: { create: "/run-tests/create/" },
  },
}));
vi.mock("../common", async (importOriginal) => ({
  ...(await importOriginal()),
  useAgentDefinitions: () => mocks.agentDefinitions,
}));
vi.mock("src/api/agent-definition/agent-definition-version", () => ({
  useAgentDefinitionVersions: ({ selectedAgentId }) =>
    selectedAgentId ? mocks.versions : mocks.emptyVersions,
}));
vi.mock("src/sections/common/EvalPicker", async () => {
  const { serializeEvalConfig } = await import(
    "src/sections/common/EvalPicker/serializeEvalConfig"
  );
  const EvalPickerDrawer = ({ open, onEvalAdded }) =>
    open ? (
      <button
        type="button"
        onClick={() =>
          onEvalAdded({
            templateId: "template-1",
            name: markers.evalName,
            evalTemplate: {
              name: markers.evalName,
              description: markers.evalDescription,
            },
            mapping: { transcript: "call.transcript" },
            config: {
              prompt: markers.prompt,
              params: { note: markers.config },
            },
            instructions: markers.instructions,
          })
        }
      >
        Save evaluation
      </button>
    ) : null;
  EvalPickerDrawer.propTypes = {
    open: PropTypes.bool,
    onEvalAdded: PropTypes.func,
  };
  return { serializeEvalConfig, EvalPickerDrawer };
});
vi.mock("src/sections/agents/AgentConfiguration/UpdateKeysDialog", () => ({
  default: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));

import CreateRunTestPage from "../CreateRunTestPage";
import { Events } from "src/utils/Mixpanel";

const scenarios = ["scenario-1", "scenario-2", "scenario-3"].map(
  (id, index) => ({
    id,
    name: `${markers.scenario} ${index + 1}`,
    description: `${markers.description} ${index + 1}`,
    dataset_rows: 3,
  }),
);

const renderPage = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return renderWithRouter(
    <QueryClientProvider client={client}>
      <CreateRunTestPage open onClose={vi.fn()} />
    </QueryClientProvider>,
  );
};

describe("create simulation event", () => {
  beforeEach(() => {
    mocks.mixpanel.track.mockClear();
    mocks.post.mockReset();
    mocks.post.mockResolvedValue({ data: { id: "test-1" } });
    mocks.get.mockImplementation((url) => {
      if (url === "/scenarios/") {
        return Promise.resolve({ data: { count: 3, results: scenarios } });
      }
      if (url === "/scenarios/columns/") {
        return Promise.resolve({ data: { columnConfigs: [] } });
      }
      return Promise.resolve({ data: {} });
    });
  });

  it("sends the agent version and counts when a test is created", async () => {
    renderPage();

    fireEvent.change(screen.getByPlaceholderText(/Enter a name for your/i), {
      target: { value: markers.name },
    });
    fireEvent.change(
      screen.getByPlaceholderText(/Describe what this simulation/i),
      {
        target: { value: markers.description },
      },
    );
    fireEvent.click(
      screen.getByPlaceholderText(/Choose your agent that you want to test/i),
    );
    fireEvent.click(await screen.findByText("Synthetic agent"));

    const next = screen.getByRole("button", { name: /next/i });
    await waitFor(() => expect(next).toBeEnabled());
    fireEvent.click(next);

    await screen.findByText(`${markers.scenario} 1`);
    scenarios.forEach((scenario) =>
      fireEvent.click(screen.getByText(scenario.name)),
    );
    fireEvent.click(next);

    fireEvent.click(screen.getByRole("button", { name: /add evaluations/i }));
    fireEvent.click(screen.getByRole("button", { name: /save evaluation/i }));
    fireEvent.click(next);
    fireEvent.click(screen.getByRole("button", { name: /run simulation/i }));

    const expectedPayload = {
      name: markers.name,
      description: markers.description,
      scenario_ids: scenarios.map(({ id }) => id),
      agent_definition_id: "agent-1",
      agent_version: "version-1",
      eval_config_ids: [],
      evaluations_config: [
        {
          name: markers.evalName,
          template_id: "template-1",
          mapping: { transcript: "call.transcript" },
          config: {
            prompt: markers.prompt,
            params: { note: markers.config },
            run_config: {},
            mapping: { transcript: "call.transcript" },
            instructions: markers.instructions,
          },
          error_localizer: false,
        },
      ],
      dataset_row_ids: [],
      enable_tool_evaluation: false,
    };
    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/run-tests/create/",
        expectedPayload,
      ),
    );

    const eventCalls = mocks.mixpanel.track.mock.calls.filter(
      ([event]) => event === Events.runTestCreateTestClicked,
    );
    expect(eventCalls).toHaveLength(1);
    expect(eventCalls[0][1]).toEqual({
      form_fields: {
        agent_definition_id: "agent-1",
        agent_version: "version-1",
        scenario_count: 3,
        evaluation_count: 1,
        enable_tool_evaluation: false,
      },
    });
    for (const marker of Object.values(markers)) {
      for (const call of mocks.mixpanel.track.mock.calls) {
        expect(JSON.stringify(call)).not.toContain(marker);
      }
    }
  });
});
