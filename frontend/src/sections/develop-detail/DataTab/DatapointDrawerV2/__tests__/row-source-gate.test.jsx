// Where an imported row came from shows only in a build that ships the row
// source surface: the Row view's source menu items, its Conversation and
// Trajectory tabs and the trace drawer read the row's source through one
// gate. A row with a source and no claim shows none of them, and a deep
// link to a tab the row cannot have lands on Row.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const { h, Null } = vi.hoisted(() => ({
  h: { nav: null, shell: null, drawer: null },
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
  default: (props) => {
    h.drawer = props;
    return null;
  },
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
  useDatasetKind: () => ({ kind: null, dataset: { name: "Support" } }),
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

const SURFACE = "dataset.rowExtras.conversation";
const TRACE = {
  type: "trace",
  label: "Trace 7e1a2b3c",
  project_id: "00000000-0000-4000-8000-0000000000b1",
  trace_id: "7e1a2b3c-0000-4000-8000-000000000001",
  span_id: "1a2b3c4d5e6f7081",
  expired: false,
};
const SPAN = { ...TRACE, type: "span", label: "Span 1a2b3c4d" };
const SESSION = {
  type: "session",
  label: "Session 5d6e7f80",
  project_id: TRACE.project_id,
  session_id: "5d6e7f80-0000-4000-8000-000000000002",
  expired: false,
};

const rowWith = (source) => ({
  row_id: "R1",
  order: 0,
  "col-1": { cell_value: "What is the refund window?" },
  ...(source ? { row_meta: { source } } : {}),
});
const navFor = (row) => ({
  row,
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
});

const client = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});
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
const menuItems = () => h.shell?.header?.menu?.props?.menuItems ?? {};
const launch = (claimed) =>
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: claimed ? [SURFACE] : [],
  });
const open = (source, search = "row=R1", claimed = true) => {
  launch(claimed);
  h.nav = navFor(rowWith(source));
  render(ui(search));
};

beforeEach(() => {
  h.shell = null;
  h.drawer = null;
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});

describe("Row view source without the row source claim", () => {
  it.each([
    ["trace", TRACE],
    ["span", SPAN],
    ["session", SESSION],
    ["expired trace", { ...TRACE, expired: true }],
  ])(
    "shows no source door, menu item or kind tab for a %s source",
    (_, source) => {
      open(source, "row=R1", false);
      expect(h.shell?.open).toBe(true);
      expect(menuItems()).not.toHaveProperty("openTrace");
      expect(menuItems()).not.toHaveProperty("openSaved");
      expect(tabValues()).not.toContain("trajectory");
      expect(tabValues()).not.toContain("conversation");
      act(() => h.shell.header.line2.props.onOpenTrace());
      expect(h.drawer).toBeNull();
    },
  );

  it("lands a rowTab=trajectory deep link on Row", () => {
    open(TRACE, "row=R1&rowTab=trajectory", false);
    expect(h.shell.tab).toBe("row");
  });
});

describe("Row view source with the row source claim", () => {
  it.each([
    ["trace", TRACE],
    ["span", SPAN],
  ])("opens the same trace from a %s source", (_, source) => {
    open(source);
    expect(tabValues()).toContain("trajectory");
    expect(tabValues()).not.toContain("conversation");
    expect(menuItems()).toHaveProperty("openTrace");
    act(() => menuItems().openTrace.onClick());
    expect(h.drawer).toMatchObject({
      traceId: source.trace_id,
      projectId: source.project_id,
      initialSpanId: source.span_id,
    });
  });

  it("opens the saved copy, never the trace drawer, for an expired trace", () => {
    open({ ...TRACE, expired: true });
    expect(menuItems()).not.toHaveProperty("openTrace");
    expect(menuItems()).toHaveProperty("openSaved");
    act(() => h.shell.header.line2.props.onOpenTrace());
    expect(h.drawer).toBeNull();
  });

  it("offers Conversation for a session source", () => {
    open(SESSION);
    expect(tabValues()).toContain("conversation");
  });

  it("offers only Conversation for an expired session", () => {
    open({ ...SESSION, expired: true });
    expect(tabValues()).toContain("conversation");
    expect(tabValues()).not.toContain("trajectory");
  });

  it.each(["trajectory", "conversation"])(
    "lands a rowTab=%s deep link on Row for a row with no source",
    (tab) => {
      open(null, `row=R1&rowTab=${tab}`);
      expect(tabValues()).not.toContain("trajectory");
      expect(tabValues()).not.toContain("conversation");
      expect(h.shell.tab).toBe("row");
    },
  );

  it("lands a rowTab=conversation deep link on Row for a trace source", () => {
    open(TRACE, "row=R1&rowTab=conversation");
    expect(h.shell.tab).toBe("row");
  });

  it("keeps a rowTab=trajectory deep link for a trace source", () => {
    open(TRACE, "row=R1&rowTab=trajectory");
    expect(h.shell.tab).toBe("trajectory");
  });
});
