import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import TestRunHeader from "../TestRunHeader";

const mocks = vi.hoisted(() => ({
  locationSearch: "",
  navigate: vi.fn(),
  recordActivationEvent: vi.fn(),
  role: "admin",
  runTest: vi.fn(),
  search: "",
  selectedScenarios: ["scenario-1"],
  setScenarioPopoverOpen: vi.fn(),
  setSdkCodeOpen: vi.fn(),
  setSearch: vi.fn(),
  setSelectedScenarios: vi.fn(),
  testData: {
    agent_definition: "agent-1",
    agent_definition_detail: {
      agent_type: "voice",
    },
    scenarios: ["scenario-1"],
    scenarios_detail: [{ id: "scenario-1", status: "Completed" }],
    source_type: "voice",
  },
}));

vi.mock("react-router", () => ({
  useLocation: () => ({
    search: mocks.locationSearch,
  }),
  useNavigate: () => mocks.navigate,
  useParams: () => ({
    testId: "test-1",
  }),
}));

vi.mock("@tanstack/react-query", () => ({
  useMutation: () => ({
    isPending: false,
    mutate: mocks.runTest,
  }),
}));

vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: ({ onChange, placeholder, searchQuery }) => (
    <input
      aria-label={placeholder}
      onChange={onChange}
      placeholder={placeholder}
      value={searchQuery}
    />
  ),
}));

vi.mock("src/components/iconify", () => ({
  default: (props) => <span data-testid="iconify" {...props} />,
}));

vi.mock("src/components/svg-color", () => ({
  default: (props) => <span data-testid="svg-color" {...props} />,
}));

vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children }) => <>{children}</>,
}));

vi.mock("src/components/show", () => ({
  ShowComponent: ({ children, condition }) => (condition ? children : null),
}));

vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    post: vi.fn(),
  },
  endpoints: {
    promptSimulation: {
      execute: () => "/prompt/run",
    },
    runTests: {
      runTest: () => "/voice/run",
    },
  },
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: {
    runTestRuntestClicked: "runTestRuntestClicked",
  },
  PropertyName: {
    id: "id",
  },
  trackEvent: vi.fn(),
}));

vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: {
    RUN_SIMULATION_TEST: "RUN_SIMULATION_TEST",
  },
  RolePermission: {
    SIMULATION_AGENT: {
      RUN_SIMULATION_TEST: {
        admin: true,
      },
    },
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    role: mocks.role,
  }),
}));

vi.mock("../../context/TestDetailContext", () => ({
  useTestDetailContext: () => ({
    refreshTestRunGrid: vi.fn(),
    testData: mocks.testData,
  }),
}));

vi.mock("../states", () => ({
  useSelectedScenariosStore: () => ({
    selectedScenarios: mocks.selectedScenarios,
    setSelectedScenarios: mocks.setSelectedScenarios,
  }),
  useTestRunsSearchStore: () => ({
    search: mocks.search,
    setSearch: mocks.setSearch,
  }),
}));

vi.mock("../state", () => ({
  useTestRunSdkStoreShallow: (selector) =>
    selector({
      setSdkCodeOpen: mocks.setSdkCodeOpen,
    }),
}));

vi.mock("../../common", () => ({
  useTestRunsSelectedCount: () => 0,
}));

vi.mock("src/sections/onboarding-home/hooks/useRecordActivationEvent", () => ({
  useRecordActivationEvent: () => ({
    mutate: mocks.recordActivationEvent,
  }),
}));

vi.mock("../ScenarioPopover", () => ({
  default: () => null,
}));

vi.mock("../TestRunsSelection", () => ({
  default: () => null,
}));

vi.mock("../NewVoiceSimulationDrawer", () => ({
  default: () => null,
}));

describe("TestRunHeader voice onboarding", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.locationSearch = "";
    mocks.navigate = vi.fn();
    mocks.role = "admin";
    mocks.search = "";
    mocks.selectedScenarios = ["scenario-1"];
    mocks.testData = {
      agent_definition: "agent-1",
      agent_definition_detail: {
        agent_type: "voice",
      },
      scenarios: ["scenario-1"],
      scenarios_detail: [{ id: "scenario-1", status: "Completed" }],
      source_type: "voice",
    };
  });

  it("shows one voice test-call action during guided voice setup", async () => {
    mocks.locationSearch =
      "?from=onboarding&onboarding=run-test-call&agent_definition_id=agent-1&tour_anchor=voice_run_button";

    render(<TestRunHeader />);

    expect(screen.getByText("Voice setup")).toBeVisible();
    expect(screen.getByText("Run a voice test call")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /^run test call$/i }),
    ).toHaveAttribute("data-tour-anchor", "voice_run_button");
    expect(
      screen.queryByRole("button", { name: /run new simulation/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /scenarios/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /github actions/i }),
    ).not.toBeInTheDocument();

    await waitFor(() =>
      expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          eventName: "onboarding_voice_route_focus_viewed",
          primaryPath: "voice",
          stage: "run_voice_test_call",
        }),
      ),
    );
    expect(mocks.setSdkCodeOpen).toHaveBeenCalledWith(true);

    await userEvent.click(
      screen.getByRole("button", { name: /^run test call$/i }),
    );

    expect(mocks.runTest).toHaveBeenCalledTimes(1);
  });

  it("keeps the normal run controls outside guided voice setup", async () => {
    render(<TestRunHeader />);

    expect(screen.queryByText("Voice setup")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /run new simulation/i }),
      ).toBeVisible(),
    );
    expect(screen.getByRole("button", { name: /scenarios/i })).toBeVisible();
    expect(
      screen.getByRole("button", { name: /github actions/i }),
    ).toHaveTextContent("GitHub Actions");
  });

  it("shows a simulation source-fix handoff before rerunning the quality check", async () => {
    mocks.locationSearch =
      "?source=onboarding&step=fix-eval-failure&source_type=simulation&source_id=test-1&eval_id=eval-1&run_id=run-1&quick_start_goal=evaluate_quality&quick_start_id=evals&quick_start_primary_path=evals";

    render(<TestRunHeader />);

    expect(screen.getByText("Simulation / Evals")).toBeVisible();
    expect(screen.getByText("Fix the simulation source")).toBeVisible();
    expect(
      screen.getByText(
        "Update the simulation scenario or expected behavior that produced the weak result, then rerun the quality check.",
      ),
    ).toBeVisible();

    await waitFor(() =>
      expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          eventName: "onboarding_eval_source_fix_route_viewed",
          primaryPath: "evals",
          stage: "eval_next_loop",
          metadata: expect.objectContaining({
            eval_id: "eval-1",
            run_id: "run-1",
            source_id: "test-1",
            source_type: "simulation",
          }),
        }),
      ),
    );

    await userEvent.click(
      screen.getByRole("button", { name: /^rerun quality check$/i }),
    );

    expect(mocks.recordActivationEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: "onboarding_eval_source_fix_rerun_clicked",
        metadata: expect.objectContaining({
          eval_id: "eval-1",
          rerun_route:
            "/dashboard/evaluations/create/eval-1?source=onboarding&step=run&source_type=simulation&source_id=test-1&rerun_from=source_fix&previous_run_id=run-1&quick_start_goal=evaluate_quality&quick_start_id=evals&quick_start_primary_path=evals",
          run_id: "run-1",
          source_id: "test-1",
          source_type: "simulation",
        }),
      }),
      expect.objectContaining({
        onSettled: expect.any(Function),
      }),
    );
  });
});
