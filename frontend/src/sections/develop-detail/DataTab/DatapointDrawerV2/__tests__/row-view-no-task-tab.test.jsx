// A task row of a task-suite dataset opens with Row and Raw and no Task tab,
// and a `rowTab=task` deep link lands on Row. Every gate is open here
// (launch off), so the absent tab cannot come from a hidden surface.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const { h, Null } = vi.hoisted(() => ({
  h: { nav: null, shell: null },
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
// The shell is where the tab strip lives: keep its props, render nothing.
vi.mock("src/sections/common/RowPanel/RowPanelShell", () => ({
  default: (props) => {
    h.shell = props;
    return null;
  },
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
  useDatasetKind: () => ({ kind: "task_suite", dataset: { name: "Suite" } }),
}));
vi.mock(
  "src/sections/develop-detail/TaskSuite/itemModel",
  async (importOriginal) => ({
    ...(await importOriginal()),
    isExpiredSource: () => false,
    rowKindOf: () => "task",
  }),
);
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
vi.mock("../RowViewBody", () => ({ default: Null }));
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
  RowHeaderLine2: Null,
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

const TASK_ROW = {
  row_id: "T1",
  order: 0,
  kind: "task",
  "col-1": { cell_value: "Book a table for two" },
};
const nav = {
  row: TASK_ROW,
  view: null,
  loaded: true,
  isPending: false,
  pendingGone: false,
  isLoading: false,
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
};

const client = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});
// The real URL hook reads `row` and `rowTab` from the address.
const ui = (search) => (
  <QueryClientProvider client={client}>
    <HelmetProvider>
      <MemoryRouter initialEntries={[`/dashboard/develop/ds-1?${search}`]}>
        <Routes>
          <Route path="/dashboard/develop/:dataset" element={<RowView />} />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>
  </QueryClientProvider>
);
const tabValues = () => (h.shell?.tabs ?? []).map((t) => t.value);

beforeEach(() => {
  setLaunchOverrideForTest({ launch: false });
  h.nav = nav;
  h.shell = null;
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});

describe("Row view of a task row", () => {
  it("offers Row and Raw and no Task tab", () => {
    render(ui("row=T1"));
    expect(h.shell?.open).toBe(true);
    expect(tabValues()).toEqual(expect.arrayContaining(["row", "raw"]));
    expect(tabValues()).not.toContain("task");
    expect(h.shell.tab).toBe("row");
  });

  it("lands a rowTab=task deep link on the Row tab", () => {
    render(ui("row=T1&rowTab=task"));
    expect(h.shell?.open).toBe(true);
    expect(tabValues()).not.toContain("task");
    expect(h.shell.tab).toBe("row");
  });
});
