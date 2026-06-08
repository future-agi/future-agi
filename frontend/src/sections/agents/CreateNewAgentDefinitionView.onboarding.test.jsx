/* eslint-disable react/prop-types */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import CreateNewAgentDefinitionView from "./CreateNewAgentDefinitionView";

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  nextStep: vi.fn(),
  prevStep: vi.fn(),
  recordActivationEvent: vi.fn(),
  reset: vi.fn(),
  setStepValidated: vi.fn(),
}));

vi.mock("react-router", () => ({
  useLocation: () => ({
    search:
      "?source=onboarding&onboarding=create-voice-agent&quick_start_goal=connect_voice_ai_agent&quick_start_id=voice&quick_start_primary_path=voice",
  }),
  useNavigate: () => mocks.navigate,
}));

vi.mock("@hookform/resolvers/zod", () => ({
  zodResolver: () => async () => ({ errors: {}, values: {} }),
}));

vi.mock("./helper", () => ({
  createAgentDefinitionSchema: () => ({}),
  defaultAgentDefinitionValues: {
    agentName: "",
    agentType: "",
    commitMessage: "",
    contactNumber: "",
    countryCode: "",
    description: "",
    languages: [],
    provider: "",
  },
  stepFields: [
    ["agentType", "agentName", "languages"],
    ["provider"],
    ["description"],
  ],
}));

vi.mock("./store/createNewAgentStore", () => ({
  useCreateNewAgentStore: () => ({
    currentStep: 0,
    nextStep: mocks.nextStep,
    prevStep: mocks.prevStep,
    reset: mocks.reset,
    setStepValidated: mocks.setStepValidated,
  }),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "admin" }),
}));

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false }),
}));

vi.mock("src/sections/onboarding-home/hooks/useRecordActivationEvent", () => ({
  useRecordActivationEvent: () => ({
    mutate: mocks.recordActivationEvent,
  }),
}));

vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: {
    CREATE: "CREATE",
  },
  RolePermission: {
    SIMULATION_AGENT: {
      CREATE: {
        admin: true,
      },
    },
  },
}));

vi.mock("./constants", () => ({
  AGENT_TYPES: {
    CHAT: "chat",
    VOICE: "voice",
  },
  isLiveKitProvider: () => false,
}));

vi.mock("src/utils/axios", () => ({
  default: {
    post: vi.fn(),
  },
  endpoints: {
    agentDefinitions: {
      create: "/agent-definitions",
    },
  },
}));

vi.mock("notistack", () => ({
  useSnackbar: () => ({
    enqueueSnackbar: vi.fn(),
  }),
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: {
    createAgentDefClicked: "createAgentDefClicked",
  },
  PropertyName: {
    formFields: "formFields",
  },
  trackEvent: vi.fn(),
}));

vi.mock("src/utils/logger", () => ({
  default: {
    error: vi.fn(),
  },
}));

vi.mock("./CreateNewAgent/StepsTracker", () => ({
  default: () => <div>Steps tracker</div>,
}));

vi.mock("./CreateNewAgent/AgentBasicInfoStep/AgentBasicInfoStep", () => ({
  default: () => <div>Basic info form</div>,
}));

vi.mock(
  "./CreateNewAgent/AgentConfigurationStep/AgentConfigurationStep",
  () => ({
    default: () => <div>Connection form</div>,
  }),
);

vi.mock("./CreateNewAgent/AgentBehaviourStep/AgentBehaviourStep", () => ({
  default: () => <div>Behavior form</div>,
}));

vi.mock(
  "./CreateNewAgent/AgentBasicInfoStep/AgentBasicInfoStepRightSection",
  () => ({
    default: () => <div>Basic info help</div>,
  }),
);

vi.mock(
  "./CreateNewAgent/AgentConfigurationStep/AgentConfigurationStepRightSection",
  () => ({
    default: () => <div>Connection help</div>,
  }),
);

vi.mock(
  "./CreateNewAgent/AgentBehaviourStep/AgentBehaviourStepRightSection",
  () => ({
    default: () => <div>Behavior help</div>,
  }),
);

vi.mock("src/components/svg-color", () => ({
  default: (props) => <span data-testid="svg-color" {...props} />,
}));

vi.mock("src/components/iconify", () => ({
  default: (props) => <span data-testid="iconify" {...props} />,
}));

describe("CreateNewAgentDefinitionView voice onboarding", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows voice-specific setup guidance instead of generic agent-definition framing", async () => {
    render(<CreateNewAgentDefinitionView />);

    expect(screen.getByText("Voice setup")).toBeVisible();
    expect(screen.getByText("Create the voice agent")).toBeVisible();
    expect(screen.getByText("Voice agents")).toBeVisible();
    expect(screen.getAllByText("Create voice agent").length).toBeGreaterThan(0);
    expect(screen.queryByText("All Agent Definitions")).not.toBeInTheDocument();

    await waitFor(() =>
      expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          eventName: "onboarding_voice_route_focus_viewed",
          primaryPath: "voice",
          stage: "create_voice_agent",
        }),
      ),
    );
  });
});
