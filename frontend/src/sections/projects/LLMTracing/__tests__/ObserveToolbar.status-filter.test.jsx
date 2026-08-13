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

  it("uses trace-backed value catalogs for Users filters", () => {
    renderToolbar({ mode: "users", projectId: "users-project" });

    expect(traceFilterPanelPropsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ source: "traces", projectId: "users-project" }),
    );
  });

  it("keeps the session value catalog for session filters", () => {
    renderToolbar({ mode: "sessions", projectId: "sessions-project" });

    expect(traceFilterPanelPropsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        source: "sessions",
        projectId: "sessions-project",
      }),
    );
  });
});
