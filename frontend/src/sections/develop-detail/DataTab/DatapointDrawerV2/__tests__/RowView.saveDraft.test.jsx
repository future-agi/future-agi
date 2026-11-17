/* eslint-disable react/prop-types -- inline vi.mock component stubs */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useCommandStore } from "src/sections/common/CommandPalette/commandRegistry";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const { h, Null } = vi.hoisted(() => ({
  h: {
    nav: null,
    bodyProps: null,
    line2Props: null,
    rowId: "R",
    dataType: "text",
    post: vi.fn(),
  },
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
    default: { ...real.default, get: pending, post: h.post },
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
  KeyCap: ({ children }) => <kbd>{children}</kbd>,
}));
vi.mock("src/components/iconify", () => ({ default: Null }));
vi.mock("@monaco-editor/react", () => ({
  Editor: ({ value, onChange, options }) => (
    <textarea
      aria-label="JSON value"
      value={value}
      disabled={options.readOnly}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
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
    const id = props.edit.editingId;
    return id ? (
      <FieldEditor
        key={`${props.rowId}:${id}`}
        dataType={h.dataType}
        value={props.row[id]?.cell_value}
        applyRef={props.edit.applyRef}
        onCancel={props.edit.cancel}
        onApply={(value) => props.edit.apply(id, value)}
      />
    ) : null;
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
    rowId: h.rowId,
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
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({
    refreshGrid: () => {},
    gridApi: { current: null },
  }),
}));

import RowView from "../RowView";
import FieldEditor from "../FieldEditor";
import { endpoints } from "src/utils/axios";

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

afterEach(() => {
  cleanup();
  client.clear();
  h.nav = null;
  h.bodyProps = null;
  h.line2Props = null;
  setLaunchOverrideForTest(null);
  useCommandStore.setState({ sources: {} });
});

beforeEach(() => {
  h.post.mockReset();
  h.rowId = "R";
  h.dataType = "text";
  h.nav = navWith(LOADED_NO_ORDER);
});

const start = (id = "col-1") => act(() => h.bodyProps.edit.start(id));
const input = () =>
  h.dataType === "json"
    ? screen.getByRole("textbox", { name: "JSON value" })
    : screen.getByPlaceholderText("Type the value");
const typeDraft = (draft) =>
  fireEvent.change(input(), { target: { value: draft } });
const apply = () =>
  fireEvent.click(screen.getByRole("button", { name: /Apply/ }));

it.each([
  ["text", "first line\nsecond line", "first line\nsecond line"],
  [
    "json",
    '{ "customer": { "tier": "enterprise" } }',
    '{"customer":{"tier":"enterprise"}}',
  ],
])(
  "keeps a refused %s draft mounted and retries the same value",
  async (type, draft, wire) => {
    h.dataType = type;
    const saved = type === "json" ? '{"customer":{"tier":"basic"}}' : "saved";
    h.nav = navWith({ row_id: "R", "col-1": { cell_value: saved } });
    h.post.mockRejectedValueOnce({
      response: { data: { result: "Value refused" } },
    });
    h.post.mockResolvedValueOnce({ data: { result: "Saved" } });
    render(ui());
    start();
    typeDraft(draft);
    await act(async () => {
      apply();
    });
    expect(input().value).toBe(draft);
    expect(h.bodyProps.edit.editingId).toBe("col-1");
    expect(h.nav.row["col-1"].cell_value).toBe(saved);
    expect(h.post.mock.calls).toEqual([
      [
        endpoints.develop.updateCellValue("ds-1"),
        {
          row_id: "R",
          column_id: "col-1",
          new_value: wire,
        },
      ],
    ]);
    await act(async () => {
      apply();
    });
    expect(h.post.mock.calls).toEqual([
      [
        endpoints.develop.updateCellValue("ds-1"),
        {
          row_id: "R",
          column_id: "col-1",
          new_value: wire,
        },
      ],
      [
        endpoints.develop.updateCellValue("ds-1"),
        {
          row_id: "R",
          column_id: "col-1",
          new_value: wire,
        },
      ],
    ]);
    expect(h.bodyProps.edit.editingId).toBeNull();
    expect(screen.queryByRole("button", { name: /Apply/ })).toBeNull();
  },
);

it.each(["another row", "another field", "reopened field"])(
  "does not close %s when an earlier save completes",
  async (destination) => {
    let finish;
    h.post.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { rerender } = render(ui());
    start();
    typeDraft("submitted");
    apply();
    if (destination === "another row") {
      h.rowId = "R2";
      h.nav = navWith({ row_id: "R2", "col-1": { cell_value: "other" } });
      rerender(ui());
    } else {
      act(() => h.bodyProps.edit.cancel());
    }
    const next = destination === "another field" ? "col-2" : "col-1";
    start(next);
    typeDraft("keep this newer draft");
    await act(async () => {
      finish({ data: { result: "Saved" } });
    });
    expect(h.bodyProps.edit.editingId).toBe(next);
    expect(input().value).toBe("keep this newer draft");
    expect(h.post).toHaveBeenCalledTimes(1);
  },
);

it("prevents repeat Apply and changes to a submitted draft while saving", async () => {
  let finish;
  h.post.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(ui());
  start();
  typeDraft("submitted once");
  apply();
  expect(input().disabled).toBe(true);
  expect(screen.getByRole("button", { name: /Apply/ }).disabled).toBe(true);
  await act(() => h.bodyProps.edit.applyRef.current());
  expect(h.post).toHaveBeenCalledTimes(1);
  await act(async () => {
    finish({ data: { result: "Saved" } });
  });
  expect(h.bodyProps.edit.editingId).toBeNull();
});
