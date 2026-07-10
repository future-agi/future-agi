import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
} from "src/utils/test-utils";
import EvalCreatePage from "../EvalCreatePage";

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  axiosGet: vi.fn(),
  updateMutate: vi.fn(),
  updateMutateAsync: vi.fn(),
}));

vi.mock("react-router", async (importActual) => {
  const actual = await importActual();
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
    useParams: () => ({ draftId: "draft-1" }),
  };
});

vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "owner" }),
}));

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false }),
}));

vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { EDIT_CREATE_DELETE_EVALS: "edit" },
  RolePermission: { EVALS: { edit: { owner: true } } },
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mocks.axiosGet(...args),
    post: vi.fn(),
  },
  endpoints: {
    develop: {
      eval: {
        getEvalDetail: (id) => `/evals/${id}`,
        createEvalTemplateV2: "/evals/",
      },
    },
  },
}));

vi.mock("../../hooks/useCreateEval", () => ({
  useCreateEval: () => ({ isLoading: false }),
}));

vi.mock("../../hooks/useEvalDetail", () => ({
  useUpdateEval: () => ({
    isLoading: false,
    mutate: mocks.updateMutate,
    mutateAsync: mocks.updateMutateAsync,
  }),
}));

vi.mock("../../hooks/useCompositeEval", () => ({
  useCreateCompositeEval: () => ({ isLoading: false, mutateAsync: vi.fn() }),
}));

vi.mock("../../hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenUnionKeys: () => [],
}));

vi.mock("../../Helpers/compositeRuntimeConfig", () => ({
  buildCompositeChildConfigs: () => [],
}));

vi.mock("src/sections/common/EvalPicker/evalPickerConfigUtils", () => ({
  buildDataInjection: () => ({}),
}));

vi.mock("src/utils/utils", () => ({
  extractVariables: () => [],
  extractVariablesFromMessages: () => [],
}));

vi.mock("../ModelSelector", () => ({
  FAGI_MODEL_VALUES: new Set(),
}));

vi.mock("../InstructionEditor", () => ({ default: () => null }));
vi.mock("../LLMPromptEditor", () => ({ default: () => null }));
vi.mock("../FewShotExamples", () => ({ default: () => null }));
vi.mock("../OutputTypeConfig", () => ({ default: () => null }));
vi.mock("../CodeEvalEditor", () => ({
  default: () => null,
  PYTHON_CODE_TEMPLATE: "def evaluate():\n    return 1",
}));
vi.mock("../CompositeDetailPanel", () => ({ default: () => null }));
vi.mock("../TestPlayground", async () => {
  const ReactModule = await import("react");
  const TestPlaygroundMock = ReactModule.forwardRef(
    function TestPlaygroundMock() {
      return null;
    },
  );
  return { default: TestPlaygroundMock };
});
vi.mock("src/sections/projects/MonitorsView/UnsavedChangesDialog", () => ({
  default: () => null,
}));
vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));
vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  ),
}));

describe("Unit: EvalCreatePage custom tags", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.axiosGet.mockResolvedValue({
      data: {
        result: {
          id: "draft-1",
          eval_type: "agent",
          eval_tags: [],
          config: {},
        },
      },
    });
  });

  it("adds a trimmed custom tag when Enter is pressed", async () => {
    const user = userEvent.setup();
    render(<EvalCreatePage />);

    await user.click(screen.getByRole("button", { name: /advanced/i }));
    const input = screen.getByRole("textbox", { name: "Add custom tag" });

    await user.type(input, "  client-demo{Enter}");

    expect(screen.getByText("client-demo")).toBeInTheDocument();
    expect(input).toHaveValue("");
  });

  it("does not add a custom tag that is already selected", async () => {
    const user = userEvent.setup();
    render(<EvalCreatePage />);

    await user.click(screen.getByRole("button", { name: /advanced/i }));
    const input = screen.getByPlaceholderText("Add custom tag...");

    await user.type(input, "client-demo{Enter}");
    await user.type(input, "client-demo{Enter}");

    expect(screen.getAllByText("client-demo")).toHaveLength(1);
  });

  it("ignores whitespace-only custom tags", async () => {
    const user = userEvent.setup();
    render(<EvalCreatePage />);

    const advancedButton = screen.getByRole("button", { name: /advanced/i });
    await user.click(advancedButton);
    await user.type(
      screen.getByPlaceholderText("Add custom tag..."),
      "   {Enter}",
    );

    expect(advancedButton).not.toHaveTextContent("1 tags");
  });

  it("shows custom tags loaded from an existing evaluation", async () => {
    mocks.axiosGet.mockResolvedValueOnce({
      data: {
        result: {
          id: "draft-1",
          eval_type: "agent",
          eval_tags: ["client-demo"],
          config: {},
        },
      },
    });
    const user = userEvent.setup();
    render(<EvalCreatePage />);

    await user.click(screen.getByRole("button", { name: /advanced/i }));

    expect(await screen.findByText("client-demo")).toBeInTheDocument();
  });

  it("does not add a tag when Enter confirms IME composition", async () => {
    const user = userEvent.setup();
    render(<EvalCreatePage />);

    await user.click(screen.getByRole("button", { name: /advanced/i }));
    const input = screen.getByRole("textbox", { name: "Add custom tag" });
    await user.type(input, "client");

    fireEvent.keyDown(input, {
      key: "Enter",
      nativeEvent: { isComposing: true },
      isComposing: true,
    });

    expect(screen.queryByText("client")).not.toBeInTheDocument();
    expect(input).toHaveValue("client");
  });

  it("includes pending custom tag input when saving the evaluation", async () => {
    const user = userEvent.setup();
    render(<EvalCreatePage />);

    await user.type(
      screen.getByPlaceholderText("Eg: Hallucination detector"),
      "Client evaluation",
    );
    await user.click(screen.getByRole("tab", { name: "Code" }));
    await user.click(screen.getByRole("button", { name: /advanced/i }));
    await user.type(
      screen.getByRole("textbox", { name: "Add custom tag" }),
      "pending-tag",
    );
    await user.click(screen.getByRole("button", { name: "Save Evaluation" }));

    await waitFor(() => {
      expect(mocks.updateMutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({ tags: ["pending-tag"], publish: true }),
      );
    });
  });

  it("includes custom tags when saving the evaluation", async () => {
    const user = userEvent.setup();
    render(<EvalCreatePage />);

    await user.type(
      screen.getByPlaceholderText("Eg: Hallucination detector"),
      "Client evaluation",
    );
    await user.click(screen.getByRole("tab", { name: "Code" }));
    await user.click(screen.getByRole("button", { name: /advanced/i }));
    await user.type(
      screen.getByPlaceholderText("Add custom tag..."),
      "client-demo{Enter}",
    );
    await user.click(screen.getByRole("button", { name: "Save Evaluation" }));

    await waitFor(() => {
      expect(mocks.updateMutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({ tags: ["client-demo"], publish: true }),
      );
    });
  });
});
