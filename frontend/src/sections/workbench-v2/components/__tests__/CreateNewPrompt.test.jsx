import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent } from "src/utils/test-utils";
import CreateNewPrompt from "../CreateNewPrompt";

// Fixture mirrors the REAL backend create-draft response: a {status,result}
// envelope with snake_case keys. The id lives under `result.root_template`
// (there is NO camelCase `rootTemplate`). This is the exact shape that broke
// TH-5824 / TH-5865 when the UI read `result.rootTemplate` and navigated to
// /create/undefined.
const ROOT_TEMPLATE_ID = "f0ec1d31-626f-4220-a04a-3ec9b9d8acf3";
const CREATE_DRAFT_RESPONSE = {
  data: {
    status: true,
    result: {
      id: ROOT_TEMPLATE_ID,
      template_version: "v1",
      root_template: ROOT_TEMPLATE_ID,
      original_template: ROOT_TEMPLATE_ID,
      is_draft: true,
      name: "Untitled-44",
    },
  },
};

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  navigate: vi.fn(),
  setSelectTemplateDrawerOpen: vi.fn(),
}));

vi.mock("react-router", async (importActual) => {
  const actual = await importActual();
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
    useParams: () => ({ folder: "all" }),
  };
});

vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: {
    develop: {
      runPrompt: {
        createPromptDraft: "/model-hub/prompt-templates/create-draft/",
      },
    },
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));

vi.mock("src/sections/workbench-v2/store/usePromptStore", () => ({
  usePromptStore: () => ({
    setSelectTemplateDrawerOpen: mocks.setSelectTemplateDrawerOpen,
    selectTemplateDrawerOpen: false,
  }),
}));

vi.mock("src/components/iconify", () => ({
  default: () => <span data-testid="iconify" />,
}));
vi.mock("src/components/svg-color", () => ({
  default: () => <span data-testid="svg-color" />,
}));

function renderModal(props = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <CreateNewPrompt open onClose={vi.fn()} isLoading={false} {...props} />
    </QueryClientProvider>,
  );
}

describe("CreateNewPrompt — create-draft navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.post.mockResolvedValue(CREATE_DRAFT_RESPONSE);
  });

  it("POSTs to create-draft when an authoring mode is chosen", async () => {
    renderModal();
    fireEvent.click(screen.getByText("Start from scratch"));

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/model-hub/prompt-templates/create-draft/",
        expect.any(Object),
      ),
    );
  });

  it("navigates to the new prompt using result.root_template", async () => {
    renderModal();
    fireEvent.click(screen.getByText("Start from scratch"));

    await waitFor(() =>
      expect(mocks.navigate).toHaveBeenCalledWith(
        `/dashboard/workbench/create/${ROOT_TEMPLATE_ID}`,
        expect.objectContaining({ state: expect.any(Object) }),
      ),
    );
  });

  it("never navigates to an undefined id (guards the camelCase regression)", async () => {
    renderModal();
    fireEvent.click(screen.getByText("Start from scratch"));

    await waitFor(() => expect(mocks.navigate).toHaveBeenCalled());
    const targetUrl = mocks.navigate.mock.calls[0][0];
    expect(targetUrl).toContain(ROOT_TEMPLATE_ID);
    expect(targetUrl).not.toContain("undefined");
  });
});
