import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import CreateScenarioView from "../CreateScenarioView";

// The app palette: links on this page use the status.info.fg colour, which
// MUI cannot resolve (and throws on) without it.
const theme = createTheme({ palette: palette("light") });

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  useAgentDefinitions: vi.fn(),
  segmentedTabs: null,
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    develop: {
      runPrompt: {
        promptExecutions: () => "/model-hub/prompt-executions/",
        getPromptVersions: () => "/model-hub/prompt-history-executions/",
      },
    },
    scenarios: {
      create: "/simulate/scenarios/create/",
      list: "/simulate/scenarios/",
    },
    misc: { uploadFile: "/model-hub/upload-file/" },
  },
}));

// The page's agent list (connected agents) and its versions.
vi.mock("src/components/run-tests/common", () => ({
  useAgentDefinitions: (...args) => mocks.useAgentDefinitions(...args),
}));
vi.mock("src/api/agent-definition/agent-definition-version", () => ({
  useAgentDefinitionVersions: () => ({
    data: undefined,
    fetchNextPage: vi.fn(),
    isFetchingNextPage: false,
    isFetching: false,
  }),
}));

vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

// Fields render as plain elements so the test reads exactly what the page
// passes them: the source list shows its option labels, and a text field
// shows the helper text the page gives it.
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: ({ fieldName, label, placeholder, helperText }) => (
    <div data-testid={`field-${fieldName}`}>
      <input aria-label={label} placeholder={placeholder} />
      {helperText ? <p>{helperText}</p> : null}
    </div>
  ),
}));
vi.mock("../CategorizedSourceDropdown", () => ({
  default: ({ label, options }) => (
    <ul aria-label={label}>
      {options.map((o) => (
        <li key={o.value}>{o.label}</li>
      ))}
    </ul>
  ),
}));
vi.mock("src/components/FromSearchSelectField", () => ({
  FormSearchSelectFieldControl: () => null,
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/tabs/tabs", () => ({
  SegmentedTabs: (props) => {
    mocks.segmentedTabs = props;
    return null;
  },
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/ModalWrapper/ModalWrapper", () => ({
  default: () => null,
}));
vi.mock("../CreateScenarioHeader", () => ({ default: () => null }));
vi.mock("../WorkflowBuilderOption", () => ({ default: () => null }));
vi.mock("../ImportDatasetOption", () => ({ default: () => null }));
vi.mock("../UploadScriptOption", () => ({ default: () => null }));
vi.mock("../CallChatSOPOption", () => ({ default: () => null }));
vi.mock("../PersonaSection", () => ({ default: () => null }));
vi.mock("../scenario-detail/ColumnSection", () => ({ default: () => null }));
vi.mock("../ResourcesSection", () => ({ default: () => null }));
vi.mock("../CustomInstruction", () => ({ default: () => null }));

const HINT = "scenario-source-empty-hint";

const agentList = (agentDefinitions, { isLoading = false } = {}) => ({
  agentDefinitions,
  fetchNextPage: vi.fn(),
  isFetchingNextPage: false,
  isLoading,
});

const promptsPage = (results) => ({
  data: { results, current_page: 1, total_pages: 1 },
});

const renderView = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={["/dashboard/simulate/scenarios/create"]}>
          <CreateScenarioView />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  mocks.get.mockReset();
  mocks.post.mockReset();
  mocks.useAgentDefinitions.mockReset();
  mocks.get.mockResolvedValue(promptsPage([]));
});

