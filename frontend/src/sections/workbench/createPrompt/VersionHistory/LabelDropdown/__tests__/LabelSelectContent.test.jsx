import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent } from "src/utils/test-utils";
import LabelSelectContent from "../LabelSelectContent";

const mocks = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: {
    develop: {
      runPrompt: {
        assignMultipleLabels:
          "/model-hub/prompt-labels/assign-multiple-labels/",
        createPromptLabel: "/model-hub/prompt-labels/",
      },
    },
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "admin" }),
}));

vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { DEPLOY: "DEPLOY" },
  RolePermission: { PROMPTS: { DEPLOY: { admin: true } } },
}));

// Forward MUI-injected className/onClick so the chip delete icon is clickable.
vi.mock("src/components/iconify", () => ({
  default: ({ className, onClick }) => (
    <span className={className} onClick={onClick} data-testid="iconify" />
  ),
}));

const ALPHA = { id: "a", name: "alpha" };
const BETA = { id: "b", name: "beta" };

const renderContent = (props = {}) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <LabelSelectContent
        promptId="prompt-1"
        versionId="version-1"
        labels={[ALPHA, BETA]}
        version={{ id: "version-1", template_version: "v1" }}
        isPending={false}
        isFetchingNextPage={false}
        fetchNextPage={vi.fn()}
        {...props}
      />
    </QueryClientProvider>,
  );
};

const saveButton = () => screen.getByRole("button", { name: /save/i });
const deleteIcons = (container) =>
  container.querySelectorAll(".MuiChip-deleteIcon");

describe("LabelSelectContent — Save button (TH-5894)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.post.mockResolvedValue({ data: {} });
  });

  it("disables Save when there were no labels and none are selected", () => {
    renderContent({ selectedLabels: [] });
    expect(saveButton()).toBeDisabled();
  });

  it("enables Save whenever the selection is non-empty", () => {
    renderContent({ selectedLabels: [ALPHA] });
    expect(saveButton()).toBeEnabled();
  });

  it("disables Save when the only tag is removed — empty selection cannot be submitted", () => {
    const { container } = renderContent({ selectedLabels: [ALPHA] });
    expect(saveButton()).toBeEnabled();

    fireEvent.click(deleteIcons(container)[0]);

    expect(saveButton()).toBeDisabled();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("submits the remaining ids when one of several tags is removed", async () => {
    const { container } = renderContent({ selectedLabels: [ALPHA, BETA] });

    // remove the first chip (alpha), leaving beta
    fireEvent.click(deleteIcons(container)[0]);

    expect(saveButton()).toBeEnabled();
    fireEvent.click(saveButton());

    await waitFor(() => {
      expect(mocks.post).toHaveBeenCalledWith(
        "/model-hub/prompt-labels/assign-multiple-labels/",
        { template_version_id: "version-1", label_ids: ["b"] },
      );
    });
  });
});
