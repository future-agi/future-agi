// Source tabs stay available in the ordinary build. An explicitly hidden
// source surface still hides its tabs and menu items. An offered tab with
// no usable source or saved conversation shows one line instead.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { palette } from "src/theme/palette";

const { h, Null } = vi.hoisted(() => ({
  h: {
    nav: null,
    shell: null,
    drawer: null,
    datasetKind: null,
    runsRow: null,
    cellEdits: {},
    sessionTurns: [],
    traceDetail: null,
    sessionRead: vi.fn(),
    traceRead: vi.fn(),
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
    default: { ...real.default, get: pending, post: pending },
  };
});
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: (props) => {
    h.drawer = props;
    return null;
  },
}));
vi.mock("src/api/develop/row-source", () => ({
  isRefusedSourceRead: () => false,
  useSourceSessionTraces: (sessionId, options) => {
    h.sessionRead(sessionId, options);
    return { data: options.enabled ? h.sessionTurns : undefined };
  },
}));
vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: (traceId, options) => {
    h.traceRead(traceId, options);
    return { data: traceId ? h.traceDetail : undefined };
  },
}));
vi.mock("src/components/iconify", () => ({ default: Null }));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => <>{children}</>,
}));
vi.mock("src/components/traceDetail/SpanTreeTimeline", () => ({
  default: ({ spans }) => (
    <div data-testid="span-tree">
      {spans.map((entry) => entry.observation_span.name).join(",")}
    </div>
  ),
}));
vi.mock("src/components/traceDetail/SpanDetailPane", () => ({ default: Null }));
vi.mock("src/sections/common/ValueViewer/ValueViewer", () => ({
  default: ({ value }) => <pre>{JSON.stringify(value)}</pre>,
}));
vi.mock("src/sections/common/ValueViewer/useValueViewMode", () => ({
  default: () => ["formatted", () => {}],
}));
vi.mock(
  "src/sections/annotations/queues/components/add-to-queue-dialog",
  () => ({ default: Null }),
);
// Keep the tab strip props and render the real selected body and cell readers.
vi.mock("src/sections/common/RowPanel/RowPanelShell", () => ({
  default: (props) => {
    h.shell = props;
    return <div data-testid="row-panel-body">{props.children}</div>;
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
  useRowRuns: () => ({ data: h.runsRow ? { row: h.runsRow } : undefined }),
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
  useDatasetKind: () => ({ kind: h.datasetKind, dataset: { name: "Support" } }),
}));
vi.mock("../../Versions/changeSetStore", () => ({
  METADATA_COLUMN: "__row_metadata__",
  ROW_STATUS_LABEL: {},
  useChangeSet: () => ({
    ops: [],
    byCell: h.cellEdits,
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
vi.mock("src/sections/common/Find/FindBar", () => ({
  default: () => <div data-testid="row-find">Find in this row</div>,
}));
vi.mock("../RowRawTab", () => ({ default: Null }));
vi.mock("../RowViewHeader", () => ({
  RowKeyStrip: Null,
  RowPositionText: Null,
  RowHeaderActions: Null,
  RowHeaderLine2: Null,
  RowHeaderMenu: Null,
}));
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

const theme = createTheme({ palette: palette("light") });
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
  defaultOptions: { queries: { retry: false, staleTime: Infinity } },
});
const setColumns = (columns) =>
  client.setQueryData(["dataset-detail-average", "ds-1"], {
    data: { result: { column_config: columns } },
  });
const conversationColumn = (dataType = "conversation") =>
  setColumns([{ id: "col-1", name: "messages", data_type: dataType }]);
const ui = (search) => (
  <QueryClientProvider client={client}>
    <ThemeProvider theme={theme}>
      <HelmetProvider>
        <MemoryRouter initialEntries={[`/dashboard/develop/ds-1?${search}`]}>
          <Routes>
            <Route path="/dashboard/develop/:dataset" element={<RowView />} />
          </Routes>
        </MemoryRouter>
      </HelmetProvider>
    </ThemeProvider>
  </QueryClientProvider>
);
const tabValues = () => (h.shell?.tabs ?? []).map((t) => t.value);
const menuItems = () => h.shell?.header?.menu?.props?.menuItems ?? {};
const launch = (claimed) =>
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    showUnreleased: false,
    madeReal: claimed ? [SURFACE] : [],
  });
const open = (source, search = "row=R1", claimed = true, rowOverrides = {}) => {
  launch(claimed);
  h.nav = navFor({ ...rowWith(source), ...rowOverrides });
  // useUrlState builds each write from window.location, so it carries the
  // router's URL; otherwise a tab switch would drop the open row.
  window.history.replaceState({}, "", `/dashboard/develop/ds-1?${search}`);
  return render(ui(search));
};
const expectMissingSource = (tab) => {
  expect(h.shell.tab).toBe(tab);
  const offered = h.shell.tabs.find((t) => t.value === tab);
  expect(offered).toBeTruthy();
  expect(offered).not.toHaveProperty("badge");
  expect(offered).not.toHaveProperty("off");
  expect(screen.getByTestId("row-panel-body")).toHaveTextContent(
    /^No source recorded for this row$/,
  );
  expect(screen.getAllByText("No source recorded for this row")).toHaveLength(
    1,
  );
  expect(screen.queryByTestId("row-find")).toBeNull();
  expect(screen.queryByTestId("session-turn")).toBeNull();
  expect(screen.queryByTestId("source-trace-tree")).toBeNull();
  expect(screen.queryByTestId("saved-row-copy")).toBeNull();
  expect(
    screen.getByTestId("row-panel-body").querySelector(".MuiChip-root"),
  ).toBeNull();
};
const expectRecordedContent = () => {
  expect(screen.queryByText("No source recorded for this row")).toBeNull();
  expect(screen.getByTestId("row-find")).toBeTruthy();
};

beforeEach(() => {
  h.shell = null;
  h.drawer = null;
  h.datasetKind = null;
  h.runsRow = null;
  h.cellEdits = {};
  h.sessionRead.mockClear();
  h.traceRead.mockClear();
  h.sessionTurns = [
    {
      trace_id: TRACE.trace_id,
      input: "Where is my parcel?",
      output: "On its way.",
    },
  ];
  h.traceDetail = {
    observation_spans: [
      {
        observation_span: {
          id: TRACE.span_id,
          name: "Recorded answer",
          parent_span_id: null,
          start_time: 0,
        },
        children: [],
      },
    ],
    summary: { total_spans: 1, total_latency_ms: 40 },
  };
  setColumns([{ id: "col-1", name: "input", data_type: "text" }]);
});
afterEach(() => {
  cleanup();
  client.clear();
  setLaunchOverrideForTest(null);
  window.history.replaceState({}, "", "/");
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
    expect(h.sessionRead).not.toHaveBeenCalled();
    expect(h.traceRead).not.toHaveBeenCalled();
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
    expect(screen.getByTestId("source-trace-tree")).toHaveTextContent(
      "Recorded answer",
    );
  });
});

