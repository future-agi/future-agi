import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  act,
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
// test is about the switch, not the picker.
vi.mock(
  "src/components/custom-model-dropdown/CustomModelDropdownControl",
  () => ({
    default: () => null,
  }),
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

// The version-detail shape the page reads: agent_kind sits beside the snapshot.
function versionDetail({ kind, apiKey, ...snapshot }) {
  return {
    id: "v1",
    agent_kind: kind,
    ...(apiKey ? { api_key: apiKey } : {}),
    configuration_snapshot: {
      agent_name: "Support agent",
      languages: ["en"],
      assistant_id: null,
      observability_enabled: false,
      description: "Answers support questions",
      inbound: true,
      contact_number: "",
      commit_message: "",
      ...snapshot,
    },
  };
}

const CHAT = { agent_type: "text", provider: null };
const VOICE_LIVEKIT = { agent_type: "voice", provider: "livekit" };
const VOICE_LIVEKIT_OUTBOUND = { ...VOICE_LIVEKIT, inbound: false };

const VAPI = {
  agent_type: "voice",
  provider: "vapi",
  authentication_method: "api_key",
};
const SOURCE_VAPI = versionDetail({ kind: "source", ...VAPI });
// A code agent that already holds a key and an assistant id, so the form is valid before anything is cleared.
const SOURCE_VAPI_OUTBOUND_WITH_KEY = versionDetail({
  kind: "source",
  apiKey: "sk-test",
  ...VAPI,
  assistant_id: "asst_1",
  inbound: false,
});
const HOSTED_VAPI = versionDetail({
  kind: "hosted",
  apiKey: "sk-test",
  ...VAPI,
  assistant_id: "asst_1",
});
const HOSTED_VAPI_ON = versionDetail({
  kind: "hosted",
  apiKey: "sk-test",
  ...VAPI,
  assistant_id: "asst_1",
  observability_enabled: true,
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

// Dirties the form with an unrelated field and fills the required message.
function editDescriptionAndCommit() {
  fireEvent.change(screen.getByLabelText(/Prompt\/Chains/), {
    target: { value: "A new prompt" },
  });
  fireEvent.change(
    screen.getByLabelText(/Commit Message|What's changing in this version/),
    { target: { value: "Change the prompt" } },
  );
}

const createBody = () => {
  const call = axios.post.mock.calls.find(([url]) => url === CREATE_URL);
  return call ? call[1] : undefined;
};

beforeEach(() => {
  vi.clearAllMocks();
  axios.get.mockResolvedValue({ data: {} });
  axios.post.mockImplementation(async (url) =>
    url === CREATE_URL
      ? { data: { message: "saved", version: { id: "v2" } } }
      : { data: {} },
  );
});

describe.each(MOUNTS)("observability switch on $name", ({ mount, save }) => {
  describe.each([
    ["a chat code agent", CHAT],
    ["a voice code agent", VOICE_LIVEKIT],
    ["an outbound voice code agent", VOICE_LIVEKIT_OUTBOUND],
  ])("for %s", (_label, snapshot) => {
    const seed = (extra = {}) =>
      useAgentDetailsStore.setState({
        agentDetails: versionDetail({ kind: "source", ...snapshot, ...extra }),
        selectedVersion: "v1",
      });

    it("renders the switch enabled with no key and no agent id", async () => {
      seed();
      mount();

      await screen.findByText("Enable Observability");
      expect(switchBox()).toBeEnabled();
      expect(switchBox()).not.toBeChecked();
    });

    it("sends observability_enabled true when it is ticked and saved", async () => {
      seed();
      mount();
      await screen.findByText("Enable Observability");

      await userEvent.click(switchBox());
      editDescriptionAndCommit();
      await save();

      await waitFor(() => expect(createBody()).toBeDefined());
      expect(createBody().observability_enabled).toBe(true);
    });

    it("keeps it on when an unrelated field is saved", async () => {
      seed({ observability_enabled: true });
      mount();
      await screen.findByText("Enable Observability");

      // Nothing in the form may untick it for want of a key or an agent id.
      expect(switchBox()).toBeChecked();

      editDescriptionAndCommit();
      await save();

      await waitFor(() => expect(createBody()).toBeDefined());
      expect(createBody().observability_enabled).toBe(true);
      expect(switchBox()).toBeChecked();
    });
  });

  it("shows no switch for a hosted chat agent", async () => {
    useAgentDetailsStore.setState({
      agentDetails: versionDetail({ kind: "hosted", ...CHAT }),
      selectedVersion: "v1",
    });
    mount();

    await screen.findByLabelText(/Prompt\/Chains/);
    expect(screen.queryByText("Enable Observability")).toBeNull();
  });

  it("keeps the switch of a hosted vapi agent disabled without a key", async () => {
    useAgentDetailsStore.setState({
      agentDetails: versionDetail({
        kind: "hosted",
        agent_type: "voice",
        provider: "vapi",
        authentication_method: "api_key",
      }),
      selectedVersion: "v1",
    });
    mount();

    await screen.findByText("Enable Observability");
    expect(switchBox()).toBeDisabled();
  });
});

// Only the commit message is filled: these cases need the description to stay empty.
function dirtyWithCommitOnly() {
  fireEvent.change(
    screen.getByLabelText(/Commit Message|What's changing in this version/),
    { target: { value: "Turn observability on" } },
  );
}

describe.each(MOUNTS)("empty description on $name", ({ mount, save, name }) => {
  describe.each([
    ["a chat code agent", CHAT],
    ["a voice code agent", VOICE_LIVEKIT],
  ])("for %s", (_label, snapshot) => {
    it("saves with the switch on when the stored Agent holds the instructions", async () => {
      useAgentDetailsStore.setState({
        agentDetails: versionDetail({
          kind: "source",
          ...snapshot,
          description: "",
        }),
        selectedVersion: "v1",
      });
      mount();
      await screen.findByText("Enable Observability");

      await userEvent.click(switchBox());
      dirtyWithCommitOnly();
      await save();

      await waitFor(() => expect(createBody()).toBeDefined());
      expect(createBody().observability_enabled).toBe(true);
    });
  });

  it("still refuses an empty description for a hosted agent", async () => {
    useAgentDetailsStore.setState({
      agentDetails: versionDetail({
        kind: "hosted",
        ...CHAT,
        description: "",
      }),
      selectedVersion: "v1",
    });
    mount();
    await screen.findByLabelText(/Prompt\/Chains/);

    dirtyWithCommitOnly();
    if (name === "the agent page") {
      fireEvent.click(await screen.findByRole("button", { name: "Save" }));
      expect(
        await screen.findByText("Description is required"),
      ).toBeInTheDocument();
    } else {
      // The button is disabled before the form has been checked at all, so
      // show it turn valid with a description and invalid again without one.
      const button = await screen.findByRole("button", {
        name: "Create version v2",
      });
      const prompt = screen.getByLabelText(/Prompt\/Chains/);
      fireEvent.change(prompt, { target: { value: "A prompt" } });
      await waitFor(() => expect(button).toBeEnabled());
      fireEvent.change(prompt, { target: { value: "" } });
      await waitFor(() => expect(button).toBeDisabled());
    }
    expect(createBody()).toBeUndefined();
  });
});

describe.each(MOUNTS)("required marks on $name", ({ mount }) => {
  it("does not mark the assistant id and the API key required for a code agent whose switch is on", async () => {
    useAgentDetailsStore.setState({
      agentDetails: SOURCE_VAPI,
      selectedVersion: "v1",
    });
    mount();
    await screen.findByText("Enable Observability");

    await userEvent.click(switchBox());
    expect(switchBox()).toBeChecked();

    expect(screen.getByLabelText(/Assistant ID/)).not.toBeRequired();
    expect(screen.getByLabelText(/Provider API Key/)).not.toBeRequired();
  });

  it("does not mark the authentication method required for a code agent whose switch is on", async () => {
    useAgentDetailsStore.setState({
      agentDetails: SOURCE_VAPI,
      selectedVersion: "v1",
    });
    mount();
    await screen.findByText("Enable Observability");

    await userEvent.click(switchBox());
    expect(switchBox()).toBeChecked();

    expect(screen.getByLabelText(/Authentication Method/)).not.toBeRequired();
  });

  it("marks them required for a hosted agent whose switch is on, and not when it is off", async () => {
    useAgentDetailsStore.setState({
      agentDetails: HOSTED_VAPI,
      selectedVersion: "v1",
    });
    mount();
    await screen.findByText("Enable Observability");

    expect(screen.getByLabelText(/Assistant ID/)).not.toBeRequired();
    expect(screen.getByLabelText(/Provider API Key/)).not.toBeRequired();
    expect(screen.getByLabelText(/Authentication Method/)).not.toBeRequired();

    await userEvent.click(switchBox());
    expect(switchBox()).toBeChecked();

    expect(screen.getByLabelText(/Assistant ID/)).toBeRequired();
    expect(screen.getByLabelText(/Provider API Key/)).toBeRequired();
    expect(screen.getByLabelText(/Authentication Method/)).toBeRequired();
  });
});

const verifyPosts = () =>
  axios.post.mock.calls.filter(
    ([url]) =>
      url === endpoints.agentDefinitions.verifyApiKey ||
      url === endpoints.agentDefinitions.verifyAssistantId,
  );

describe.each(MOUNTS)(
  "voice rule for a code agent on $name",
  ({ mount, save, name }) => {
    it("saves a code vapi agent with the switch on and no key or assistant id", async () => {
      useAgentDetailsStore.setState({
        agentDetails: SOURCE_VAPI,
        selectedVersion: "v1",
      });
      mount();
      await screen.findByText("Enable Observability");

      await userEvent.click(switchBox());
      editDescriptionAndCommit();
      await save();

      await waitFor(() => expect(createBody()).toBeDefined());
      expect(createBody().observability_enabled).toBe(true);
      expect(verifyPosts()).toEqual([]);
    });

    it("still blocks an outbound code vapi agent that has lost its API key", async () => {
      useAgentDetailsStore.setState({
        agentDetails: SOURCE_VAPI_OUTBOUND_WITH_KEY,
        selectedVersion: "v1",
      });
      mount();
      await screen.findByText("Enable Observability");

      await userEvent.click(switchBox());
      editDescriptionAndCommit();
      if (name === "the new-version drawer") {
        // Enabled first, so the disabled state below is the rule's verdict and
        // not the form's state before it has been checked.
        const button = await screen.findByRole("button", {
          name: "Create version v2",
        });
        await waitFor(() => expect(button).toBeEnabled());
      }

      fireEvent.change(screen.getByLabelText(/Provider API Key/), {
        target: { value: "" },
      });

      expect(await screen.findByText("API key is required")).toBeVisible();
      if (name === "the new-version drawer") {
        await waitFor(() =>
          expect(
            screen.getByRole("button", { name: "Create version v2" }),
          ).toBeDisabled(),
        );
      } else {
        fireEvent.click(await screen.findByRole("button", { name: "Save" }));
      }
      // Let the form rule finish before reading the calls.
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 50));
      });
      expect(createBody()).toBeUndefined();
    });
  },
);

describe.each(MOUNTS)(
  "store filled after mount on $name",
  ({ mount, save }) => {
    it("renders the switch enabled and sends observability_enabled true", async () => {
      // The page mounts with an empty store; the version detail arrives later.
      useAgentDetailsStore.setState({
        agentDetails: {},
        selectedVersion: "v1",
      });
      mount();
      act(() => {
        useAgentDetailsStore.setState({ agentDetails: SOURCE_VAPI });
      });

      await screen.findByText("Enable Observability");
      expect(switchBox()).toBeEnabled();

      await userEvent.click(switchBox());
      editDescriptionAndCommit();
      await save();

      await waitFor(() => expect(createBody()).toBeDefined());
      expect(createBody().observability_enabled).toBe(true);
    });
  },
);

describe.each(MOUNTS)("hosted agents on $name", ({ mount, save }) => {
  it("sends no observability_enabled for a hosted chat agent", async () => {
    useAgentDetailsStore.setState({
      agentDetails: versionDetail({
        kind: "hosted",
        ...CHAT,
        observability_enabled: true,
      }),
      selectedVersion: "v1",
    });
    mount();
    await screen.findByLabelText(/Prompt\/Chains/);

    editDescriptionAndCommit();
    await save();

    await waitFor(() => expect(createBody()).toBeDefined());
    expect(createBody()).not.toHaveProperty("observability_enabled");
  });

  it.each([
    ["Provider API Key", /Provider API Key/],
    ["Assistant ID", /Assistant ID/],
  ])(
    "unticks the switch of a hosted voice agent when its %s is cleared",
    async (_label, field) => {
      useAgentDetailsStore.setState({
        agentDetails: HOSTED_VAPI_ON,
        selectedVersion: "v1",
      });
      mount();
      await screen.findByText("Enable Observability");
      expect(switchBox()).toBeChecked();

      fireEvent.change(screen.getByLabelText(field), {
        target: { value: "" },
      });

      await waitFor(() => expect(switchBox()).not.toBeChecked());
      expect(switchBox()).toBeDisabled();
    },
  );
});

describe.each(MOUNTS)("LiveKit and prompt marks on $name", ({ mount }) => {
  // The agent-name input shares its label with the first field, so it is found by placeholder.
  const marked = () => [
    screen.getByLabelText(/Prompt\/Chains/),
    screen.getByLabelText(/LiveKit Server URL/),
    screen.getByLabelText(/LiveKit API Key/),
    screen.getByLabelText(/LiveKit API Secret/),
    screen.getByPlaceholderText("e.g. test-agent"),
  ];

  it("marks none of the five fields required for a code LiveKit voice agent", async () => {
    useAgentDetailsStore.setState({
      agentDetails: versionDetail({ kind: "source", ...VOICE_LIVEKIT }),
      selectedVersion: "v1",
    });
    mount();
    await screen.findByLabelText(/LiveKit Server URL/);

    marked().forEach((field) => expect(field).not.toBeRequired());
  });

  it("marks all five fields required for a hosted LiveKit voice agent", async () => {
    useAgentDetailsStore.setState({
      agentDetails: versionDetail({ kind: "hosted", ...VOICE_LIVEKIT }),
      selectedVersion: "v1",
    });
    mount();
    await screen.findByLabelText(/LiveKit Server URL/);

    marked().forEach((field) => expect(field).toBeRequired());
  });
});

describe.each(MOUNTS)(
  "description in the saved body on $name",
  ({ mount, save }) => {
    it.each([
      ["a chat code agent", CHAT],
      ["a voice code agent", VOICE_LIVEKIT],
    ])("sends an empty description for %s", async (_label, snapshot) => {
      useAgentDetailsStore.setState({
        agentDetails: versionDetail({
          kind: "source",
          ...snapshot,
          description: "",
        }),
        selectedVersion: "v1",
      });
      mount();
      await screen.findByText("Enable Observability");

      await userEvent.click(switchBox());
      dirtyWithCommitOnly();
      await save();

      await waitFor(() => expect(createBody()).toBeDefined());
      expect(createBody()).toHaveProperty("description", "");
    });

    it("sends a hosted agent's description as it is", async () => {
      useAgentDetailsStore.setState({
        agentDetails: versionDetail({
          kind: "hosted",
          ...CHAT,
          description: "Answers support questions",
        }),
        selectedVersion: "v1",
      });
      mount();
      await screen.findByLabelText(/Prompt\/Chains/);

      dirtyWithCommitOnly();
      await save();

      await waitFor(() => expect(createBody()).toBeDefined());
      expect(createBody()).toHaveProperty(
        "description",
        "Answers support questions",
      );
      expect(createBody()).not.toHaveProperty("observability_enabled");
    });
  },
);
