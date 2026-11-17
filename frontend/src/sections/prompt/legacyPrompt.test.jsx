import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";

import AddNewPromptView from "./NewPrompt/AddNewPrompt";
import PromtLeftSidebar from "./PromtSidebar/PromtLeftSidebar";
import PromptRightSidebar from "./PromtSidebar/PromptRightSidebar";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  topMenu: vi.fn(() => null),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    develop: {
      runPrompt: {
        createPromptDraft: "/prompt-templates/create-draft/",
        promptExecutions: () => "/prompt-templates/",
        getPromptVersions: (id) => `/prompt-templates/${id}/versions/`,
        getPrompt: (id) => `/prompt-templates/${id}/`,
      },
    },
  },
}));
vi.mock("src/routes/hooks", async () => ({
  useParams: (await import("react-router")).useParams,
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/utils/logger", () => ({ default: { error: vi.fn() } }));
vi.mock("src/utils/utils", () => ({ getRandomId: () => "message-id" }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/custom-dialog/confirm-dialog", () => ({ default: () => null }));
vi.mock("src/hooks/use-scroll-end", () => ({ useScrollEnd: () => null }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("src/hooks/use-drawer-stack", () => ({
  useDrawerCovered: () => false,
  coveredDrawerSx: () => ({}),
}));
vi.mock("./PromptDrawer/GeneratePrompt", () => ({ default: () => null }));
vi.mock("./NewPrompt/ImprovePrompt/ImprovePrompt", () => ({ default: () => null }));
vi.mock("./NewPrompt/TopMenu", () => ({ default: mocks.topMenu }));
vi.mock("./NewPrompt/PromptGenerate/Results", () => ({ default: () => null }));
vi.mock("./NewPrompt/PromptGenerate/Workbench", async () => {
  const { useWatch } = await import("react-hook-form");
  const { default: PropTypes } = await import("prop-types");
  function Messages({ control }) {
    const messages = useWatch({ control, name: "config.messages" });
    return <pre data-testid="messages">{JSON.stringify(messages)}</pre>;
  }
  Messages.propTypes = { control: PropTypes.object.isRequired };
  return { default: Messages };
});

const TEMPLATE_ID = "f0ec1d31-626f-4220-a04a-3ec9b9d8acf3";
const EDITOR_PATH = `/dashboard/prompt/add/${TEMPLATE_ID}`;
// The backend emits snake_case only, without response-key conversion in Axios.
const CREATE_RESPONSE = {
  data: {
    status: true,
    result: {
      id: TEMPLATE_ID,
      root_template: TEMPLATE_ID,
      name: "Untitled-44",
      template_version: "v1",
      is_draft: true,
    },
  },
};
const MESSAGES = [
  { role: "user", content: [{ type: "text", text: "Hello {{ name }}" }] },
];
const CONFIGURATION = { model: "test-model", temperature: 0.4, template_format: "jinja" };
const TEMPLATE = {
  id: TEMPLATE_ID,
  name: "Existing prompt",
  version: "v2",
  is_draft: true,
  variable_names: { name: ["Ada"] },
  prompt_config: [{ messages: MESSAGES, configuration: CONFIGURATION }],
};

function CurrentPath() {
  return <output data-testid="path">{useLocation().pathname}</output>;
}

const clients = [];
function renderLegacyRoute(element, initialPath = "/dashboard/prompt") {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <CurrentPath />
        <Routes>
          <Route path="/dashboard/prompt" element={element} />
          <Route path="/dashboard/prompt/add/:id" element={<AddNewPromptView />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function expectHydrated() {
  await waitFor(() => {
    expect(screen.getByTestId("path").textContent).toBe(EDITOR_PATH);
    expect(mocks.topMenu.mock.lastCall?.[0].initialConfig).toEqual(CONFIGURATION);
    expect(mocks.topMenu.mock.lastCall?.[0].templateFormat).toBe("jinja");
    expect(mocks.topMenu.mock.lastCall?.[0].currentTitle).toBe(TEMPLATE.name);
    expect(mocks.topMenu.mock.lastCall?.[0].appliedVariableData).toEqual(TEMPLATE.variable_names);
    expect(JSON.parse(screen.getByTestId("messages").textContent)).toEqual([
      expect.objectContaining(MESSAGES[0]),
    ]);
  });
}

describe("W10-SETTINGS-TASKS-L05 retained prompt response contract", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.post.mockResolvedValue(CREATE_RESPONSE);
    mocks.get.mockImplementation(async (url) => {
      if (url === "/prompt-templates/") return { data: { results: [], next: null } };
      if (url === `/prompt-templates/${TEMPLATE_ID}/versions/`) {
        return { data: { results: [], count: 0 } };
      }
      if (url === `/prompt-templates/${TEMPLATE_ID}/`) return { data: TEMPLATE };
      throw new Error(`Unexpected request: ${url}`);
    });
  });

  afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
  });

  it("creates from scratch and opens the returned root_template", async () => {
    renderLegacyRoute(<PromptRightSidebar />);
    fireEvent.click(screen.getByRole("button", { name: "Write a prompt from scratch" }));

    await expectHydrated();
    expect(mocks.post).toHaveBeenCalledWith(
      "/prompt-templates/create-draft/",
      expect.objectContaining({ prompt_config: expect.any(Array) }),
    );
  });

  it("creates from the sidebar and opens the returned root_template", async () => {
    renderLegacyRoute(<PromtLeftSidebar currentIndex={0} setCurrentIndex={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Add New Prompt" }));

    await expectHydrated();
    expect(mocks.post).toHaveBeenCalledWith(
      "/prompt-templates/create-draft/",
      expect.objectContaining({ prompt_config: expect.any(Array) }),
    );
  });

  it("creates from the open editor without navigating to an undefined ID", async () => {
    renderLegacyRoute(null, EDITOR_PATH);
    await expectHydrated();
    act(() => mocks.topMenu.mock.lastCall[0].handleCreateDraft());

    await waitFor(() => expect(mocks.topMenu.mock.lastCall[0].currentTitle).toBe("Untitled-44"));
    expect(screen.getByTestId("path").textContent).toBe(EDITOR_PATH);
    expect(mocks.post).toHaveBeenCalledWith(
      "/prompt-templates/create-draft/",
      expect.objectContaining({ prompt_config: expect.any(Array) }),
    );
  });

  it("hydrates a draft deep link and retains its configuration in the version list", async () => {
    renderLegacyRoute(null, EDITOR_PATH);
    await expectHydrated();

    const props = mocks.topMenu.mock.lastCall[0];
    expect(props.versions).toEqual([
      expect.objectContaining({
        isDraft: true,
        templateVersion: "v2",
        promptConfigSnapshot: { configuration: CONFIGURATION, messages: MESSAGES },
      }),
    ]);
    expect(props.total).toBe(1);
  });

  it("hydrates a published deep link without adding a draft version", async () => {
    const version = {
      id: "published-version",
      template_version: "v2",
      prompt_config_snapshot: TEMPLATE.prompt_config[0],
      is_draft: false,
    };
    mocks.get.mockImplementation(async (url) => {
      if (url === `/prompt-templates/${TEMPLATE_ID}/versions/`) {
        return { data: { results: [version], count: 1 } };
      }
      if (url === `/prompt-templates/${TEMPLATE_ID}/`) {
        return { data: { ...TEMPLATE, is_draft: false } };
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    renderLegacyRoute(null, EDITOR_PATH);
    await expectHydrated();

    const props = mocks.topMenu.mock.lastCall[0];
    expect(props.versions).toHaveLength(1);
    expect(props.versions[0].isDraft).toBe(false);
    expect(props.total).toBe(1);
  });
});
