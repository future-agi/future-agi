import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import TestEvaluationPage from "../TestEvaluationPage";
import { TEST_ONBOARDING_MODES } from "../testOnboardingModes";

const mocks = vi.hoisted(() => ({
  onAddEvaluation: vi.fn(),
  onClose: vi.fn(),
  queryClient: {
    getQueryData: vi.fn(),
    invalidateQueries: vi.fn(),
    setQueryData: vi.fn(),
  },
  selectedCount: 0,
  testData: {
    enableToolEvaluation: false,
    simulate_eval_configs_detail: [],
  },
  updateTestRuns: vi.fn(),
}));

vi.mock("react-router", () => ({
  useParams: () => ({
    testId: "test-1",
  }),
}));

vi.mock("@tanstack/react-query", () => ({
  useMutation: () => ({
    isPending: false,
    mutate: vi.fn(),
  }),
  useQueryClient: () => mocks.queryClient,
}));

vi.mock("src/utils/axios", () => ({
  default: {
    delete: vi.fn(),
    post: vi.fn(),
  },
  endpoints: {
    runTests: {
      deleteEvals: () => "/run-tests/delete-eval",
      runEvals: () => "/run-tests/run-evals",
    },
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "admin" }),
}));

vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: {
    EDIT_CREATE_DELETE_EVALS: "EDIT_CREATE_DELETE_EVALS",
  },
  RolePermission: {
    EVALS: {
      EDIT_CREATE_DELETE_EVALS: {
        admin: true,
      },
    },
  },
}));

vi.mock("src/hooks/useTestRunDetails", () => ({
  default: () => ({
    data: mocks.testData,
    loading: { isPending: false },
  }),
}));

vi.mock("../context/TestDetailContext", () => ({
  useTestDetailContext: () => ({
    refreshTestRunGrid: vi.fn(),
  }),
}));

vi.mock("../states", () => ({
  useTestRunsGridStoreShallow: (selector) =>
    selector({
      selectAll: false,
      setSelectAll: vi.fn(),
      setToggledNodes: vi.fn(),
      toggledNodes: [],
    }),
}));

vi.mock("../common", () => ({
  useTestRunsSelectedCount: () => mocks.selectedCount,
}));

vi.mock("src/api/tests/testRuns", () => ({
  useUpdateTestRuns: () => ({
    mutate: mocks.updateTestRuns,
  }),
}));

vi.mock("../TestRuns/states", () => ({
  useSelectedAgentDefinitionStore: () => ({
    setSelectedAgentDefinitionVersion: vi.fn(),
  }),
}));

vi.mock("../TestRuns/common", () => ({
  ComponentApiMapping: {
    ToolEvaluationApiKey: "tool_eval_api_key",
  },
}));

vi.mock("../../common/EvaluationDrawer/SavedEvalsSkeleton", () => ({
  default: () => <div>Loading saved evals</div>,
}));

vi.mock("../../common/EvaluationDrawer/SavedEvalsList", () => ({
  default: ({ evals }) => (
    <div>
      {evals.map((evalItem) => (
        <div key={evalItem.id}>{evalItem.name}</div>
      ))}
    </div>
  ),
}));

vi.mock("../../common/EvaluationDrawer/ConfirmRunEvaluations", () => ({
  default: ({ open }) =>
    open ? <div role="dialog">Confirm run evaluations</div> : null,
}));

vi.mock("src/components/custom-dialog", () => ({
  ConfirmDialog: () => null,
}));

vi.mock("../../agents/AgentConfiguration/UpdateKeysDialog", () => ({
  default: () => null,
}));

vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => <>{children}</>,
}));

vi.mock("src/components/iconify", () => ({
  default: (props) => <span data-testid="iconify" {...props} />,
}));

vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: {
    runTestAddEvalClicked: "runTestAddEvalClicked",
  },
  PropertyName: {
    id: "id",
  },
  trackEvent: vi.fn(),
}));

vi.mock("src/utils/logger", () => ({
  default: {
    debug: vi.fn(),
    info: vi.fn(),
  },
}));

describe("TestEvaluationPage onboarding focus", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.selectedCount = 0;
    mocks.testData = {
      enableToolEvaluation: false,
      simulate_eval_configs_detail: [],
    };
  });

  it("shows one add-evaluation action before an eval exists", async () => {
    render(
      <TestEvaluationPage
        onboardingMode={TEST_ONBOARDING_MODES.CREATE_EVAL}
        onAddEvaluation={mocks.onAddEvaluation}
        onClose={mocks.onClose}
      />,
    );

    expect(screen.getByTestId("test-onboarding-focus")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /^add evaluation$/i }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /run evaluation/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /cancel/i }),
    ).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: /^add evaluation$/i }),
    );

    expect(mocks.onAddEvaluation).toHaveBeenCalledTimes(1);
  });

  it("can render voice success criteria copy in the shared eval drawer", async () => {
    render(
      <TestEvaluationPage
        onboardingMode={TEST_ONBOARDING_MODES.CREATE_EVAL}
        onboardingAddLabel="Add success criteria"
        onboardingCopy={{
          title: "Add voice success criteria",
          description:
            "Add one success criterion so future voice calls can be scored after each run.",
        }}
        onboardingCurrentStep="Success criteria"
        onboardingEyebrow="Voice setup"
        onboardingSteps={[
          { label: "Test call", complete: true },
          { label: "Review call", complete: true },
          { label: "Success criteria", complete: false },
        ]}
        onAddEvaluation={mocks.onAddEvaluation}
        onClose={mocks.onClose}
      />,
    );

    expect(screen.getByText("Voice setup")).toBeVisible();
    expect(screen.queryByText("Eval setup")).not.toBeInTheDocument();
    expect(screen.getByText("Add voice success criteria")).toBeVisible();
    expect(screen.getByText("Step 3 of 3")).toBeVisible();
    expect(screen.getByText("Test call")).toBeVisible();
    expect(screen.getByText("Review call")).toBeVisible();

    await userEvent.click(
      screen.getByRole("button", { name: /add success criteria/i }),
    );

    expect(mocks.onAddEvaluation).toHaveBeenCalledTimes(1);
  });

  it("shows one run-evaluation action after an eval exists", async () => {
    mocks.selectedCount = 1;
    mocks.testData = {
      enableToolEvaluation: false,
      simulate_eval_configs_detail: [
        {
          id: "eval-1",
          name: "First evaluator",
        },
      ],
    };

    render(
      <TestEvaluationPage
        onboardingMode={TEST_ONBOARDING_MODES.SAVE_EVAL}
        onAddEvaluation={mocks.onAddEvaluation}
        onClose={mocks.onClose}
      />,
    );

    expect(screen.getByText("First evaluator")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /^run evaluation$/i }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /add another evaluation/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /cancel/i }),
    ).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: /^run evaluation$/i }),
    );

    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Confirm run evaluations",
    );
  });
});
