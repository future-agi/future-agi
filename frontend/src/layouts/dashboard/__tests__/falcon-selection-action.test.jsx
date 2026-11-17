/* eslint-disable react/prop-types */
import React from "react";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import {
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import DashboardLayout from "../index";
import SelectionBar from "src/sections/experiment-detail/results/SelectionBar";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";

const fixture = vi.hoisted(() => ({
  capability: {},
  barWidth: 160,
  belowMd: false,
  refetch: vi.fn(),
  socket: vi.fn(),
  catalog: vi.fn(),
  sendChat: vi.fn(),
  createConversation: vi.fn(),
}));

vi.mock("src/hooks/useCapabilities", () => ({
  useFeatureAllowed: () => fixture.capability,
}));
vi.mock("src/hooks/use-responsive", () => ({
  useResponsive: (query) =>
    query === "down" ? fixture.belowMd : !fixture.belowMd,
}));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ themeLayout: "vertical" }),
}));
vi.mock("src/sections/omega/shell/useOmegaConsole", () => ({
  useOmegaConsole: () => ({ enabled: false }),
}));
vi.mock("src/sections/omega/shell/OmegaMobileBar", () => ({
  default: () => null,
}));
vi.mock("src/sections/omega/shell/products", () => ({
  isAgentInfrastructurePath: () => false,
  isGatewayPath: () => false,
  rememberHostPath: () => {},
}));
vi.mock("../nav-mini", () => ({ default: () => null }));
vi.mock("../nav-vertical", () => ({ default: () => null }));
vi.mock("../nav-horizontal", () => ({ default: () => null }));
vi.mock("../WorkspaceSwitcher/CreateWorkspaceModal", () => ({
  default: () => null,
}));
vi.mock("../sos-mode-banner", () => ({ default: () => null }));
vi.mock("src/components/two-factor-enforcement/TwoFactorBanner", () => ({
  default: () => null,
}));
vi.mock("src/components/host-update-status/HostUpdateNotice", () => ({
  default: () => null,
}));
vi.mock("src/sections/gateway/shell/GatewayNavigationFocus", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/sections/common/CommandPalette/PaletteBoundary", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/sections/common/CommandPalette/commandRegistry", () => ({
  useRegisterCommands: () => {},
}));
vi.mock("src/sections/common/CommandPalette/CommandPalette", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/CommandPalette/AppPageCommands", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/ux/GlobalKeyboard", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/ux/oneview/OneViewStyles", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/ux/oneview/LegacyParamScrub", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Experiment/RunTray", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/RunSources/RerunWithChangesHost", () => ({
  default: () => null,
  routeIdsOf: () => ({}),
}));
vi.mock("src/launch/launchSwitch", () => ({ isSurfaceShown: () => false }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/falcon-ai/components/FalconAIFab", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/components/FalconAIBarButton", () => ({
  default: () => null,
}));

// Keep the actual lazy sidebar, ChatInput, store and contextual action chain.
// Only chat I/O and unrelated sidebar controls are replaced.
vi.mock("src/sections/falcon-ai/hooks/useFalconSocket", () => ({
  default: fixture.socket,
}));
vi.mock("src/sections/falcon-ai/hooks/useFalconCatalog", () => ({
  default: fixture.catalog,
}));
vi.mock("src/sections/falcon-ai/hooks/useFalconContext", () => ({
  useFalconContext: () => ({ page: "experiment" }),
}));
vi.mock("src/sections/falcon-ai/hooks/useFalconAPI", () => ({
  createConversation: fixture.createConversation,
  uploadFile: vi.fn(),
  updateConnector: vi.fn(),
  fetchConnectors: vi.fn(),
}));
vi.mock("src/sections/falcon-ai/components/FalconAIHeader", () => ({
  default: ({ onClose }) => (
    <button type="button" onClick={onClose}>
      Close Falcon AI
    </button>
  ),
}));
vi.mock("src/sections/falcon-ai/components/MessageList", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/components/SkillPicker", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/components/ContextSelector", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/components/SlashCommandPicker", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/components/AttachedFileChip", () => ({
  default: () => null,
}));
vi.mock("src/sections/gateway/hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: false }),
}));
vi.mock("src/sections/common/Collab/AssignDialog", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Review/StartReviewDialog", () => ({
  default: () => null,
}));
vi.mock("src/sections/experiment-detail/results/PairwiseJudgeDialog", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/experiment-detail/results/AddResultsToDatasetDialog",
  () => ({ default: () => null }),
);
vi.mock("src/sections/common/export/ExportDialog", () => ({
  ResultsExportDialog: () => null,
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({ can: { edit: true, run: true, download: true } }),
}));
vi.mock("src/api/develop/run-results", () => ({
  useRerunResultCells: () => ({ request: vi.fn() }),
}));
vi.mock("src/sections/common/Runs/useExternalRunHandoff", () => ({
  useExternalRunHandoff: () => ({ request: vi.fn(), dialog: null }),
}));
vi.mock("src/sections/common/Runs/useRescore", () => ({
  useRescore: () => ({ request: vi.fn(), dialog: null }),
}));
vi.mock("src/sections/common/Runs/rescoreCopyModel", () => ({
  scoreWithChoices: () => [],
}));
vi.mock("src/sections/common/Runs/runKind", () => ({
  canRerunHere: () => true,
}));
vi.mock("src/sections/experiment-detail/iterate/iterateModel", () => ({
  canIterate: () => true,
}));
vi.mock("src/sections/common/Selection/exportSelection", () => ({
  resultsSelectionView: () => ({}),
}));
vi.mock("src/sections/common/Selection/useSelectAllMatching", () => ({
  useSelectAllMatching: ({ selectedIds }) => ({ ids: selectedIds }),
}));
vi.mock("src/sections/common/Selection/SelectionCount", () => ({
  default: () => null,
}));