describe("Row view source bodies", () => {
  it.each([
    ["trace", TRACE, ["trajectory"]],
    ["span", SPAN, ["trajectory"]],
    ["session", SESSION, ["conversation", "trajectory"]],
  ])(
    "offers the recorded %s tabs in an ordinary launch build",
    (_, source, tabs) => {
      setLaunchOverrideForTest({
        launch: true,
        experimentsBasic: false,
        madeReal: [],
      });
      h.nav = navFor(rowWith(source));
      render(ui("row=R1"));
      expect(
        tabValues().filter((t) => ["conversation", "trajectory"].includes(t)),
      ).toEqual(tabs);
    },
  );

  it.each([
    ["conversation", { type: "session" }],
    ["trajectory", { type: "trace", trace_id: "  " }],
  ])("shows the calm %s body in an ordinary launch build", (tab, source) => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: [],
    });
    h.nav = navFor(rowWith(source));
    render(ui(`row=R1&rowTab=${tab}`));
    expectMissingSource(tab);
  });

  it.each([
    ["trace", TRACE, "trajectory", "source-trace-tree"],
    ["span", SPAN, "trajectory", "source-trace-tree"],
    ["session conversation", SESSION, "conversation", "session-turn"],
    ["session trajectory", SESSION, "trajectory", "source-trace-tree"],
    [
      "expired trace",
      { ...TRACE, expired: true },
      "trajectory",
      "saved-row-copy",
    ],
    [
      "expired session",
      { ...SESSION, expired: true },
      "conversation",
      "saved-row-copy",
    ],
    [
      "kind alias",
      { ...SESSION, kind: "session", type: undefined },
      "conversation",
      "session-turn",
    ],
  ])("keeps the recorded %s body", (_, source, tab, testId) => {
    open(source, `row=R1&rowTab=${tab}`);
    expect(h.shell.tab).toBe(tab);
    expect(screen.getByTestId(testId)).toBeTruthy();
    expectRecordedContent();
  });

  it.each([
    ["absent identifier", { type: "session" }],
    ["null identifier", { ...SESSION, session_id: null }],
    ["empty identifier", { ...SESSION, session_id: "" }],
    ["blank identifier", { ...SESSION, session_id: "  " }],
    ["non-text identifier", { ...SESSION, session_id: 17 }],
    ["expired incomplete source", { type: "session", expired: true }],
    ["kind alias", { kind: "session", session_id: "" }],
  ])("shows one calm line for a session with an %s", (_, source) => {
    open(source, "row=R1&rowTab=conversation");
    expectMissingSource("conversation");
    expect(h.sessionRead).not.toHaveBeenCalled();
    expect(h.traceRead).not.toHaveBeenCalled();
  });

  it.each([
    ["trace", { ...TRACE, trace_id: "  " }],
    ["span", { ...SPAN, trace_id: "  " }],
    ["session", { ...SESSION, session_id: "  " }],
    ["non-text trace", { ...TRACE, trace_id: 17 }],
    ["non-text session", { ...SESSION, session_id: 17 }],
    ["untyped trace", { trace_id: "  " }],
  ])(
    "shows one calm line for an offered %s trajectory without a record",
    (_, source) => {
      open(source, "row=R1&rowTab=trajectory");
      expectMissingSource("trajectory");
      expect(h.sessionRead).not.toHaveBeenCalled();
      expect(h.traceRead).not.toHaveBeenCalled();
    },
  );

  it.each([undefined, null, "", "  ", 17])(
    "keeps a session's trace with an unusable session identifier: %j",
    (sessionId) => {
      open(
        { ...SESSION, trace_id: TRACE.trace_id, session_id: sessionId },
        "row=R1&rowTab=trajectory",
      );
      expect(screen.getByTestId("source-trace-tree")).toHaveTextContent(
        "Recorded answer",
      );
      expect(h.traceRead).toHaveBeenLastCalledWith(TRACE.trace_id, {
        projectId: TRACE.project_id,
      });
      expect(h.sessionRead).toHaveBeenLastCalledWith(sessionId, {
        enabled: false,
        projectId: TRACE.project_id,
      });
      expectRecordedContent();
      h.sessionRead.mockClear();
      h.traceRead.mockClear();
      act(() => h.shell.onTab("conversation"));
      expectMissingSource("conversation");
      expect(h.sessionRead).not.toHaveBeenCalled();
      expect(h.traceRead).not.toHaveBeenCalled();
    },
  );

  it("keeps the saved trajectory of an expired session with only a trace", () => {
    open(
      { type: "session", trace_id: TRACE.trace_id, expired: true },
      "row=R1&rowTab=trajectory",
    );
    expect(screen.getByTestId("saved-row-copy")).toHaveTextContent(
      "What is the refund window?",
    );
    expect(h.traceRead).toHaveBeenLastCalledWith(null, {
      projectId: undefined,
    });
    expectRecordedContent();
  });

  it.each(["", "  ", 17])(
    "validates each tab separately when a session has an unusable trace: %j",
    (traceId) => {
      open({ ...SESSION, trace_id: traceId }, "row=R1&rowTab=conversation");
      expect(screen.getByTestId("session-turn")).toHaveTextContent(
        "On its way.",
      );
      expect(h.sessionRead).toHaveBeenLastCalledWith(SESSION.session_id, {
        enabled: true,
        projectId: SESSION.project_id,
      });
      expectRecordedContent();
      act(() => h.shell.onTab("trajectory"));
      expectMissingSource("trajectory");
      expect(h.traceRead).not.toHaveBeenCalled();
    },
  );

  it.each([
    ["missing", null],
    ["empty", {}],
    ["array", []],
    ["non-object", "session"],
    ["metadata-only", { label: "Imported" }],
    ["trace-only", TRACE],
    ["untyped session", { session_id: SESSION.session_id }],
  ])(
    "shows one calm line for a conversation-kind row with a %s source",
    (_, source) => {
      open(source, "row=R1&rowTab=conversation", true, {
        kind: "conversation",
      });
      expectMissingSource("conversation");
    },
  );

  it.each([
    [null, "json"],
    [{}, "conversation"],
    [{ type: "session" }, "text"],
    [{ label: "Imported" }, "json"],
  ])(
    "keeps saved conversation content with source %j in a %s column",
    (source, dataType) => {
      conversationColumn(dataType);
      open(source, "row=R1&rowTab=conversation", true, {
        kind: "conversation",
        "col-1": {
          cell_value: [
            { role: "user", content: "Hello" },
            { role: "assistant", content: "How can I help?" },
          ],
        },
      });
      expect(screen.getByText("Hello")).toBeTruthy();
      expect(screen.getByText("How can I help?")).toBeTruthy();
      expectRecordedContent();
    },
  );

  it.each(["saved", "pending"])(
    "keeps a %s system-only conversation without a source",
    (location) => {
      conversationColumn();
      const value = [
        { role: "system", content: "Respond in short sentences." },
      ];
      if (location === "pending") {
        h.cellEdits = { R1: { "col-1": { after: value } } };
      }
      open(null, "row=R1&rowTab=conversation", true, {
        kind: "conversation",
        "col-1": { cell_value: location === "saved" ? value : null },
      });
      expect(screen.getByText("System prompt")).toBeTruthy();
      expect(screen.getByText("Respond in short sentences.")).toBeTruthy();
      expect(screen.getByText("0 turns")).toBeTruthy();
      expectRecordedContent();
      expect(h.sessionRead).not.toHaveBeenCalled();
    },
  );

  it.each(["saved", "pending"])(
    "keeps a %s conversation verdict without turns or a source",
    (location) => {
      conversationColumn();
      const value = {
        turns: [],
        resolution: "Pass",
        resolution_reason: "The request was resolved.",
      };
      if (location === "pending") {
        h.cellEdits = { R1: { "col-1": { after: value } } };
      }
      open(null, "row=R1&rowTab=conversation", true, {
        kind: "conversation",
        "col-1": { cell_value: location === "saved" ? value : null },
      });
      expect(screen.getByTestId("conversation-verdict")).toHaveTextContent(
        'Conversation: Pass · Resolution · "The request was resolved."',
      );
      expect(screen.getByText("0 turns")).toBeTruthy();
      expectRecordedContent();
    },
  );

  it("uses the renderer's selected conversation rather than any nonempty cell", () => {
    setColumns([
      { id: "col-1", name: "conversation", data_type: "conversation" },
      { id: "col-2", name: "messages", data_type: "json" },
    ]);
    open(null, "row=R1&rowTab=conversation", true, {
      kind: "conversation",
      "col-1": { cell_value: [] },
      "col-2": {
        cell_value: [{ role: "user", content: "Another conversation" }],
      },
    });
    expect(screen.getByText("0 turns")).toBeTruthy();
    expect(screen.queryByText("Another conversation")).toBeNull();
    expectRecordedContent();
  });

  it("shows one calm line when both source and saved conversation are absent", () => {
    conversationColumn();
    open({}, "row=R1&rowTab=conversation", true, {
      kind: "conversation",
      "col-1": { cell_value: null },
    });
    expectMissingSource("conversation");
  });

  it("keeps a conversation from the pending cell value without a source", () => {
    conversationColumn("json");
    h.cellEdits = {
      R1: {
        "col-1": { after: [{ role: "user", content: "Hello" }] },
      },
    };
    open(null, "row=R1&rowTab=conversation", true, { kind: "conversation" });
    expect(screen.getByText("Hello")).toBeTruthy();
    expectRecordedContent();
  });

  it("uses the same calm line when the dataset supplies the conversation kind", () => {
    h.datasetKind = "conversations";
    open({}, "row=R1&rowTab=conversation");
    expectMissingSource("conversation");
  });

  it("uses the same calm line when the row read supplies the conversation kind", () => {
    h.runsRow = { kind: "conversation" };
    open({}, "row=R1&rowTab=conversation");
    expectMissingSource("conversation");
  });

  it.each([
    null,
    {},
    { label: "Imported" },
    { type: "trace" },
    { type: "span", trace_id: "" },
  ])(
    "adds no source tab to an ordinary row without a source identifier: %j",
    (source) => {
      open(source, "row=R1&rowTab=trajectory");
      expect(tabValues()).not.toContain("conversation");
      expect(tabValues()).not.toContain("trajectory");
      expect(h.shell.tab).toBe("row");
      expect(screen.queryByText("No source recorded for this row")).toBeNull();
    },
  );

  it("shows the same single line when switching between incomplete session tabs", () => {
    open({ ...SESSION, session_id: "  " });
    act(() => h.shell.onTab("conversation"));
    expectMissingSource("conversation");
    act(() => h.shell.onTab("trajectory"));
    expectMissingSource("trajectory");
  });

  it("replaces the calm line when the source record arrives", () => {
    const search = "row=R1&rowTab=conversation";
    const view = open({ type: "session" }, search);
    expectMissingSource("conversation");
    h.nav = navFor(rowWith(SESSION));
    view.rerender(ui(search));
    expect(screen.getByTestId("session-turn")).toHaveTextContent("On its way.");
    expectRecordedContent();
  });

  it("returns to Row when the next ordinary row has no source", () => {
    const search = "row=R1&rowTab=conversation";
    const view = open({ type: "session" }, search);
    expectMissingSource("conversation");
    h.nav = navFor({ ...rowWith(null), row_id: "R2" });
    view.rerender(ui(search));
    expect(tabValues()).not.toContain("conversation");
    expect(tabValues()).not.toContain("trajectory");
    expect(h.shell.tab).toBe("row");
    expect(screen.queryByText("No source recorded for this row")).toBeNull();
  });
});
