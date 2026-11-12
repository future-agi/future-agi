/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  renderWithRouter,
  screen,
  waitFor,
  fireEvent,
} from "src/utils/test-utils";

import CreateRunTestPage from "../CreateRunTestPage";

const VERSION_URL = "/simulate/agent-definitions/agent-1/versions/version-1/";

// Hook return values must stay referentially stable across renders (see
// scenario-step-loading.test.jsx).
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  snackbar: vi.fn(),
  versionDetail: null,
  agentDefinitions: {
    agentDefinitions: [
      { id: "agent-1", agent_name: "Support Agent", agent_type: "voice" },
    ],
    fetchNextPage: () => {},
    isLoading: false,
    isFetchingNextPage: false,
  },
  agentVersionsEmpty: {
    data: { pages: [] },
    isLoading: false,
    fetchNextPage: () => {},
    isFetchingNextPage: false,
  },
  agentVersionsLoaded: {
    data: {
      pages: [{ results: [{ id: "version-1", version_name_display: "v1" }] }],
    },
    isLoading: false,
    fetchNextPage: () => {},
    isFetchingNextPage: false,
  },
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    scenarios: {
      list: "/scenarios/",
      getColumns: "/scenarios/columns/",
    },
    agentDefinitions: {
      versionDetail: (agentId, versionId) =>
        `/simulate/agent-definitions/${agentId}/versions/${versionId}/`,
    },
    runTests: {
      create: "/simulate/run-tests/create/",
    },
  },
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({
    enqueueSnackbar: mocks.snackbar,
    closeSnackbar: () => {},
  }),
}));

vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));

vi.mock("../common", async (importOriginal) => ({
  ...(await importOriginal()),
  useAgentDefinitions: () => mocks.agentDefinitions,
}));

vi.mock("src/api/agent-definition/agent-definition-version", () => ({
  useAgentDefinitionVersions: ({ selectedAgentId }) =>
    selectedAgentId ? mocks.agentVersionsLoaded : mocks.agentVersionsEmpty,
}));

vi.mock("src/sections/common/EvalPicker", () => ({
  serializeEvalConfig: () => ({ config: {} }),
  EvalPickerDrawer: () => null,
}));

// The real dialog (AgentConfiguration/UpdateKeysDialog.jsx) is titled
// "Update Keys for <agent>" and asks for an API Key and an Assistant ID.
vi.mock("src/sections/agents/AgentConfiguration/UpdateKeysDialog", () => ({
  default: ({ open }) =>
    open ? <div role="dialog">Update Keys for Support Agent</div> : null,
}));

// Version detail as AgentVersionResponseSerializer returns it (snake_case).
// A LiveKit snapshot carries the server URL and agent name, the detail view
// adds the masked key and secret, and there is no assistant_id.
const liveKitVersion = {
  id: "version-1",
  agent_definition: "agent-1",
  api_key: "****",
  configuration_snapshot: {
    provider: "livekit_bridge",
    agent_type: "voice",
    livekit_url: "wss://agent.example.com",
    livekit_agent_name: "support-agent",
    livekit_api_key: "****",
    livekit_api_secret: "****",
  },
};

// A Vapi agent saved without its API key or assistant ID.
const vapiVersionWithoutKeys = {
  id: "version-1",
  agent_definition: "agent-1",
  api_key: null,
  configuration_snapshot: { provider: "vapi", agent_type: "voice" },
};

// A Retell agent connected with its keys: the switch used to turn on, but the
// run skips tool-call evaluation for any provider other than Vapi.
const retellVersionWithKeys = {
  id: "version-1",
  agent_definition: "agent-1",
  api_key: "****",
  configuration_snapshot: {
    provider: "retell",
    agent_type: "voice",
    assistant_id: "agent_retell_1",
  },
};

// A Bland agent saved without keys: it used to get the same "Update Keys"
// dialog asking for a Vapi-style asst_xxx Assistant ID.
const blandVersionWithoutKeys = {
  id: "version-1",
  agent_definition: "agent-1",
  api_key: null,
  configuration_snapshot: { provider: "bland", agent_type: "voice" },
};

