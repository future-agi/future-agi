import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const MARK = "SYNTHETIC_CREDENTIAL_VALUE_9f3a";

const mocks = vi.hoisted(() => {
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "synthetic-project-token");
  return {
    formValues: {},
    mixpanel: {
      init: vi.fn(),
      track: vi.fn(),
      register: vi.fn(),
      get_config: vi.fn(),
      set_config: vi.fn(),
      stop_session_recording: vi.fn(),
    },
    post: vi.fn(),
  };
});

vi.mock("mixpanel-browser", () => ({ default: mocks.mixpanel }));
vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: {
    agentDefinitions: {
      create: "/simulate/agent-definitions/create/",
      createVersion: (id) => `/simulate/agent-definitions/${id}/versions/`,
    },
  },
}));
vi.mock("@hookform/resolvers/zod", () => ({
  zodResolver: () => async (values) => ({ values, errors: {} }),
}));
vi.mock("../helper", () => ({
  createAgentDefinitionSchema: () => ({}),
  get defaultAgentDefinitionValues() {
    return mocks.formValues;
  },
  stepFields: [[], [], []],
}));
vi.mock("../store/createNewAgentStore", () => ({
  useCreateNewAgentStore: () => ({
    currentStep: 2,
    reset: vi.fn(),
    nextStep: vi.fn(),
    prevStep: vi.fn(),
    setStepValidated: vi.fn(),
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("../CreateNewAgent/StepsTracker", () => ({ default: () => null }));
vi.mock(
  "../CreateNewAgent/AgentConfigurationStep/AgentConfigurationStep",
  () => ({ default: () => null }),
);
vi.mock(
  "../CreateNewAgent/AgentBasicInfoStep/AgentBasicInfoStepRightSection",
  () => ({ default: () => null }),
);
vi.mock("../CreateNewAgent/AgentBasicInfoStep/AgentBasicInfoStep", () => ({
  default: () => null,
}));
vi.mock(
  "../CreateNewAgent/AgentConfigurationStep/AgentConfigurationStepRightSection",
  () => ({ default: () => null }),
);
vi.mock(
  "../CreateNewAgent/AgentBehaviourStep/AgentBehaviourStepRightSection",
  () => ({ default: () => null }),
);
vi.mock("../CreateNewAgent/AgentBehaviourStep/AgentBehaviourStep", () => ({
  default: () => null,
}));

import CreateNewAgentDefinitionView from "../CreateNewAgentDefinitionView";
import { useAgentSubmit } from "../useAgentConfigForm";
import { Events } from "src/utils/Mixpanel";

const credentialFields = {
  apiKey: MARK,
  token: MARK,
  username: "synthetic-user",
  password: MARK,
  headers: [{ key: "Authorization", value: `Bearer ${MARK}` }],
};

const apiKeyVoiceAgent = {
  agentType: "voice",
  agentName: "Synthetic voice agent",
  languages: ["en", "es"],
  provider: "vapi",
  assistantId: "asst-synthetic",
  description: "Synthetic description",
  knowledgeBase: "kb-1",
  countryCode: "1",
  contactNumber: "5550100",
  inbound: true,
  targetSpeaksFirst: false,
  commitMessage: "Synthetic commit",
  observabilityEnabled: true,
  livekitMaxConcurrency: 5,
  ...credentialFields,
};

const livekitAgent = {
  agentType: "voice",
  agentName: "Synthetic livekit agent",
  languages: ["en"],
  provider: "livekit_bridge",
  description: "Synthetic description",
  knowledgeBase: "",
  countryCode: "",
  contactNumber: "",
  inbound: true,
  targetSpeaksFirst: false,
  commitMessage: "Synthetic commit",
  observabilityEnabled: false,
  livekitUrl: "wss://livekit.example.test",
  livekitApiKey: MARK,
  livekitApiSecret: MARK,
  livekitAgentName: "synthetic-agent",
  livekitConfigJson: JSON.stringify({ room_token: MARK, note: MARK }),
  livekitMaxConcurrency: 3,
  ...credentialFields,
  apiKey: "",
};

const chatAgent = {
  agentType: "text",
  agentName: "Synthetic chat agent",
  languages: ["en"],
  provider: "",
  description: "Synthetic description",
  knowledgeBase: "",
  countryCode: "",
  contactNumber: "",
  inbound: true,
  targetSpeaksFirst: false,
  commitMessage: "Synthetic commit",
  observabilityEnabled: false,
  model: "synthetic-model",
  modelDetails: { api_key: MARK },
  ...credentialFields,
};

const cases = [
  [
    "an api_key voice agent",
    apiKeyVoiceAgent,
    {
      agent_type: "voice",
      provider: "vapi",
      authentication_method: "api_key",
      language_count: 2,
      inbound: true,
      target_speaks_first: false,
      observability_enabled: true,
      has_knowledge_base: true,
      has_contact_number: true,
      max_concurrency: 5,
    },
  ],
  [
    "a livekit agent",
    livekitAgent,
    {
      agent_type: "voice",
      provider: "livekit_bridge",
      authentication_method: "api_key",
      language_count: 1,
      inbound: true,
      target_speaks_first: false,
      observability_enabled: false,
      has_knowledge_base: false,
      has_contact_number: false,
      max_concurrency: 3,
    },
  ],
  [
    "a chat agent",
    chatAgent,
    {
      agent_type: "text",
      language_count: 1,
      inbound: true,
      target_speaks_first: false,
      has_knowledge_base: false,
      has_contact_number: false,
    },
  ],
];

const trackedProperties = (eventName) =>
  mocks.mixpanel.track.mock.calls
    .filter(([name]) => name === eventName)
    .map(([, properties]) => properties);

describe("agent definition analytics events", () => {
  beforeEach(() => {
    mocks.mixpanel.track.mockClear();
    mocks.post.mockReset();
    mocks.post.mockResolvedValue({
      data: { agent: { id: "agent-1" }, version: { id: "v1" } },
    });
  });

  it.each(cases)(
    "create sends only the declared form fields for %s",
    async (_label, values, expected) => {
      mocks.formValues = values;
      render(
        <MemoryRouter>
          <CreateNewAgentDefinitionView />
        </MemoryRouter>,
      );

      fireEvent.click(
        screen.getByRole("button", { name: /create agent definition/i }),
      );
      await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));

      const tracked = trackedProperties(Events.createAgentDefClicked);
      expect(tracked).toHaveLength(1);
      expect(tracked[0]).toEqual({ form_fields: expected });
      expect(JSON.stringify(mocks.mixpanel.track.mock.calls)).not.toContain(
        MARK,
      );
      expect(JSON.stringify(mocks.mixpanel.track.mock.calls)).not.toContain(
        "Synthetic",
      );
    },
  );

  it.each(cases)(
    "update sends only the declared form fields for %s",
    async (_label, values, expected) => {
      const client = new QueryClient({
        defaultOptions: { mutations: { retry: false } },
      });
      function Wrapper({ children }) {
        return (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        );
      }
      Wrapper.propTypes = { children: PropTypes.node };
      const { result } = renderHook(
        () =>
          useAgentSubmit({
            agentDefinitionId: "agent-1",
            reset: vi.fn(),
            queryClient: { invalidateQueries: vi.fn() },
            enqueueSnackbar: vi.fn(),
            setError: vi.fn(),
            setSelectedVersion: vi.fn(),
          }),
        { wrapper: Wrapper },
      );

      await result.current.onSubmit({
        ...values,
        authenticationMethod: "api_key",
        voiceTransport: values.contactNumber ? "telephony" : "webrtc",
      });

      const tracked = trackedProperties(Events.updateAgentDefClicked);
      expect(tracked).toHaveLength(1);
      expect(tracked[0]).toEqual({
        id: "agent-1",
        source: "agent_def",
        form_fields: expected,
      });
      expect(JSON.stringify(mocks.mixpanel.track.mock.calls)).not.toContain(
        MARK,
      );
      expect(JSON.stringify(mocks.mixpanel.track.mock.calls)).not.toContain(
        "Synthetic",
      );
    },
  );
});
