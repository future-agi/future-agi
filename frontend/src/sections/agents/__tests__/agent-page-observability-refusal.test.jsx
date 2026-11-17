import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  within,
  fireEvent,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";

// Keep the real `endpoints` so the create-version URL is the one the page
// really posts to; only the axios instance is stubbed.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
});

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ orgLimit: {} }),
}));

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useParams: () => ({ agentDefinitionId: "agent-1" }) };
});

vi.mock("src/api/knowledge-base/files", () => ({
  useKnowledgeBaseList: () => ({ data: [] }),
}));

vi.mock("notistack", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    enqueueSnackbar: vi.fn(),
    useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
  };
});

vi.mock("src/utils/Mixpanel", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, trackEvent: vi.fn(), handleOnDocsClicked: vi.fn() };
});

// The chat branch renders the model picker, which runs its own queries; this
// test is about the refusal, not the picker.
vi.mock(
  "src/components/custom-model-dropdown/CustomModelDropdownControl",
  () => ({ default: () => null }),
);

const { default: axios, endpoints } = await import("src/utils/axios");
const { useAgentDetailsStore } = await import("../store/agentDetailsStore");
const { default: AgentConfigurationView } = await import(
  "../AgentConfiguration/AgentConfigurationView"
);
const { default: AddNewVersionDrawer } = await import(
  "../VersionManagement/AddNewVersionDrawer"
);

const CREATE_URL = endpoints.agentDefinitions.createVersion("agent-1");

const testTheme = createTheme({
  palette: palette("light"),
  spacing: (factor) => `${0.25 * factor}rem`,
});

function Providers({ children }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider theme={testTheme}>{children}</ThemeProvider>
    </QueryClientProvider>
  );
}
Providers.propTypes = { children: PropTypes.node };

function versionDetail({ kind, apiKey, ...snapshot }) {
  return {
    id: "v1",
    agent_kind: kind,
    ...(apiKey ? { api_key: apiKey } : {}),
    configuration_snapshot: {
      agent_name: "Support agent",
      languages: ["en"],
      observability_enabled: false,
      description: "Answers support questions",
      inbound: true,
      contact_number: "",
      commit_message: "",
      ...snapshot,
    },
  };
}

const SOURCE_CHAT = versionDetail({
  kind: "source",
  agent_type: "text",
  provider: null,
});

const HOSTED_VAPI = versionDetail({
  kind: "hosted",
  apiKey: "sk-test",
  agent_type: "voice",
  provider: "vapi",
  assistant_id: "asst_1",
  authentication_method: "api_key",
});

const MOUNTS = [
  {
    name: "the agent page",
    mount: () =>
      render(
        <Providers>
          <AgentConfigurationView />
        </Providers>,
      ),
    save: async () =>
      fireEvent.click(await screen.findByRole("button", { name: "Save" })),
  },
  {
    name: "the new-version drawer",
    mount: () =>
      render(
        <Providers>
          <AddNewVersionDrawer open onClose={vi.fn()} latestVersion={1} />
        </Providers>,
      ),
    save: async () => {
      const button = await screen.findByRole("button", {
        name: "Create version v2",
      });
      await waitFor(() => expect(button).toBeEnabled());
      fireEvent.click(button);
    },
  },
];

const switchBox = () =>
  within(screen.getByText("Enable Observability").closest("label")).getByRole(
    "checkbox",
  );

