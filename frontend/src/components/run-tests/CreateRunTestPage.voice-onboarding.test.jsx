/* eslint-disable react/prop-types */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import CreateRunTestPage from "./CreateRunTestPage";

const mocks = vi.hoisted(() => ({
  agentDefinitions: [
    {
      agentName: "Checkout voice agent",
      agentType: "voice",
      id: "agent-1",
    },
  ],
  agentVersions: {
    pages: [
      {
        results: [{ id: "version-1", versionNameDisplay: "v1" }],
      },
    ],
  },
  columnConfigData: { data: { columnConfigs: [] } },
  locationSearch:
    "?from=onboarding&onboarding=create-test-call&agent_definition_id=agent-1&tour_anchor=voice_test_call_button&quick_start_goal=connect_voice_ai_agent&quick_start_id=voice&quick_start_primary_path=voice",
  navigate: vi.fn(),
  push: vi.fn(),
  scenariosData: {
    count: 1,
    results: [
      {
        id: "scenario-1",
        name: "Checkout call",
        description: "First checkout support call",
        datasetRows: 1,
      },
    ],
  },
  versionDetailData: {
    configuration_snapshot: {
      livekitAgentName: "voice-agent",
      livekitApiKey: "key",
      livekitApiSecret: "secret",
      livekitUrl: "wss://voice.example",
      provider: "livekit",
    },
  },
}));

vi.mock("react-router", () => ({
  useLocation: () => ({ search: mocks.locationSearch }),
  useNavigate: () => mocks.navigate,
}));

vi.mock("src/routes/hooks", () => ({
  useRouter: () => ({ push: mocks.push }),
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({
    invalidateQueries: vi.fn(),
  }),
  useMutation: (options = {}) => ({
    isPending: false,
    mutate: (payload) => options.onSuccess?.({ id: "test-1" }, payload),
  }),
  useQuery: ({ queryKey }) => {
    if (queryKey?.[0] === "scenarios") {
      return {
        data: mocks.scenariosData,
        error: null,
        isLoading: false,
      };
    }
    if (queryKey?.[0] === "get-scenario-column-configs") {
      return { data: mocks.columnConfigData };
    }
    if (queryKey?.[0] === "agentVersionDetail") {
      return {
        data: mocks.versionDetailData,
      };
    }
    return { data: null, error: null, isLoading: false };
  },
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
  endpoints: {
    agentDefinitions: {
      versionDetail: () => "/agent-version-detail",
    },
    runTests: {
      create: "/run-tests/create",
    },
    scenarios: {
      getColumns: "/scenarios/columns",
      list: "/scenarios",
    },
  },
}));

vi.mock("./common", () => ({
  chatEvalColumns: [],
  getVersionedEvalName: (name) => name || "criterion",
  useAgentDefinitions: () => ({
    agentDefinitions: mocks.agentDefinitions,
    fetchNextPage: vi.fn(),
    isFetchingNextPage: false,
    isLoading: false,
  }),
  voiceEvalColumns: [],
}));

vi.mock("src/api/agent-definition/agent-definition-version", () => ({
  useAgentDefinitionVersions: () => ({
    data: mocks.agentVersions,
    fetchNextPage: vi.fn(),
    isFetchingNextPage: false,
    isLoading: false,
  }),
}));

vi.mock("src/sections/test/common", () => ({
  useScenarioColumnConfig: (columns) => columns,
}));

vi.mock("src/sections/common/EvalPicker", () => ({
  EvalPickerDrawer: () => null,
  serializeEvalConfig: (config) => config,
}));

vi.mock("src/sections/agents/AgentConfiguration/UpdateKeysDialog", () => ({
  default: () => null,
}));

vi.mock("../FromSearchSelectField", () => ({
  FormSearchSelectFieldState: ({ createLabel, label, options = [], value }) => (
    <label>
      {label}
      <select aria-label={label} value={value} readOnly>
        <option value="">{createLabel}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  ),
}));

vi.mock("../EmptyLayout/EmptyLayout", () => ({
  default: ({ title }) => <div>{title}</div>,
}));

vi.mock("../show", () => ({
  ShowComponent: ({ children, condition }) => (condition ? children : null),
}));

vi.mock("../svg-color", () => ({
  default: (props) => <span data-testid="svg-color" {...props} />,
}));

vi.mock("src/components/iconify", () => ({
  default: (props) => <span data-testid="iconify" {...props} />,
}));

vi.mock("../tooltip", () => ({
  default: ({ children }) => <>{children}</>,
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: {
    runTestCreateTestClicked: "runTestCreateTestClicked",
  },
  PropertyName: {
    formFields: "formFields",
  },
  trackEvent: vi.fn(),
}));

vi.mock("notistack", () => ({
  useSnackbar: () => ({
    enqueueSnackbar: vi.fn(),
  }),
}));

describe("CreateRunTestPage voice onboarding", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the voice-specific test-call setup instead of generic simulation copy", () => {
    render(
      <CreateRunTestPage
        open
        onClose={vi.fn()}
        initialAgentDefinitionId="agent-1"
        initialAgentType="voice"
      />,
    );

    expect(screen.getByText("Create voice test call")).toBeVisible();
    expect(screen.getByText("Voice setup")).toBeVisible();
    expect(screen.getByText("Create the first voice test call")).toBeVisible();
    expect(screen.getAllByText("Name test call").length).toBeGreaterThan(0);
    expect(screen.getByDisplayValue("First voice test call")).toBeVisible();
    expect(screen.getByLabelText("Voice agent")).toBeVisible();
    expect(screen.queryByText("Run Simulation")).not.toBeInTheDocument();
  });

  it("lets the first voice test call move to review without pre-adding success criteria", async () => {
    const user = userEvent.setup();

    render(
      <CreateRunTestPage
        open
        onClose={vi.fn()}
        initialAgentDefinitionId="agent-1"
        initialAgentType="voice"
      />,
    );

    await user.click(screen.getByRole("button", { name: /^next$/i }));
    await screen.findByText("Choose your scenarios");

    await user.click(screen.getByText("Checkout call"));
    await user.click(screen.getByRole("button", { name: /^next$/i }));
    await screen.findByText("Confirm the review path");

    await user.click(screen.getByRole("button", { name: /^next$/i }));

    await waitFor(() =>
      expect(screen.getByText("Review test call setup")).toBeVisible(),
    );
  });
});