const ROUTE = "/dashboard/develop/experiment/exp-1/data?datasetId=ds-1";
const ACTION = "Rule-based summary of this row (Falcon)";
const PROMPT = [
  "What happened on this row?",
  "",
  "Comparison context:",
  "- dataset_id: ds-1",
  "- experiment_id: exp-1",
  "- runs: #1A=run-1",
  "- baseline: none",
  '- filters: [{"type":"rows","ids":["ex-1"],"label":"1 row from #1A"}]',
  `- analysis_url: ${ROUTE}`,
].join("\n");
const UNKNOWN = "Couldn't check whether Falcon AI is available. Try again.";
const DENIED = "Falcon AI is not available in this workspace.";
const ENTRY_POINTS = [
  ["More actions", 160],
  ["direct button", 5000],
];

const page = () => (
  <DashboardLayout>
    <SelectionBar
      datasetId="ds-1"
      selectedRows={[{ example_id: "ex-1", output: "private row contents" }]}
      runs={[{ run_id: "run-1", label: "#1A" }]}
      baselineId={null}
      pills={[]}
      onClear={() => {}}
    />
  </DashboardLayout>
);

async function chooseAction(entry = "More actions") {
  const bar = within(screen.getByTestId("results-selection-bar"));
  if (entry === "More actions") {
    expect(bar.queryByRole("button", { name: ACTION, exact: true })).toBeNull();
    fireEvent.click(bar.getByRole("button", { name: "More actions" }));
    const menu = await screen.findByRole("menu");
    fireEvent.click(within(menu).getByRole("menuitem", { name: ACTION }));
  } else {
    fireEvent.click(bar.getByRole("button", { name: ACTION, exact: true }));
  }
  await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
}

function expectNothingSent() {
  expect(fixture.sendChat.mock.calls).toEqual([]);
  expect(fixture.createConversation.mock.calls).toEqual([]);
  expect(useFalconStore.getState().messages).toEqual([]);
  expect(`${window.location.pathname}${window.location.search}`).toBe(ROUTE);
}

// The layout lazy-loads the sidebar. Load that module once so the first
// allowed case does not race a cold import against findBy's default wait.
beforeAll(async () => {
  await import("src/sections/falcon-ai/FalconAISidebar");
}, 30000);

