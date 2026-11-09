// PF-1 (PUX-4, walk check w-l6-rv2 A-row-palette-row-verbs-registered): the
// Row view's palette verbs (Copy row id, Go to next row, ...) register once
// the row has loaded, on a deep link too, and no label ever reads "null".
// On the dev backend get-row-data's `current` may carry no `order`, so the
// row number stays null after the load; the registration must still follow
// the row, and the labels say "this row".
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useCommandStore } from "src/sections/common/CommandPalette/commandRegistry";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const { h, Null } = vi.hoisted(() => ({
  h: { nav: null, bodyProps: null, line2Props: null },
  Null: () => null,
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner", user: {} }),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const real = await importOriginal();
  const pending = () => new Promise(() => {});
  return {
    ...real,
    default: { ...real.default, get: pending, post: pending },
  };
});
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: Null,
}));
vi.mock(
  "src/sections/annotations/queues/components/add-to-queue-dialog",
  () => ({ default: Null }),
);
// The shell renders its header's line 2 and its body (both stubs below).
vi.mock("src/sections/common/RowPanel/RowPanelShell", () => ({
  default: ({ header, children }) => (
    <>
      {header?.line2}
      {children}
    </>
  ),
}));
vi.mock("src/sections/common/RowPanel/ShortcutSheet", () => ({
  default: Null,
}));
vi.mock("src/sections/common/EvaluationDrawer/getEvalsList", () => ({
  useEvalsList: () => ({ data: undefined }),
}));
vi.mock("src/api/develop/row-view", () => ({
  useDatasetSplitList: () => ({ data: undefined }),
  useRowRuns: () => ({ data: undefined }),
  rowViewKeys: { all: () => ["row-view"] },
}));
vi.mock("src/sections/common/ux/CopyId", () => ({ default: Null }));
vi.mock("src/sections/common/ux/CopyButton", () => ({
  copyText: async () => true,
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: () => {} }));
vi.mock("src/sections/common/ux/ConfirmDialog", () => ({ default: Null }));
vi.mock("src/sections/common/ux/oneview/Hint", () => ({
  default: Null,
  readDismissed: () => [],
  useHintStore: (select) => select({ dismissed: {} }),
}));
vi.mock("src/sections/common/ux/oneview/layoutPrefs", () => ({
  PROTO_USER_ID: "proto-user",
  useLayoutUserId: () => "u1",
}));
vi.mock("../../Versions/PendingChangesGate", () => ({
  useOpenSaveVersion: () => () => {},
}));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "rows", dataset: { name: "Seed" } }),
}));
vi.mock("src/sections/develop-detail/TaskSuite/itemModel", () => ({
  isExpiredSource: () => false,
  rowKindOf: () => "row",
}));
vi.mock("../../Versions/changeSetStore", () => ({
  METADATA_COLUMN: "__row_metadata__",
  ROW_STATUS_LABEL: {},
  useChangeSet: () => ({
    ops: [],
    byCell: {},
    deletedRowIds: [],
    pendingStatus: {},
    hasPending: false,
    addOp: () => {},
    revert: () => {},
  }),
  useChangeSetStore: { getState: () => ({ getOps: () => [] }) },
}));
vi.mock("../../Versions/pendingRows", () => ({
  findAddRowOp: () => null,
  isBlankAddRow: () => false,
  isImportedRowId: () => false,
}));
vi.mock("src/api/develop/dataset-versions", () => ({
  latestOf: () => null,
  useAsOfVersion: () => ({ latest: null }),
  useDatasetVersions: () => ({ data: undefined }),
}));
vi.mock("../../Versions/RestoreRowsDialog", () => ({
  default: Null,
  useRestoreRowsDialog: { getState: () => ({ open: () => {} }) },
}));
vi.mock("../../Versions/versionText", () => ({ restorableRows: () => null }));
vi.mock("../RowViewBody", () => ({
  default: (props) => {
    h.bodyProps = props;
    return null;
  },
}));
vi.mock("src/sections/common/Find/FindBar", () => ({ default: Null }));
vi.mock("../RowRawTab", () => ({ default: Null }));
vi.mock("../RowViewStates", () => ({
  RowBanner: Null,
  deletedRowText: () => "",
}));
vi.mock("../RowViewHeader", () => ({
  RowKeyStrip: Null,
  RowPositionText: Null,
  RowHeaderActions: Null,
  RowHeaderLine2: (props) => {
    h.line2Props = props;
    return null;
  },
  RowHeaderMenu: Null,
}));
vi.mock("../ConversationTab", () => ({ default: Null }));
vi.mock("../SourceTrajectoryTab", () => ({ default: Null }));
vi.mock("../RowHeaderPills", () => ({ DraftRowChip: Null, UnsavedPill: Null }));
vi.mock("../useRowActions", () => ({
  default: () => ({
    copyLink: () => {},
    duplicate: () => {},
    remove: () => {},
    download: () => {},
    newExperiment: () => {},
    selectInGrid: () => {},
    copyRowJson: () => {},
  }),
}));
vi.mock("../rowAccessGate", () => ({
  rowViewGate: () => ({ canEdit: true, annotate: true, editReason: null }),
  activityBadge: () => 0,
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({
    can: { edit: true, download: true, run: true },
    reason: () => "",
  }),
}));
vi.mock("src/api/develop/row-collab", () => ({
  useRowComments: () => ({ data: undefined }),
}));
vi.mock("../useRowNavigation", () => ({ default: () => h.nav }));
vi.mock("../useRowViewShortcuts", () => ({ default: () => {} }));
vi.mock("../useRowViewUrl", () => ({
  default: () => ({
    rowId: "R",
    rowTab: "row",
    full: false,
    editField: null,
    openRow: () => {},
    closeRow: () => {},
    setRowTab: () => {},
    setFull: () => {},
    clearEditField: () => {},
  }),
}));
vi.mock("src/sections/develop/realBackendCaps", () => ({
  gridWritesClassic: () => true,
}));
vi.mock("../../Versions/classicWrites", () => ({
  refusalSentence: () => "",
  writeCellClassic: () => Promise.resolve(),
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({
    refreshGrid: () => {},
    gridApi: { current: null },
  }),
}));

