import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import CreateScenarioView from "../CreateScenarioView";

// Create a scenario at a 1024px window: the "Choose source" placeholder was
// cut ("Select agent definition or prompt" needs about 250px: 186px of 12px
// Inter plus the field's padding, arrow and border, and the row gave it about
// 210px), and "Learn more" beside the scenario type tabs wrapped onto two
// lines. jsdom has no layout, so these tests pin the styles that decide it.
// The two fields and the link render for real; the rest of the page is
// stubbed as in CreateScenarioView.test.jsx.

const theme = createTheme({ palette: palette("light") });

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  useAgentDefinitions: vi.fn(),
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
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: ({ fieldName, label, placeholder }) => (
    <div data-testid={`field-${fieldName}`}>
      <input aria-label={label} placeholder={placeholder} />
    </div>
  ),
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/tabs/tabs", () => ({
  SegmentedTabs: () => null,
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

const SOURCE_PLACEHOLDER = "Select agent definition or prompt";
const VERSION_PLACEHOLDER = "Select version (eg: v1)";
// The source placeholder's width plus the field's 64px of padding, arrow and
// border.
const SOURCE_FIELD_MIN_PX = 251;

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
  mocks.get.mockResolvedValue({
    data: { results: [], current_page: 1, total_pages: 1 },
  });
  mocks.useAgentDefinitions.mockReturnValue({
    agentDefinitions: [
      { id: "agent-1", agent_name: "Support Agent", agent_type: "voice" },
    ],
    fetchNextPage: vi.fn(),
    isFetchingNextPage: false,
    isLoading: false,
  });
});

describe("CreateScenarioView: Choose source and Choose version", () => {
  it("never makes a field narrower than its placeholder; the row wraps instead", () => {
    renderView();

    // Both placeholders keep their full wording.
    const source = screen
      .getByPlaceholderText(SOURCE_PLACEHOLDER)
      .closest(".MuiFormControl-root");
    const versionInput = screen.getByPlaceholderText(VERSION_PLACEHOLDER);
    expect(source).not.toBeNull();

    const row = source.parentElement;
    expect(row).toContainElement(versionInput);
    const version = [...row.children].find((child) =>
      child.contains(versionInput),
    );

    // Each field starts from enough room for the source placeholder and
    // shares what is left; when the row has less than that for both, the
    // version field moves under the source field.
    expect(getComputedStyle(row).flexWrap).toBe("wrap");
    for (const field of [source, version]) {
      const style = getComputedStyle(field);
      expect(parseFloat(style.flexBasis)).toBeGreaterThanOrEqual(
        SOURCE_FIELD_MIN_PX,
      );
      expect(style.flexGrow).toBe("1");
    }
  });
});

describe("CreateScenarioView: Learn more beside the scenario type tabs", () => {
  it("stays on one line at the right and moves under the tabs when they need the row", () => {
    renderView();
    const link = screen.getByRole("link", { name: "Learn more" });

    const style = getComputedStyle(link);
    expect(style.whiteSpace).toBe("nowrap");
    expect(style.flexShrink).toBe("0");
    expect(getComputedStyle(link.parentElement).flexWrap).toBe("wrap");
    // Alone on the next line it keeps to the right, as beside the tabs.
    expect(style.marginLeft).toBe("auto");
  });
});