describe("CreateScenarioView: No. of scenarios", () => {
  it("asks for the count in plain English", () => {
    mocks.useAgentDefinitions.mockReturnValue(agentList([]));
    renderView();
    expect(
      screen.getByPlaceholderText(
        "Set how many scenario variations you want to generate",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText(
        "Set how many scenario variations you want generate",
      ),
    ).toBeNull();
  });

  // The form starts at 20, so the placeholder is hidden; the minimum has to
  // be in the helper text or a new user only learns it from the error.
  it("says the minimum before anyone types a number", () => {
    mocks.useAgentDefinitions.mockReturnValue(agentList([]));
    renderView();
    expect(
      within(screen.getByTestId("field-noOfRows")).getByText(
        "At least 10, up to 20,000",
      ),
    ).toBeInTheDocument();
  });
});

describe("CreateScenarioView: Choose source with nothing to choose", () => {
  it("says where to get an agent or a prompt when the workspace has neither", async () => {
    mocks.useAgentDefinitions.mockReturnValue(agentList([]));
    renderView();

    await waitFor(() =>
      expect(screen.getByTestId(HINT)).toHaveTextContent(
        "No connected agents or saved prompts yet. Connect an agent or create a prompt in a new tab, then pick it here.",
      ),
    );
    const hint = screen.getByTestId(HINT);

    const connect = within(hint).getByRole("link", {
      name: "Connect an agent",
    });
    expect(connect).toHaveAttribute(
      "href",
      "/dashboard/simulate/environments?tab=build",
    );
    expect(connect).toHaveAttribute("target", "_blank");
    expect(connect.getAttribute("rel")).toContain("noopener");

    const prompt = within(hint).getByRole("link", { name: "create a prompt" });
    expect(prompt).toHaveAttribute("href", "/dashboard/workbench");
    expect(prompt).toHaveAttribute("target", "_blank");
  });

  it("points only to connecting an agent when prompts exist", async () => {
    mocks.useAgentDefinitions.mockReturnValue(agentList([]));
    mocks.get.mockResolvedValue(
      promptsPage([{ id: "prompt-1", name: "Support prompt" }]),
    );
    renderView();

    // The prompt is offered as a source, so the prompt list has loaded.
    await within(
      screen.getByRole("list", { name: "Choose source" }),
    ).findByText("Support prompt");

    const hint = await screen.findByTestId(HINT);
    expect(hint).toHaveTextContent(
      "No connected agents yet. Connect an agent in a new tab, then pick it here.",
    );
    expect(
      within(hint).getByRole("link", { name: "Connect an agent" }),
    ).toHaveAttribute("href", "/dashboard/simulate/environments?tab=build");
    expect(
      within(hint).queryByRole("link", { name: "create a prompt" }),
    ).toBeNull();
  });

  // Guards: these hold at the base too; they keep the hint honest.
  it("shows no hint when an agent is connected", async () => {
    mocks.useAgentDefinitions.mockReturnValue(
      agentList([
        {
          id: "agent-1",
          agent_name: "Clinic receptionist",
          agent_type: "voice",
        },
      ]),
    );
    renderView();

    await within(
      screen.getByRole("list", { name: "Choose source" }),
    ).findByText("Clinic receptionist");
    await waitFor(() => expect(mocks.get).toHaveBeenCalled());
    expect(screen.queryByTestId(HINT)).toBeNull();
  });

  it.each([
    ["is still loading", agentList(undefined, { isLoading: true })],
    ["failed to load", agentList(undefined)],
  ])(
    "does not claim there are no agents while the agent list %s",
    async (_label, state) => {
      mocks.useAgentDefinitions.mockReturnValue(state);
      renderView();

      await waitFor(() => expect(mocks.get).toHaveBeenCalled());
      expect(screen.queryByTestId(HINT)).toBeNull();
      expect(screen.queryByText(/No connected agents/)).toBeNull();
    },
  );
});

// At 1024 px the four build methods need about
// 445 px in a 423 px column; as a scrollable strip they showed a cut "Uplo"
// and a scroll arrow, hiding Upload script and Call / Chat SOP, and Learn
// more broke onto two lines beside them.
describe("CreateScenarioView: build-method tabs at 1024 px", () => {
  it("lets the four build methods wrap whole instead of scrolling, and keeps Learn more on one line", () => {
    mocks.useAgentDefinitions.mockReturnValue(agentList([]));
    renderView();
    expect(mocks.segmentedTabs.wrap).toBe(true);
    const learnMore = screen.getByRole("link", { name: "Learn more" });
    expect(getComputedStyle(learnMore).whiteSpace).toBe("nowrap");
  });
});