beforeEach(() => {
  vi.clearAllMocks();
  window.history.pushState({}, "", "/");
  mocks.get.mockImplementation((url) => {
    if (url === "/scenarios/")
      return Promise.resolve({
        data: {
          count: 1,
          results: [
            {
              id: "scenario-1",
              name: "Refund request",
              description: "A customer asks for a refund",
              dataset_rows: 5,
            },
          ],
        },
      });
    if (url === VERSION_URL)
      return Promise.resolve({ data: mocks.versionDetail });
    return Promise.resolve({ data: {} });
  });
});

const renderPage = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return renderWithRouter(
    <QueryClientProvider client={queryClient}>
      <CreateRunTestPage open onClose={vi.fn()} />
    </QueryClientProvider>,
  );
};

const nextButton = () => screen.getByRole("button", { name: /^next$/i });

const walkToEvaluations = async () => {
  fireEvent.change(screen.getByPlaceholderText(/Enter a name for your/i), {
    target: { value: "Run A" },
  });
  fireEvent.click(
    screen.getByPlaceholderText(/Choose your agent that you want to test/i),
  );
  fireEvent.click(await screen.findByText("Support Agent"));
  await waitFor(() => expect(nextButton()).toBeEnabled());
  // The switch reads the version detail; let it arrive before going on.
  await waitFor(() => expect(mocks.get).toHaveBeenCalledWith(VERSION_URL));
  fireEvent.click(nextButton());

  fireEvent.click(await screen.findByText("Refund request"));
  await waitFor(() => expect(nextButton()).toBeEnabled());
  fireEvent.click(nextButton());
  await screen.findByText("Select evaluations");
};

describe("Select Evaluations: tool call evaluation", () => {
  // Before: turning the switch on opened "Update Keys for <agent>" asking a
  // LiveKit agent for a Vapi API key and assistant ID, so it never turned on.
  // The tool-call judge reads tool calls back from Vapi only
  // (simulate/semantics.py), so for LiveKit the switch is off and says so.
  it("does not ask a LiveKit agent for Vapi keys, and says why it is off", async () => {
    mocks.versionDetail = liveKitVersion;
    renderPage();
    await walkToEvaluations();

    // The switch is the only checkbox on this step.
    const toggle = screen.getByRole("checkbox");
    fireEvent.click(toggle);

    expect(screen.queryByText(/Update Keys for/)).not.toBeInTheDocument();
    expect(toggle).not.toBeChecked();
    expect(
      screen.getByText("Available for Vapi agents only for now."),
    ).toBeInTheDocument();
    expect(toggle).toBeDisabled();
    expect(
      screen.getByRole("checkbox", { name: "Enable tool call evaluation" }),
    ).toBe(toggle);
    expect(mocks.snackbar).not.toHaveBeenCalled();
  });

  // Before: a Retell agent with keys got a switch that turned on and was
  // saved, yet the run returns at the ToolCallingSupportedProviders check
  // (simulate/temporal/activities/xl.py), so no tool-call results ever came
  // and nothing said why. A Bland agent without keys was asked for Vapi keys.
  it.each([
    ["a Retell agent with keys", retellVersionWithKeys],
    ["a Bland agent without keys", blandVersionWithoutKeys],
  ])("keeps the switch off for %s, and says why", async (_, version) => {
    mocks.versionDetail = version;
    renderPage();
    await walkToEvaluations();

    // The switch is the only checkbox on this step.
    const toggle = screen.getByRole("checkbox");
    fireEvent.click(toggle);

    expect(screen.queryByText(/Update Keys for/)).not.toBeInTheDocument();
    expect(toggle).not.toBeChecked();
    expect(
      screen.getByText("Available for Vapi agents only for now."),
    ).toBeInTheDocument();
    expect(toggle).toBeDisabled();
    expect(mocks.snackbar).not.toHaveBeenCalled();
  });

  // Guards the fix from over-reaching: a Vapi agent without keys is still
  // asked for them, and gets no "Vapi only" line.
  it("still asks a Vapi agent without keys for them", async () => {
    mocks.versionDetail = vapiVersionWithoutKeys;
    renderPage();
    await walkToEvaluations();

    const toggle = screen.getByRole("checkbox");
    expect(toggle).toBeEnabled();
    expect(
      screen.queryByText("Available for Vapi agents only for now."),
    ).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(
      await screen.findByText("Update Keys for Support Agent"),
    ).toBeInTheDocument();
  });
});
