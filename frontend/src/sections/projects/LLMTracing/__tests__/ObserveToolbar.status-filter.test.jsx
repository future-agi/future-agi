import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";
import ObserveToolbar from "../ObserveToolbar";

const traceFilterPanelPropsMock = vi.hoisted(() => vi.fn());

vi.mock("../TraceFilterPanel", () => ({
  default: (props) => {
    traceFilterPanelPropsMock(props);
    return null;
  },
}));

vi.mock("../DisplayPanel", () => ({ default: () => null }));
vi.mock("../BulkActionsBar", () => ({ default: () => null }));
vi.mock("../tabStore", () => ({
  useTabStoreShallow: (selector) => selector({ openCreateModal: vi.fn() }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));

const renderToolbar = (props = {}) =>
  render(
    <ObserveToolbar
      inline
      tab="trace"
      isFilterOpen={false}
      onFilterToggle={vi.fn()}
      onApplyExtraFilters={vi.fn()}
      {...props}
    />,
  );

describe("ObserveToolbar status filter registry", () => {
  beforeEach(() => {
    traceFilterPanelPropsMock.mockClear();
  });

  it("uses voice-call fields when the rendered trace grid is a simulator call log", () => {
    renderToolbar({ isSimulator: true });

    expect(traceFilterPanelPropsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        tab: "voiceCalls",
        isSimulator: true,
      }),
    );
  });

  it.each(["trace", "spans"])(
    "keeps the %s registry for ordinary tracing grids",
    (tab) => {
      renderToolbar({ tab, isSimulator: false });

      expect(traceFilterPanelPropsMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ tab, isSimulator: false }),
      );
    },
  );

  it("forwards an explicit project scope for routes without observeId", () => {
    renderToolbar({ projectId: "project-from-query-string" });

    expect(traceFilterPanelPropsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ projectId: "project-from-query-string" }),
    );
  });

  it("forwards workspace property scope for cross-project user detail", () => {
    renderToolbar({ allowWorkspaceScope: true });

    expect(traceFilterPanelPropsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        projectId: undefined,
        allowWorkspaceScope: true,
      }),
    );
  });

  it("keeps workspace Users values session-scoped and attributes trace-scoped", () => {
    renderToolbar({ mode: "users", allowWorkspaceScope: true });

    expect(traceFilterPanelPropsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        source: "sessions",
        propertyNamespace: "users",
        attributeSource: "traces",
        projectId: undefined,
        allowWorkspaceScope: true,
      }),
    );
  });

  it("keeps user-detail Sessions values session-scoped and attributes trace-scoped", () => {
    renderToolbar({ mode: "sessions", allowWorkspaceScope: true });

    expect(traceFilterPanelPropsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        source: "sessions",
        propertyNamespace: "sessions",
        attributeSource: "traces",
        projectId: undefined,
        allowWorkspaceScope: true,
      }),
    );
  });

  it("keeps user-detail Trace values, namespace, and attributes trace-scoped", () => {
    renderToolbar({ mode: "traces", allowWorkspaceScope: true });

    expect(traceFilterPanelPropsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        source: "traces",
        propertyNamespace: "traces",
        attributeSource: undefined,
        projectId: undefined,
        allowWorkspaceScope: true,
      }),
    );
  });
});
