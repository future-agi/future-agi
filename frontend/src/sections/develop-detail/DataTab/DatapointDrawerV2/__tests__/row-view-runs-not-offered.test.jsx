// Where the deployment does not offer runs across experiments, the Row view
// keeps its Runs tab in the strip, turned off with the shared line and no
// count; a rowTab=runs link lands on the Row tab, and the view asks for no
// runs read. Every gate of the launch switch is open here, so the turned-off
// tab comes from the registry's answer alone. Where runs are offered, the
// Runs count is the summary of the row runs read the view sends, and the Row
// tab is handed the same count.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { setCollabOfferForTest } from "src/sections/common/Collab/collabOffer";

const { h, Null } = vi.hoisted(() => ({
  h: { nav: null, shell: null, runs: [], answer: null, body: null },
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
// The shell is where the tab strip lives: keep its props and render its
// children, each of which is a stub here.
vi.mock("src/sections/common/RowPanel/RowPanelShell", () => ({
  default: (props) => {
    h.shell = props;
    return props.children ?? null;
  },
}));
vi.mock("src/sections/common/RowPanel/ShortcutSheet", () => ({
  default: Null,
}));
vi.mock("src/sections/common/EvaluationDrawer/getEvalsList", () => ({
  useEvalsList: () => ({ data: undefined }),
}));
// The runs read: keep what the view asks for; an enabled read answers
// h.answer.
vi.mock("src/api/develop/row-view", () => ({
  useDatasetSplitList: () => ({ data: undefined }),
  useRowRuns: (...args) => {
    h.runs.push(args);
    return args[3]?.enabled === false ? { data: undefined } : h.answer;
  },
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
  isImportedRowId: (id) => typeof id === "string" && id.startsWith("imp-"),
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
// The Row tab's body: keep its props, render nothing.
vi.mock("../RowViewBody", () => ({
  default: (props) => {
    h.body = props;
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

const ROW = {
  row_id: "T1",
  order: 0,
  kind: "task",
  row_meta: {},
  "col-1": { cell_value: "Book a table for two" },
};
// A row whose own row_meta names a runs count the read does not give.
const ROW_WITH_COUNTS = { ...ROW, row_meta: { counts: { runs: 7 } } };
const read = (runs, extra = {}) => ({
  data: { summary: { runs }, metrics: [], runs: [] },
  isError: false,
  isPlaceholderData: false,
  ...extra,
});
const nav = {
  row: ROW,
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
const runsTab = () => (h.shell?.tabs ?? []).find((t) => t.value === "runs");

beforeEach(() => {
  setLaunchOverrideForTest({ launch: false });
  // A deployment that has made nothing real.
  setCollabOfferForTest({ all: false, madeReal: [] });
  h.nav = nav;
  h.shell = null;
  h.runs = [];
  h.answer = { data: undefined, isLoading: true };
  h.body = null;
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
  setCollabOfferForTest(null);
});

describe("Row view where runs across experiments are not offered", () => {
  it("keeps the Runs tab, turned off with the shared line and no count", () => {
    render(ui("row=T1"));
    expect(h.shell?.open).toBe(true);
    expect(runsTab()).toEqual({
      value: "runs",
      label: "Runs",
      off: NOT_IN_RELEASE,
    });
    expect(h.shell.tab).toBe("row");
  });

  it("lands a rowTab=runs link on the Row tab, the Runs tab saying the shared line", () => {
    render(ui("row=T1&rowTab=runs"));
    expect(runsTab()?.off).toBe(NOT_IN_RELEASE);
    expect(h.shell.tab).toBe("row");
  });

  it("asks for no runs read", () => {
    render(ui("row=T1"));
    expect(h.runs.length).toBeGreaterThan(0);
    for (const args of h.runs) expect(args[3]).toEqual({ enabled: false });
  });

  it("hands the Row tab no runs count, whatever the row's own counts say", () => {
    h.nav = { ...nav, row: ROW_WITH_COUNTS };
    render(ui("row=T1"));
    expect(runsTab()).toEqual({
      value: "runs",
      label: "Runs",
      off: NOT_IN_RELEASE,
    });
    expect(h.body).not.toBeNull();
    expect(h.body.runsCount).toBeUndefined();
  });
});

describe("Row view where the registry makes runs real", () => {
  beforeEach(() =>
    setCollabOfferForTest({
      all: false,
      madeReal: ["dataset.rowExtras.runs"],
    }),
  );

  it("the Runs tab opens, with its count", () => {
    h.answer = read(3);
    render(ui("row=T1&rowTab=runs"));
    expect(runsTab()).toEqual({ value: "runs", label: "Runs", badge: 3 });
    expect(h.shell.tab).toBe("runs");
    for (const args of h.runs) expect(args[3]).toEqual({});
  });
});

describe("Row view with this build's own claims", () => {
  beforeEach(() => setCollabOfferForTest({ all: false }));

  it("the Runs tab opens, with its count, and asks for the runs read", () => {
    h.answer = read(3);
    render(ui("row=T1&rowTab=runs"));
    expect(runsTab()).toEqual({ value: "runs", label: "Runs", badge: 3 });
    expect(h.shell.tab).toBe("runs");
    expect(h.runs.length).toBeGreaterThan(0);
    for (const args of h.runs) expect(args[3]).toEqual({});
  });
});

describe("the Runs count comes from the row runs read", () => {
  beforeEach(() => {
    setCollabOfferForTest({ all: false });
    h.nav = { ...nav, row: ROW_WITH_COUNTS };
  });
  const runsBadge = () => runsTab()?.badge;
  // The tab is in the strip with no badge (toEqual skips an undefined one).
  const NO_BADGE = { value: "runs", label: "Runs" };

  it("shows no count while the read is in flight", () => {
    h.answer = { data: undefined, isLoading: true };
    render(ui("row=T1"));
    expect(runsTab()).toEqual(NO_BADGE);
    expect(runsBadge()).toBeUndefined();
    expect(h.body.runsCount).toBeUndefined();
  });

  it("shows no count when the read failed", () => {
    h.answer = { data: undefined, isError: true };
    render(ui("row=T1"));
    expect(runsTab()).toEqual(NO_BADGE);
    expect(runsBadge()).toBeUndefined();
    expect(h.body.runsCount).toBeUndefined();
  });

  it("shows no count when a refetch failed while holding the row's earlier answer", () => {
    h.answer = read(4, { isError: true });
    render(ui("row=T1"));
    expect(runsTab()).toEqual(NO_BADGE);
    expect(runsBadge()).toBeUndefined();
    expect(h.body.runsCount).toBeUndefined();
  });

  it("shows no count while the read still holds the previous row's answer", () => {
    h.answer = read(5, { isPlaceholderData: true });
    render(ui("row=T1"));
    expect(runsTab()).toEqual(NO_BADGE);
    expect(runsBadge()).toBeUndefined();
    expect(h.body.runsCount).toBeUndefined();
  });

  it("at 0, the badge is 0 and the Row tab is told 0", () => {
    h.answer = read(0);
    render(ui("row=T1"));
    expect(runsBadge()).toBe(0);
    expect(h.body.runsCount).toBe(0);
  });

  it("above 0, the badge and the Row tab carry the read's count", () => {
    h.answer = read(2);
    render(ui("row=T1"));
    expect(runsBadge()).toBe(2);
    expect(h.body.runsCount).toBe(2);
  });

  it("asks only for the Runs tab's default view, so no other read is sent", () => {
    h.answer = read(2);
    render(ui("row=T1&rowTab=runs"));
    expect(h.runs.length).toBeGreaterThan(0);
    for (const args of h.runs) {
      expect(args.slice(0, 3)).toEqual(["ds-1", "T1", {}]);
      expect(args[3]).toEqual({});
    }
  });
});

describe("a row that is not on the server yet has no runs", () => {
  beforeEach(() => setCollabOfferForTest({ all: false }));

  it.each([
    ["new-1", false],
    ["imp-1", true],
  ])("%s: the count is 0, never unknown", (id, imported) => {
    h.nav = {
      ...nav,
      isPending: true,
      row: { ...ROW_WITH_COUNTS, row_id: id, order: null, __pending: "new" },
    };
    // Its read stays off; whatever the hook still holds is not its answer.
    h.answer = read(5, { isPlaceholderData: true });
    render(ui(`row=${id}`));
    expect(runsTab()).toEqual({ value: "runs", label: "Runs", badge: 0 });
    expect(h.body.isNew).toBe(true);
    expect(h.body.isImported).toBe(imported);
    expect(h.body.runsCount).toBe(0);
  });
});