beforeEach(() => {
  vi.clearAllMocks();
  fixture.barWidth = 160;
  fixture.belowMd = false;
  fixture.capability = {
    allowed: false,
    isKnown: true,
    isLoading: false,
    isError: false,
    isFetching: false,
    reason: null,
    refetch: fixture.refetch,
  };
  fixture.socket.mockReturnValue({
    sendChat: fixture.sendChat,
    sendStop: vi.fn(),
    sendFeedback: vi.fn(),
  });
  fixture.catalog.mockReturnValue(vi.fn());
  useFalconStore.getState().resetAll();
  // jsdom has no layout: exercise the real fitter with measured action
  // widths, not a mocked ActionBar or a direct call to the action helper.
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(
    function rect() {
      const width = this.hasAttribute("data-action-bar")
        ? fixture.barWidth
        : this.hasAttribute("data-measure-more")
          ? 72
          : this.getAttribute("data-measure-id") === "clear"
            ? 32
            : 180;
      return {
        width,
        height: 32,
        top: 0,
        bottom: 32,
        left: 0,
        right: width,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      };
    },
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Falcon contextual selection action", () => {
  it.each(ENTRY_POINTS)(
    "from %s opens the allowed sidebar with the exact editable question",
    async (entry, width) => {
      fixture.barWidth = width;
      fixture.capability.allowed = true;
      renderWithRouter(page(), { route: ROUTE });
      expect(screen.queryByTestId("falcon-ai-sidebar")).toBeNull();
      expect(fixture.socket.mock.calls).toEqual([]);

      await chooseAction(entry);

      const input = await screen.findByPlaceholderText("Ask anything...");
      await waitFor(() => expect(input).toHaveValue(PROMPT));
      expect(screen.getByTestId("falcon-ai-sidebar")).toBeVisible();
      expect(useFalconStore.getState().isSidebarOpen).toBe(true);
      expect(useFalconStore.getState().pendingPrompt).toBeNull();
      fireEvent.change(input, {
        target: { value: `${PROMPT}\nExplain more.` },
      });
      expect(input).toHaveValue(`${PROMPT}\nExplain more.`);
      expectNothingSent();
    },
  );

  it.each(ENTRY_POINTS)(
    "from %s explains a denied capability and can dismiss and reopen the notice",
    async (entry, width) => {
      fixture.barWidth = width;
      renderWithRouter(page(), { route: ROUTE });
      expect(screen.queryByRole("dialog")).toBeNull();

      await chooseAction(entry);

      const dialog = await screen.findByRole("dialog", { name: "Falcon AI" });
      expect(dialog).toHaveAccessibleDescription(DENIED);
      expect(screen.queryByPlaceholderText("Ask anything...")).toBeNull();
      expect(useFalconStore.getState().isSidebarOpen).toBe(true);
      expect(useFalconStore.getState().pendingPrompt).toBe(PROMPT);
      expect(fixture.socket.mock.calls).toEqual([]);
      expect(fixture.catalog.mock.calls).toEqual([]);
      expectNothingSent();

      fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(useFalconStore.getState().isSidebarOpen).toBe(false);
      expect(useFalconStore.getState().pendingPrompt).toBe(PROMPT);
      await chooseAction(entry);
      expect(
        await screen.findByRole("dialog", { name: "Falcon AI" }),
      ).toHaveAccessibleDescription(DENIED);
    },
  );

  it.each([
    ["a missing answer", { isKnown: false }],
    ["a failed read", { isError: true }],
  ])(
    "after %s offers retry and opens the saved question on success",
    async (_, state) => {
      Object.assign(fixture.capability, state);
      const { rerender } = renderWithRouter(page(), { route: ROUTE });
      await chooseAction();
      const dialog = await screen.findByRole("dialog", { name: "Falcon AI" });
      expect(dialog).toHaveAccessibleDescription(UNKNOWN);
      expect(useFalconStore.getState().pendingPrompt).toBe(PROMPT);
      expect(fixture.socket.mock.calls).toEqual([]);

      fireEvent.click(
        within(dialog).getByRole("button", { name: "Try again" }),
      );
      expect(fixture.refetch).toHaveBeenCalledTimes(1);
      expect(fixture.refetch).toHaveBeenCalledWith();
      fixture.capability = {
        ...fixture.capability,
        allowed: true,
        isKnown: true,
        isError: false,
      };
      rerender(page());
      const input = await screen.findByPlaceholderText("Ask anything...");
      await waitFor(() => expect(input).toHaveValue(PROMPT));
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(useFalconStore.getState().pendingPrompt).toBeNull();
      expectNothingSent();
    },
  );

  it("shows progress while the capability loads, then the supplied denial reason", async () => {
    Object.assign(fixture.capability, {
      isLoading: true,
      isKnown: false,
      isFetching: true,
    });
    const { rerender } = renderWithRouter(page(), { route: ROUTE });
    await chooseAction();
    const dialog = await screen.findByRole("dialog", { name: "Falcon AI" });
    expect(dialog).toHaveAccessibleDescription(
      "Checking whether Falcon AI is available…",
    );
    expect(
      within(dialog).getByRole("button", { name: "Try again" }),
    ).toBeDisabled();
    expect(fixture.socket.mock.calls).toEqual([]);

    fixture.capability = {
      ...fixture.capability,
      isLoading: false,
      isKnown: true,
      isFetching: false,
      reason: "Falcon AI is unavailable on this deployment.",
    };
    rerender(page());
    expect(dialog).toHaveAccessibleDescription(
      "Falcon AI is unavailable on this deployment.",
    );
    expect(useFalconStore.getState().pendingPrompt).toBe(PROMPT);
    expectNothingSent();
  });

  it("also explains a blocked contextual open in the compact shell", async () => {
    fixture.belowMd = true;
    const route = "/dashboard/settings/profile-settings";
    renderWithRouter(page(), { route });
    expect(
      screen.getByRole("main").closest('[data-shell="compact"]'),
    ).not.toBeNull();
    await chooseAction();
    expect(
      await screen.findByRole("dialog", { name: "Falcon AI" }),
    ).toHaveAccessibleDescription(DENIED);
    expect(screen.queryByPlaceholderText("Ask anything...")).toBeNull();
    expect(fixture.socket.mock.calls).toEqual([]);
  });
});