function editDescriptionAndCommit() {
  fireEvent.change(screen.getByLabelText(/Prompt\/Chains/), {
    target: { value: "A new prompt" },
  });
  fireEvent.change(
    screen.getByLabelText(/Commit Message|What's changing in this version/),
    { target: { value: "Change the prompt" } },
  );
}

// The MUI field box that holds a labelled input and its helper text.
const fieldOf = (label) =>
  screen.getByLabelText(label).closest(".MuiFormControl-root");

// What the version endpoint answers when the switch is refused (08 error
// envelope: a banner string plus details keyed by field).
const refusal = (details) => ({
  response: {
    data: {
      error: "Observability prerequisites are not met",
      code: "observability_prerequisite_missing",
      details,
    },
  },
});

function rejectCreateWith(error) {
  axios.post.mockImplementation(async (url) => {
    if (url === CREATE_URL) throw error;
    return { data: {} };
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  axios.get.mockResolvedValue({ data: {} });
  axios.post.mockResolvedValue({ data: {} });
});

describe.each(MOUNTS)("observability refusal on $name", ({ mount, save }) => {
  it("shows details[field] beside each field", async () => {
    useAgentDetailsStore.setState({
      agentDetails: HOSTED_VAPI,
      selectedVersion: "v1",
    });
    rejectCreateWith(
      refusal({
        api_key: ["The provider refused this API key"],
        assistant_id: ["The provider has no such assistant"],
      }),
    );
    mount();
    await screen.findByText("Enable Observability");

    await userEvent.click(switchBox());
    expect(switchBox()).toBeChecked();
    editDescriptionAndCommit();
    await save();

    const apiKeyField = fieldOf(/Provider API Key/);
    const assistantField = fieldOf(/Assistant ID/);
    expect(
      await within(apiKeyField).findByText("The provider refused this API key"),
    ).toBeInTheDocument();
    expect(
      await within(assistantField).findByText(
        "The provider has no such assistant",
      ),
    ).toBeInTheDocument();
    // Each message belongs to its own field and to no other.
    expect(
      within(apiKeyField).queryByText("The provider has no such assistant"),
    ).toBeNull();
    expect(
      within(assistantField).queryByText("The provider refused this API key"),
    ).toBeNull();
    expect(switchBox()).not.toBeChecked();
  });

  it("shows only the first message of a field's list", async () => {
    useAgentDetailsStore.setState({
      agentDetails: HOSTED_VAPI,
      selectedVersion: "v1",
    });
    rejectCreateWith(
      refusal({
        api_key: [
          "The provider refused this API key",
          "A second message about the key",
        ],
      }),
    );
    mount();
    await screen.findByText("Enable Observability");

    await userEvent.click(switchBox());
    editDescriptionAndCommit();
    await save();

    expect(
      await within(fieldOf(/Provider API Key/)).findByText(
        "The provider refused this API key",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("A second message about the key")).toBeNull();
  });

  it("shows the banner only for a detail key that names no field", async () => {
    useAgentDetailsStore.setState({
      agentDetails: HOSTED_VAPI,
      selectedVersion: "v1",
    });
    rejectCreateWith(refusal({ provider: ["Choose another provider"] }));
    mount();
    await screen.findByText("Enable Observability");

    editDescriptionAndCommit();
    await save();

    // The banner is set after the field messages, so it also shows that
    // reading the details threw nothing.
    expect(
      await screen.findByText("Observability prerequisites are not met"),
    ).toBeVisible();
    expect(screen.queryByText("Choose another provider")).toBeNull();
  });

  it("shows the instrumentation refusal under the switch of a code agent and leaves it off", async () => {
    useAgentDetailsStore.setState({
      agentDetails: SOURCE_CHAT,
      selectedVersion: "v1",
    });
    rejectCreateWith(
      refusal({ instrumentation: ["The agent's code carries no telemetry"] }),
    );
    mount();
    await screen.findByText("Enable Observability");

    await userEvent.click(switchBox());
    expect(switchBox()).toBeChecked();
    editDescriptionAndCommit();
    await save();

    const message = await screen.findByText(
      "The agent's code carries no telemetry",
    );
    // The message sits in the switch's own control, not elsewhere on the page.
    expect(message.closest("label, .MuiFormControl-root")).toContainElement(
      screen.getByText("Enable Observability"),
    );
    expect(switchBox()).not.toBeChecked();
  });

  it("leaves the switch alone on an ordinary 400 that also carries details", async () => {
    useAgentDetailsStore.setState({
      agentDetails: SOURCE_CHAT,
      selectedVersion: "v1",
    });
    rejectCreateWith({
      response: {
        data: {
          error: "Something else was wrong",
          details: { instrumentation: ["Not an observability refusal"] },
        },
      },
    });
    mount();
    await screen.findByText("Enable Observability");

    await userEvent.click(switchBox());
    editDescriptionAndCommit();
    await save();

    expect(await screen.findByText("Something else was wrong")).toBeVisible();
    expect(screen.queryByText("Not an observability refusal")).toBeNull();
    expect(switchBox()).toBeChecked();
  });
});