import RowView from "../RowView";

const navWith = (row, extra = {}) => ({
  row,
  view: null,
  loaded: Boolean(row),
  isPending: false,
  pendingGone: false,
  isLoading: !row,
  isError: false,
  refetch: () => {},
  position: null,
  outside: false,
  notFound: false,
  goPrev: () => {},
  goNext: () => {},
  prevDisabled: true,
  nextDisabled: false,
  nav: {},
  ...extra,
});

// A loaded row as dev's get-row-data answers it: row_id + cells, no order.
const LOADED_NO_ORDER = { row_id: "R", "col-1": { cell_value: "x" } };

const client = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});
const ui = () => (
  <QueryClientProvider client={client}>
    <HelmetProvider>
      <MemoryRouter initialEntries={["/dashboard/develop/ds-1?row=R"]}>
        <Routes>
          <Route path="/dashboard/develop/:dataset" element={<RowView />} />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>
  </QueryClientProvider>
);

const registered = () => useCommandStore.getState().sources["row-view"] ?? [];
const ids = () => registered().map((c) => c.id);
const labels = () => registered().map((c) => String(c.label));

afterEach(() => {
  h.nav = null;
  h.bodyProps = null;
  h.line2Props = null;
  setLaunchOverrideForTest(null);
  useCommandStore.setState({ sources: {} });
});

describe("Row view palette verbs (PF-1)", () => {
  it("a deep link registers the verbs once the row loads, with no order", () => {
    h.nav = navWith(null);
    const { rerender } = render(ui());
    // Nothing to act on while get-row-data is in flight.
    expect(ids()).toEqual([]);

    h.nav = navWith(LOADED_NO_ORDER);
    rerender(ui());
    expect(ids()).toEqual(expect.arrayContaining(["row.copyId", "row.next"]));
    expect(labels().filter((l) => /null|undefined/.test(l))).toEqual([]);
    expect(labels()).toContain("Delete this row…");
  });

  it("a click-open (grid row with order, then a loaded row without) keeps the verbs and never says null", () => {
    h.nav = navWith(
      { row_id: "R", order: 1, "col-1": { cell_value: "x" } },
      { loaded: false },
    );
    const { rerender } = render(ui());
    expect(ids()).toEqual(expect.arrayContaining(["row.copyId", "row.next"]));
    expect(labels()).toContain("Delete Row 2…");

    h.nav = navWith(LOADED_NO_ORDER);
    rerender(ui());
    expect(ids()).toEqual(expect.arrayContaining(["row.copyId", "row.next"]));
    expect(labels().filter((l) => /null|undefined/.test(l))).toEqual([]);
    expect(labels()).toContain("Delete this row…");
    expect(labels()).toContain("Restore this row…");
  });

  it("a loaded row that carries its order keeps its number (positive control)", () => {
    h.nav = navWith(null);
    const { rerender } = render(ui());
    h.nav = navWith({ ...LOADED_NO_ORDER, order: 2 });
    rerender(ui());
    expect(ids()).toEqual(expect.arrayContaining(["row.copyId", "row.next"]));
    expect(labels()).toContain("Delete Row 3…");
    expect(labels()).toContain("Restore Row 3…");
  });
});

// PF-4 (PUX-8): a launch build hides dataset.versions, so the Row view
// hands no history door to "Row not found" (RowViewBody) or the version
// pill (RowHeaderLine2); prototype and test builds keep it.
describe("Row view history door (PF-4)", () => {
  it("a launch build passes no onOpenHistory", () => {
    setLaunchOverrideForTest({ launch: true, experimentsBasic: false });
    h.nav = navWith(LOADED_NO_ORDER);
    render(ui());
    expect(h.bodyProps).not.toBeNull();
    expect(h.line2Props).not.toBeNull();
    expect(h.bodyProps.onOpenHistory).toBeUndefined();
    expect(h.line2Props.onOpenHistory).toBeUndefined();
  });

  it("a build that shows versions keeps the door (positive control)", () => {
    h.nav = navWith(LOADED_NO_ORDER);
    render(ui());
    expect(typeof h.bodyProps?.onOpenHistory).toBe("function");
    expect(typeof h.line2Props?.onOpenHistory).toBe("function");
  });
});
