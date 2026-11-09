import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Helmet } from "react-helmet-async";
import { Box, Chip, useMediaQuery } from "@mui/material";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { useAuthContext } from "src/auth/hooks";
import axios, { endpoints } from "src/utils/axios";
import { ROLES } from "src/utils/rolePermissionMapping";
import { useUrlState } from "src/routes/hooks/use-url-state";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import AddToQueueDialog from "src/sections/annotations/queues/components/add-to-queue-dialog";
import RowPanelShell from "src/sections/common/RowPanel/RowPanelShell";
import ShortcutSheet from "src/sections/common/RowPanel/ShortcutSheet";
import { useEvalsList } from "src/sections/common/EvaluationDrawer/getEvalsList";
import { useDatasetSplitList, useRowRuns } from "src/api/develop/row-view";
import { copyText } from "src/sections/common/ux/CopyButton";
import { notify } from "src/sections/common/ux/toast";
import ConfirmDialog from "src/sections/common/ux/ConfirmDialog";
import Hint, {
  readDismissed,
  useHintStore,
} from "src/sections/common/ux/oneview/Hint";
import {
  PROTO_USER_ID,
  useLayoutUserId,
} from "src/sections/common/ux/oneview/layoutPrefs";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import { useOpenSaveVersion } from "../Versions/PendingChangesGate";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import {
  isExpiredSource,
  rowKindOf,
} from "src/sections/develop-detail/TaskSuite/itemModel";
import {
  METADATA_COLUMN,
  useChangeSet,
  useChangeSetStore,
} from "../Versions/changeSetStore";
import {
  findAddRowOp,
  isBlankAddRow,
  isImportedRowId,
} from "../Versions/pendingRows";
import {
  latestOf,
  useAsOfVersion,
  useDatasetVersions,
} from "src/api/develop/dataset-versions";
import RestoreRowsDialog, {
  useRestoreRowsDialog,
} from "../Versions/RestoreRowsDialog";
import { restorableRows } from "../Versions/versionText";
import RowViewBody from "./RowViewBody";
import FindBar from "src/sections/common/Find/FindBar";
import RowRawTab from "./RowRawTab";
import { RowBanner, deletedRowText } from "./RowViewStates";
import { RowKeyStrip, RowPositionText } from "./RowViewHeader";
import ConversationTab from "./ConversationTab";
import SourceTrajectoryTab from "./SourceTrajectoryTab";
import { opensTrace, shownRowSource } from "./rowSource";
import {
  RowHeaderActions,
  RowHeaderLine2,
  RowHeaderMenu,
} from "./RowViewHeader";
import { DraftRowChip, UnsavedPill } from "./RowHeaderPills";
import RowIdChip from "./RowIdChip";
import { ROW_STATUS_LABEL } from "../Versions/changeSetStore";
import useRowActions from "./useRowActions";
import { activityBadge, rowViewGate } from "./rowAccessGate";
import useDatasetAccess from "src/sections/common/Access/useDatasetAccess";
import { useRowComments } from "src/api/develop/row-collab";
import useRowNavigation from "./useRowNavigation";
import useRowViewShortcuts from "./useRowViewShortcuts";
import useRowViewUrl from "./useRowViewUrl";
import { gridWritesClassic } from "src/sections/develop/realBackendCaps";
import { refusalSentence, writeCellClassic } from "../Versions/classicWrites";
import { rowViewKeys } from "src/api/develop/row-view";
import { useDevelopDetailContext } from "src/sections/develop-detail/Context/DevelopDetailContext";
import {
  buildRowModel,
  effectiveSplitIds,
  rowAsJson,
  rowHasPending,
  rowNumberOf,
  withEdits,
} from "./rowModel";
import { isSurfaceShown, isRowTabShown } from "src/launch/launchSwitch";

// Below this viewport width the open Row view covers the grid (RV#16).
const OVERLAY_BELOW = 1280;
// The shell's root (ResizableSplit) stays the flex child it was.
const ROW_SLOT_SX = {
  display: "flex",
  flex: 1,
  minWidth: 0,
  minHeight: 0,
  position: "relative",
};
// Root > [grid pane, splitter, row pane]: the row pane floats over the
// grid's right side; the splitter goes (the overlay has a fixed width).
const ROW_OVERLAY_SX = {
  ...ROW_SLOT_SX,
  [`@media (min-width:${OVERLAY_BELOW}px)`]: {
    "& > .MuiBox-root": { position: "relative" },
    "& > .MuiBox-root > [role=separator]": { display: "none !important" },
    "& > .MuiBox-root > .MuiBox-root:last-of-type": {
      position: "absolute",
      top: 0,
      right: 0,
      bottom: 0,
      width: "min(640px, 50vw) !important",
      zIndex: 6,
      borderLeft: "1px solid",
      borderColor: "divider",
      boxShadow: "-12px 0 32px -12px rgba(0,0,0,0.18)",
    },
  },
};

const useColumns = (datasetId, enabled) =>
  useQuery({
    // Same query the grid uses for its header averages (deduped).
    queryKey: ["dataset-detail-average", datasetId],
    queryFn: () =>
      axios.get(endpoints.develop.getDatasetDetail(datasetId), {
        params: { column_config_only: true },
      }),
    select: (d) => d.data?.result?.column_config ?? [],
    enabled: Boolean(datasetId) && enabled,
  });

/**
 * The dataset Row view (PRODUCT-SPEC §3.4): the docked, non-modal panel
 * beside the Data grid for the row in `?row=`. Always mounted (closed it
 * renders the grid alone) so the grid never remounts.
 */
export const ROW_KEYS_HINT = "row.keys";
// IT-R2 G5 P6: the key strip goes after the first j / k (one use teaches it).
const JK_DONE = 1;
const JK_KEY = "fa.hints.row.keys.jk";

/** J / K presses in the row view, remembered (the key tip's `doneWhen`). */
function useJkCount() {
  const [n, setN] = useState(() => {
    try {
      return Number(window.localStorage.getItem(JK_KEY)) || 0;
    } catch {
      return 0;
    }
  });
  const bump = () =>
    setN((cur) => {
      if (cur >= JK_DONE) return cur;
      const next = cur + 1;
      try {
        window.localStorage.setItem(JK_KEY, String(next));
      } catch {
        // storage blocked: this session only
      }
      return next;
    });
  return [n, bump];
}

/** True until the tip was dismissed (so the shell gets no empty footer). */
function useTipShown(id) {
  const userId = useLayoutUserId();
  const list = useHintStore((st) => st.dismissed[userId || PROTO_USER_ID]);
  return !(list ?? readDismissed(userId)).includes(id);
}

const RowView = ({ grid, suppressed = false }) => {
  const { dataset: datasetId } = useParams();
  const navigate = useNavigate();
  const url = useRowViewUrl();
  const [versionParam, setVersionParam] = useUrlState("version", null);
  const [, setHistory] = useUrlState("history", null);
  const open = Boolean(url.rowId) && !suppressed;
  const rowId = open ? url.rowId : null;
  const { role } = useAuthContext();
  const viewer = role === ROLES.VIEWER || role === ROLES.WORKSPACE_VIEWER;
  const asOf = versionParam != null && versionParam !== "";

  const wide = useMediaQuery("(min-width:1440px)");
  // A1-18: the key strip is a footer tip until the user has moved rows
  // with J / K three times (or hides it); then `?` in the header remains.
  const [jk, bumpJk] = useJkCount();
  const keysTipShown = useTipShown(ROW_KEYS_HINT) && jk < JK_DONE;
  const nav = useRowNavigation({ datasetId, rowId, openRow: url.openRow });
  const row = nav.row;
  // A pending new row (`?row=new-…`, Add row / Duplicate): edited here,
  // nothing on the server until a version is saved.
  const isNew = nav.isPending;
  // IT-R3 G5 P1: a row an import added (`imp-…`) opens read-only from the
  // pending change set, with every tab its payload can fill.
  const isImported = isNew && isImportedRowId(rowId);
  const { data: columns = [] } = useColumns(datasetId, open);
  const { data: evalsData } = useEvalsList(
    open ? datasetId : undefined,
    { eval_type: "user" },
    "dataset",
  );
  const { data: splitData } = useDatasetSplitList(open ? datasetId : undefined);
  // L6 launch switch: no row runs / comments reads where they are hidden
  // (dataset.rowExtras.runs, dataset.collab.comments: no dev endpoint).
  const rowRunsShown = isSurfaceShown("dataset.rowExtras.runs");
  const runsQ = useRowRuns(
    datasetId,
    rowId,
    {},
    rowRunsShown ? {} : { enabled: false },
  );
  const changeSet = useChangeSet(datasetId);
  const { kind: datasetKind, dataset } = useDatasetKind(datasetId);
  const openSave = useOpenSaveVersion(datasetId);

  const pendingDelete = Boolean(
    rowId && changeSet.deletedRowIds?.includes(rowId),
  );
  // the interface contract §9 (M6): edit also needs edit access on the dataset.
  const access = useDatasetAccess(datasetId);
  // the interface contract: as of an older version the Row view is read-only everywhere
  // (no edit, no annotate / queue / comment / new experiment).
  const gate = rowViewGate({
    viewer,
    asOf,
    pendingDelete,
    isImported,
    access,
  });
  const { canEdit } = gate;
  const { data: rowThreads } = useRowComments(datasetId, rowId, {
    runId: "all",
    ...(isSurfaceShown("dataset.collab.comments") ? {} : { enabled: false }),
  });
  const { data: versions } = useDatasetVersions(datasetId, {
    enabled: Boolean(datasetId) && isImported,
  });
  const draftVersion = (latestOf(versions)?.number ?? 0) + 1;
  // `New row 3` = its place among the pending rows (no row number yet).
  const pendingNo = isNew
    ? (changeSet.ops ?? [])
        .filter((o) => o.op === "add_row")
        .findIndex((o) => o.temp_id === rowId) + 1
    : 0;
  const model = useMemo(
    () =>
      withEdits(
        buildRowModel(columns, row, evalsData?.evals ?? []),
        changeSet.byCell?.[rowId] ?? {},
        row?.row_meta?.metadata ?? {},
        METADATA_COLUMN,
      ),
    [columns, row, evalsData, changeSet.byCell, rowId],
  );

  // The Row tab always renders formatted; raw JSON is the Raw tab (R2D-20).
  const mode = "formatted";
  const setMode = (m) => url.setRowTab(m === "raw" ? "raw" : "row");
  const [diffOn, setDiffOn] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [queueAnchor, setQueueAnchor] = useState(null);
  const [traceOpen, setTraceOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [collapseTick, setCollapseTick] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [splitAnchor, setSplitAnchor] = useState(null);
  const applyRef = useRef(null);
  const anchorRef = useRef(null);
  const splitChipRef = useRef(null);
  useEffect(() => setEditingId(null), [rowId]);

  // The new row's op is gone: Save version / Discard / Discard row (each
  // says so itself), or a reload or old link (the change set lives in this
  // tab only), which gets its own toast.
  const sawPendingOp = useRef(null);
  useEffect(() => {
    if (!open || !isNew) return;
    if (!nav.pendingGone) {
      sawPendingOp.current = rowId;
      return;
    }
    const seen = sawPendingOp.current === rowId;
    sawPendingOp.current = null;
    url.closeRow();
    if (!seen)
      notify(
        "That new row was never saved, so it is gone. Add row starts a fresh one.",
        { key: "row-new-gone" },
      );
  }, [open, isNew, nav.pendingGone, rowId]); // eslint-disable-line react-hooks/exhaustive-deps

  const actions = useRowActions({ datasetId, rowId, row, columns, changeSet });
  // L1 (the capability contract): on the real backend a field edit is written at
  // once (update_cell_value); there is no change-set to hold it.
  const classic = gridWritesClassic();
  const queryClient = useQueryClient();
  const { refreshGrid } = useDevelopDetailContext();
  const writeNow = (columnId, value) =>
    writeCellClassic({ datasetId, rowId, columnId, value }).then(
      () => {
        queryClient.invalidateQueries({ queryKey: rowViewKeys.all(datasetId) });
        refreshGrid?.();
        notify("Saved", { key: "row-field-save", variant: "success" });
      },
      (e) =>
        notify(refusalSentence(e, "Couldn't save that field."), {
          key: "row-field-save",
          variant: "error",
        }),
    );
  const discardNew = () => {
    const ops = useChangeSetStore.getState().getOps(datasetId);
    const op = findAddRowOp(ops, rowId);
    if (!op) return;
    changeSet.addOp({ op: "delete_rows", row_ids: [rowId] });
    notify("Discarded the new row", {
      key: "row-new-discard",
      undo: {
        ms: 10000,
        onUndo: () => {
          changeSet.addOp(op);
          url.openRow(op.temp_id);
        },
      },
    });
  };
  const askDelete = () => (isNew ? discardNew() : setConfirmDelete(true));

  const original = (colId) => {
    const all = [
      ...model.input,
      ...model.expected,
      ...model.metadata,
      ...model.noRole,
    ];
    return all.find((f) => f.column.id === colId)?.original ?? "";
  };
  const edit = {
    canEdit,
    editingId,
    applyRef,
    metaKey: METADATA_COLUMN,
    start: (id) => canEdit && setEditingId(id),
    cancel: () => setEditingId(null),
    apply: (id, value) => {
      if (classic) {
        setEditingId(null);
        if (id === METADATA_COLUMN) {
          notify("Row metadata can't be edited on this workspace yet.", {
            key: "row-field-save",
            variant: "error",
          });
          return;
        }
        writeNow(id, value);
        return;
      }
      if (id === METADATA_COLUMN) {
        let metadata = {};
        try {
          metadata = value ? JSON.parse(value) : {};
        } catch {
          return;
        }
        changeSet.addOp({
          op: "set_row_metadata",
          row_id: rowId,
          metadata,
          before: model.rowMetadata.original,
        });
      } else {
        changeSet.addOp({
          op: "update_cell",
          row_id: rowId,
          column_id: id,
          value,
          before: original(id),
        });
      }
      setEditingId(null);
    },
    revert: (id) => changeSet.revert(rowId, id),
  };

  // Add row opens a blank row ready to type: the first input field (else
  // the first expected one) is in edit, once per row.
  const autoEdited = useRef(null);
  const blankNew = isNew && isBlankAddRow(findAddRowOp(changeSet.ops, rowId));
  useEffect(() => {
    if (!open || !blankNew || !canEdit || !columns.length) return;
    if (autoEdited.current === rowId) return;
    const first = model.input[0]?.column.id ?? model.expected[0]?.column.id;
    if (!first) return;
    autoEdited.current = rowId;
    if (url.rowTab !== "row") url.setRowTab("row");
    setEditingId(first);
  }, [open, blankNew, canEdit, columns.length, rowId]); // eslint-disable-line react-hooks/exhaustive-deps

  // `edit=expected` (one-shot link) opens the first Expected editor.
  useEffect(() => {
    if (!url.editField || !row || !columns.length) return;
    const first = model.expected[0]?.column.id;
    if (first && canEdit) setEditingId(first);
    url.clearEditField();
  }, [url.editField, row, columns.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const splitById = splitData?.byId ?? {};
  const memberIds = effectiveSplitIds(
    row?.row_meta?.split_ids,
    changeSet.ops,
    rowId,
  );
  const splits = memberIds.map((id) => splitById[id]).filter(Boolean);
  // A build that hides the row source surface shows no source anywhere.
  const source = shownRowSource(row?.row_meta?.source);
  const pendingSt = changeSet.pendingStatus?.[rowId];
  const statusShown =
    pendingSt ??
    (row?.row_meta?.status
      ? { status: row.row_meta.status, reason: row.row_meta.status_reason }
      : null);
  const openQueue = (el) => {
    if (gate.annotate) setQueueAnchor(el ?? anchorRef.current);
  };
  // the interface contract (DV-06): a row deleted after the viewed version (or by it) opens
  // read-only from the as-of read; `Restore this row…` brings it back with
  // its id (N2 restore_rows). Absent for viewers.
  const asOfInfo = useAsOfVersion(datasetId);
  const rowChange = asOf ? row?.row_meta?.change ?? null : null;
  const restoreReq =
    asOf && row ? restorableRows([row], Number(versionParam)) : null;
  // the interface contract review: a row this version deleted that is back in the latest
  // version (restored since) says so, with no Restore.
  const isDeleted = ["deleted", "deleted_later"].includes(rowChange?.kind);
  const deletedRow =
    isDeleted && (restoreReq || rowChange?.in_latest)
      ? {
          version:
            rowChange?.kind === "deleted"
              ? Number(versionParam)
              : rowChange?.version ?? null,
          latest: asOfInfo.latest?.number ?? null,
          inLatest: Boolean(rowChange?.in_latest),
          restoredIn: rowChange?.restored_in ?? null,
          canRestore:
            Boolean(restoreReq) && !viewer && access.can.edit !== false,
        }
      : null;
  const openRestore = () =>
    restoreReq && useRestoreRowsDialog.getState().open(restoreReq);
  const openHistory = () => setHistory("1", { replace: false });
  // PF-4 (PUX-8): no door to the version history where the build hides it.
  const historyDoor = isSurfaceShown("dataset.versions")
    ? openHistory
    : undefined;
  const openVersion = (n) => {
    setVersionParam(String(n));
    setDiffOn(true);
  };

  const toggleRaw = () => url.setRowTab(url.rowTab === "raw" ? "row" : "raw");
  const startEdit = () => {
    if (url.rowTab !== "row") url.setRowTab("row");
    const first = model.expected[0]?.column.id ?? model.input[0]?.column.id;
    if (first) edit.start(first);
  };
  const copyRowId = async () => {
    if (isNew) {
      notify("A new row gets its id when you save a version", {
        key: "row-id-copy",
      });
      return;
    }
    if (rowId && (await copyText(rowId)))
      notify(`Copied row id ${rowId.slice(-8)}`, { key: "row-id-copy" });
  };
  const saveVersion = () => {
    if (changeSet.hasPending) openSave();
    else
      notify("Nothing to save yet. Edit a field or change a split first.", {
        key: "save-nothing",
      });
  };
  const openSplits = () => {
    if (!canEdit) return;
    if (url.rowTab !== "row") url.setRowTab("row");
    setSplitAnchor(splitChipRef.current ?? anchorRef.current);
  };
  const rowJson = () => JSON.stringify(rowAsJson(columns, row), null, 2);
  // `1`–`4` pick the visible tabs in order (filled in below).
  const tabByKey = useRef([]);

  useRowViewShortcuts({
    enabled: open && !traceOpen,
    full: url.full,
    setFull: url.setFull,
    closeRow: url.closeRow,
    goNext: () => {
      bumpJk();
      nav.goNext();
    },
    goPrev: () => {
      bumpJk();
      nav.goPrev();
    },
    // RV#8: `D` only toggles when there is something to diff.
    onDiff: () => {
      if (asOf || (model.expected.length && model.outputs.length))
        setDiffOn((d) => !d);
      else
        notify("Nothing to diff: no run output on this row yet", {
          key: "row-diff-none",
        });
    },
    onRaw: toggleRaw,
    onEdit: startEdit,
    onSubmit: () => applyRef.current?.(),
    onCopyLink: actions.copyLink,
    onAddToQueue: () => openQueue(),
    onHelp: () => setHelpOpen(true),
    onTab: (e) => {
      const t = tabByKey.current[Number(e?.key) - 1];
      if (t) url.setRowTab(t);
    },
    onSplits: openSplits,
    onCollapseAll: () => {
      if (url.rowTab !== "row") url.setRowTab("row");
      setCollapseTick((n) => n + 1);
    },
    onCopyRowId: copyRowId,
    onSaveVersion: saveVersion,
    // A1-15: `/` focuses Find field on the Row and Raw tabs.
    onFindField:
      url.rowTab === "raw" || url.rowTab === "row"
        ? () => {
            const el = document.querySelector(
              '[data-testid="row-panel"] input[data-fa-search="row"]',
            );
            el?.focus();
            el?.select?.();
          }
        : undefined,
  });

  const counts = row?.row_meta?.counts ?? {};
  // R2 (P5): tabs by row kind / source; bodies are L5b's. The wire row
  // carries no `kind` yet, so the dataset's kind decides (conversations →
  // conversation).
  // R2D-22: the row's own `kind` (N7 `row`, else the row read), then the
  // dataset's kind as the fallback.
  const wireRow = runsQ.data?.row;
  const rowKind = rowKindOf(
    wireRow?.kind ? { kind: wireRow.kind } : row,
    datasetKind,
  );
  const sourceKind = source?.kind ?? source?.type ?? null;
  const isConversation = rowKind === "conversation" || sourceKind === "session";
  // A session source that is still in Observe has traces to show; an
  // expired session has only the conversation copy.
  const hasTrajectory = Boolean(
    source?.trace_id ||
      (sourceKind === "session" &&
        source?.session_id &&
        !isExpiredSource(source)),
  );
  // L6 launch switch (ROW_TAB_SURFACES): Runs, Activity and the kind tabs
  // read row runs / activity paths dev does not have; each tab
  // shows with its own claim.
  const kindTabs =
    isNew && !isImported
      ? []
      : [
          ...(isConversation && isRowTabShown("conversation")
            ? [{ value: "conversation", label: "Conversation" }]
            : []),
          ...(hasTrajectory && isRowTabShown("trajectory")
            ? [{ value: "trajectory", label: "Trajectory" }]
            : []),
        ];
  const KindBody =
    isNew && !isImported
      ? null
      : {
          conversation:
            isConversation && isRowTabShown("conversation")
              ? ConversationTab
              : null,
          trajectory:
            hasTrajectory && isRowTabShown("trajectory")
              ? SourceTrajectoryTab
              : null,
        }[url.rowTab];
  // A kind tab that does not apply to this row shows the Row tab.
  const rowTab =
    (["conversation", "trajectory"].includes(url.rowTab) && !KindBody) ||
    !isRowTabShown(url.rowTab)
      ? "row"
      : url.rowTab;
  // A1-16: `Row · Raw · [kind] · Runs · Annotations · Activity` for
  // everyone; `R` toggles Row ↔ Raw.
  const tabs = [
    { value: "row", label: "Row" },
    { value: "raw", label: "Raw" },
    ...kindTabs,
    { value: "runs", label: "Runs", badge: counts.runs ?? 0 },
    {
      value: "annotations",
      label: "Annotations",
      badge: counts.annotations ?? 0,
    },
    {
      value: "activity",
      label: "Activity",
      // N8 = the version / queue / score history + one event per run.
      // + the row's comment threads (the All chip counts them; M6).
      // With comments alone (no history claim) it counts the threads.
      badge: isSurfaceShown("dataset.rowExtras.activity")
        ? activityBadge(counts, rowThreads?.threads)
        : rowThreads?.threads?.length ?? 0,
    },
  ].filter((t) => isRowTabShown(t.value));
  // `Runs` says what it holds on hover (the shell's tabs take string
  // labels; its tooltip prop is a G follow-up).
  useEffect(() => {
    if (!open) return;
    document
      .querySelector('[data-testid="row-tab-runs"]')
      ?.setAttribute("title", "Runs across experiments");
  });
  // `1`–`4` = the first four visible tabs, in screen order.
  tabByKey.current = tabs.slice(0, 4).map((t) => t.value);
  const n = rowNumberOf(row);
  // PF-1: no number yet (a deep link before get-row-data answers, or a
  // backend that sends no `order`): the labels say "this row", never "null".
  const rowLabel = n ? `Row ${n}` : "this row";
  const pending = rowHasPending(changeSet.ops, rowId);
  const rowLink = () =>
    typeof window === "undefined" ? "" : window.location.href;

  useRegisterCommands(
    "row-view",
    open && row
      ? [
          {
            id: "row.next",
            label: "Go to next row",
            shortcut: "next",
            icon: "mdi:chevron-down",
            run: nav.goNext,
            when: () => !nav.nextDisabled,
          },
          {
            id: "row.prev",
            label: "Go to previous row",
            shortcut: "prev",
            icon: "mdi:chevron-up",
            run: nav.goPrev,
            when: () => !nav.prevDisabled,
          },
          {
            id: "row.edit",
            label: "Edit this row",
            shortcut: "edit",
            icon: "mdi:pencil-outline",
            run: startEdit,
            when: () => canEdit,
          },
          {
            id: "row.copyId",
            label: "Copy row id",
            shortcut: "copyRowId",
            icon: "mdi:identifier",
            run: copyRowId,
            when: () => !isNew,
          },
          {
            id: "row.copyLink",
            label: "Copy link to this row",
            shortcut: "copyLink",
            icon: "mdi:link-variant",
            run: actions.copyLink,
            when: () => !isNew,
          },
          {
            id: "row.raw",
            label:
              url.rowTab === "raw"
                ? "Show formatted fields"
                : "Show this row as raw JSON",
            shortcut: "raw",
            icon: "mdi:code-json",
            run: toggleRaw,
          },
          {
            id: "row.copyJson",
            label: "Copy row as JSON",
            icon: "mdi:content-copy",
            run: async () => {
              if (await copyText(rowJson()))
                notify(`Copied ${rowLabel} as JSON`, { key: "row-json-copy" });
            },
          },
          {
            id: "row.splits",
            label: "Change splits of this row",
            shortcut: "splits",
            icon: "mdi:tag-multiple-outline",
            run: openSplits,
            when: () => canEdit,
          },
          {
            id: "row.queue",
            label: "Add this row to an annotation queue",
            shortcut: "addToQueue",
            icon: "mdi:account-check-outline",
            run: () => openQueue(),
            when: () => gate.annotate,
          },
          {
            id: "row.restore",
            label: `Restore ${rowLabel}…`,
            icon: "mdi:restore",
            run: openRestore,
            when: () => Boolean(deletedRow?.canRestore),
          },
          {
            id: "row.full",
            label: url.full
              ? "Show the grid beside the row"
              : "Expand the row view",
            shortcut: "full",
            icon: "mdi:arrow-expand",
            run: () => url.setFull(!url.full),
          },
          {
            id: "row.duplicate",
            label: "Duplicate this row",
            icon: "mdi:content-duplicate",
            run: actions.duplicate,
            when: () => canEdit,
          },
          {
            id: "row.delete",
            label: isNew ? "Discard this new row" : `Delete ${rowLabel}…`,
            icon: "mdi:delete-outline",
            run: askDelete,
            when: () => canEdit,
          },
          {
            id: "row.saveVersion",
            label: "Save a new version…",
            shortcut: "saveVersion",
            icon: "mdi:content-save-outline",
            run: saveVersion,
            when: () => changeSet.hasPending,
          },
          {
            id: "row.history",
            label: "Open version history",
            icon: "mdi:history",
            run: () => openHistory(),
            when: () => isSurfaceShown("dataset.versions"),
          },
          {
            id: "row.help",
            label: "Row view keyboard shortcuts",
            shortcut: "help",
            icon: "mdi:keyboard-outline",
            run: () => setHelpOpen(true),
          },
        ]
      : [],
    [
      open,
      rowId,
      // PF-1: a deep link registers [] while the row loads; the loaded row
      // must re-register even when its number stays null.
      Boolean(row),
      n,
      url.rowTab,
      url.full,
      canEdit,
      viewer,
      changeSet.hasPending,
      gate.annotate,
      deletedRow?.canRestore,
    ],
  );
  // A new row is not on the server yet: queue / experiment / copy wait
  // for Save version.
  const NEW_HINT = "Save a version first";
  const copyRowJson = async () => {
    if (await copyText(rowJson()))
      notify(`Copied ${rowLabel} as JSON`, { key: "row-json-copy" });
  };
  const downloadItem = {
    onClick: actions.download,
    disabled: !access.can.download,
    hint: access.can.download ? undefined : access.reason("download"),
  };
  // the interface contract (DM-07): a live source opens its trace; an expired one opens the
  // copy saved at import (Trajectory, or Conversation for a session), never
  // a live-looking Observe drawer.
  const sourceItems = isExpiredSource(source)
    ? hasTrajectory || isConversation
      ? {
          openSaved: {
            onClick: () =>
              url.setRowTab(hasTrajectory ? "trajectory" : "conversation"),
          },
        }
      : {}
    : opensTrace(source)
      ? { openTrace: { onClick: () => setTraceOpen(true) } }
      : {};
  // the interface contract: as of an older version the ⋯ menu holds read items only.
  const asOfMenuItems = {
    ...sourceItems,
    copyJson: { onClick: copyRowJson },
    download: downloadItem,
    ...(historyDoor ? { history: { onClick: historyDoor } } : {}),
  };
  const latestMenuItems = {
    ...sourceItems,
    addToQueue: {
      onClick: () => openQueue(),
      disabled: isNew,
      hint: isNew ? NEW_HINT : undefined,
    },
    // L6 launch switch: no New experiment door without the drawer, and none
    // that preselects this row while dev runs every row (data:rows:these).
    ...(isSurfaceShown("exp.new.scope")
      ? {
          newExperiment: {
            onClick: actions.newExperiment,
            disabled: isNew,
            hint: isNew ? NEW_HINT : undefined,
          },
        }
      : {}),
    duplicate: { onClick: actions.duplicate },
    copyTo: {
      onClick: actions.selectInGrid,
      disabled: isNew,
      hint: isNew ? NEW_HINT : undefined,
    },
    // the interface contract §9 (M6): the row's JSON is a download of dataset content.
    download: downloadItem,
    delete: { onClick: askDelete, disabled: pendingDelete },
  };
  const menuItems = asOf ? asOfMenuItems : latestMenuItems;

  return (
    <>
      <ConfirmDialog
        open={confirmDelete}
        title={n ? `Delete Row ${n}?` : "Delete this row?"}
        body={
          classic
            ? `${n ? `Row ${n}` : "This row"} is deleted from the dataset now. This can't be undone.`
            : `Row ${n ?? ""} is removed from the dataset when you save the next version. Older versions and the experiments that ran on them keep it. You can undo for 10 seconds.`
        }
        confirmLabel="Delete row"
        cancelLabel="Keep row"
        onConfirm={() => {
          setConfirmDelete(false);
          actions.remove({ onDeleted: url.closeRow });
        }}
        onClose={() => setConfirmDelete(false)}
      />
      {open && (n || isNew) ? (
        <Helmet>
          <title>{`${n ? `Row ${n}` : "New row"} · ${dataset?.name ?? "Dataset"} · Future AGI`}</title>
        </Helmet>
      ) : null}
      {/* IT-R2 G5 P6: at ≥ 1280 px the open row OVERLAYS the grid at
          min(640px, 50vw) (rule 7: drawers overlay); the grid keeps its
          full width and columns underneath. Below 1280 the shell's own
          overlayBelow covers the grid; F (full) is unchanged. */}
      <Box sx={open && !url.full ? ROW_OVERLAY_SX : ROW_SLOT_SX}>
        <RowPanelShell
          open={open}
          storageKey={`fa.ds.rowView.width.${datasetId}`}
          initialWidth={640}
          minWidth={440}
          maxWidth={1100}
          full={url.full}
          onToggleFull={() => url.setFull(!url.full)}
          onClose={url.closeRow}
          grid={grid}
          tabs={nav.notFound ? undefined : tabs}
          tab={rowTab}
          onTab={url.setRowTab}
          overlayBelow={OVERLAY_BELOW}
          keyHints
          focusRing
          compactTabs={tabs.length > 5}
          // RV#44: the previous row stays (dimmed) while the next one loads.
          bodyBusy={Boolean(row) && !nav.loaded && !nav.notFound}
          footer={
            open && row && !nav.notFound && keysTipShown ? (
              <Hint
                id={ROW_KEYS_HINT}
                variant="footer"
                icon="mdi:keyboard-outline"
                doneWhen={jk >= JK_DONE}
                sx={{ borderTop: 0, px: 0, py: 0, minHeight: 26 }}
              >
                <RowKeyStrip
                  onHelp={() => setHelpOpen(true)}
                  readOnly={!canEdit}
                />
              </Hint>
            ) : null
          }
          header={{
            title: n
              ? `Row ${n}`
              : isNew
                ? `New row${pendingNo > 0 ? ` ${pendingNo}` : ""}`
                : "Row",
            position: nav.position ?? undefined,
            subtitle: n ? (
              <RowPositionText n={n} position={nav.position} />
            ) : undefined,
            after: (
              <>
                {isImported ? (
                  <DraftRowChip
                    version={draftVersion}
                    onSave={saveVersion}
                    canSave={!viewer}
                  />
                ) : pending && !pendingDelete ? (
                  <UnsavedPill />
                ) : null}
                {/* the interface contract: the row's status and why (archived rows). */}
                {statusShown && statusShown.status !== "approved" ? (
                  <Chip
                    size="small"
                    data-testid="row-view-status"
                    label={`${ROW_STATUS_LABEL[statusShown.status] ?? statusShown.status}${statusShown.reason ? ` · ${statusShown.reason}` : ""}`}
                    sx={{
                      height: 22,
                      typography: "s3",
                      ml: 0.5,
                      maxWidth: 320,
                    }}
                  />
                ) : null}
              </>
            ),
            onPrev: nav.goPrev,
            onNext: nav.goNext,
            prevDisabled: nav.prevDisabled,
            nextDisabled: nav.nextDisabled,
            // IT-R2 G5 P6: the id chip moved to line 2 (it clipped here).
            idChip: null,
            actions: row ? (
              <RowHeaderActions
                actions={actions}
                anchorRef={anchorRef}
                labelled={wide}
                canEdit={canEdit}
                editReason={gate.editReason}
                onEdit={startEdit}
                onAnnotate={() => openQueue()}
                onHelp={() => setHelpOpen(true)}
                // Annotate needs a saved row and edit access (M6).
                viewer={!gate.annotate || isNew}
              />
            ) : null,
            menu: row ? (
              <RowHeaderMenu
                menuItems={menuItems}
                canEdit={!viewer && gate.annotate}
              />
            ) : null,
            line2: row ? (
              <RowHeaderLine2
                datasetId={datasetId}
                row={row}
                splits={splits}
                canEdit={canEdit}
                onOpenTrace={() =>
                  isExpiredSource(source)
                    ? url.setRowTab("trajectory")
                    : setTraceOpen(true)
                }
                onOpenConversation={() => url.setRowTab("conversation")}
                onOpenSourceRun={() =>
                  source?.experiment_id &&
                  navigate(
                    `/dashboard/develop/experiment/${source.experiment_id}/data?row=${rowId}${source.run_id ? `&run=${source.run_id}` : ""}`,
                  )
                }
                onOpenHistory={historyDoor}
                runsSummary={runsQ.data?.summary}
                pendingDelete={pendingDelete}
                splitAnchor={splitAnchor}
                setSplitAnchor={setSplitAnchor}
                splitChipRef={splitChipRef}
                expectedSource={wireRow?.expected_source ?? null}
                // A new row has no id the server knows yet (no chip to copy).
                // Its API items follow dataset.rowApiPath (RowIdChip).
                idChip={
                  rowId && !isNew ? (
                    <RowIdChip
                      datasetId={datasetId}
                      rowId={rowId}
                      link={rowLink()}
                    />
                  ) : null
                }
              />
            ) : null,
          }}
        >
          {/* the interface contract: Cmd/Ctrl+F finds in this row; `/` keeps Find field. */}
          {open && row ? (
            <FindBar
              scope='[data-testid="row-panel"]'
              label="Find in this row"
            />
          ) : null}
          {open && KindBody && (
            <KindBody datasetId={datasetId} rowId={rowId} row={row} />
          )}
          {open && !KindBody && rowTab === "raw" && row && (
            <>
              {asOf && (
                <RowBanner icon="mdi:lock-outline" testId="row-banner-asof">
                  {deletedRow ? deletedRowText(deletedRow) : "Read-only"}
                </RowBanner>
              )}
              <RowRawTab model={model} rowJson={rowJson} />
            </>
          )}
          {open && !KindBody && rowTab !== "raw" && (
            <RowViewBody
              datasetId={datasetId}
              rowId={rowId}
              row={row}
              nav={nav}
              model={model}
              tab={rowTab}
              setTab={url.setRowTab}
              view={{
                mode,
                setMode,
                diffOn,
                setDiffOn,
                asOf,
                onCopyRow: actions.copyRowJson,
                rowJson,
                collapseTick,
              }}
              asOfVersion={asOf ? versionParam : null}
              deletedRow={deletedRow}
              onRestoreRow={openRestore}
              onBackToLatest={() => setVersionParam(null)}
              onClose={url.closeRow}
              onOpenRow={url.openRow}
              edit={edit}
              actions={actions}
              viewer={viewer}
              canAnnotate={gate.annotate}
              canRun={access.can.run}
              pendingDelete={pendingDelete}
              onRevertDelete={() => changeSet.revert(rowId)}
              onOpenHistory={historyDoor}
              onOpenVersion={openVersion}
              onAddToQueue={openQueue}
              isNew={isNew}
              isImported={isImported}
              draftVersion={isImported ? draftVersion : null}
              hasConversationTab={kindTabs.some(
                (t) => t.value === "conversation",
              )}
              onSaveVersion={isNew ? saveVersion : undefined}
            />
          )}
        </RowPanelShell>
      </Box>
      {rowId && (
        <AddToQueueDialog
          anchorEl={queueAnchor}
          onClose={() => setQueueAnchor(null)}
          sourceType="dataset_row"
          sourceIds={[rowId]}
          itemName="Row"
        />
      )}
      {traceOpen && source?.trace_id && !isExpiredSource(source) && (
        <TraceDetailDrawerV2
          traceId={source.trace_id}
          projectId={source.project_id}
          initialSpanId={source.span_id}
          open
          onClose={() => setTraceOpen(false)}
        />
      )}
      <ShortcutSheet
        open={helpOpen}
        onClose={() => setHelpOpen(false)}
        surface="row"
      />
      {/* the interface contract: the one host of Restore rows… (bar, banner, ⌘K). */}
      <RestoreRowsDialog datasetId={datasetId} />
    </>
  );
};

RowView.propTypes = {
  grid: PropTypes.node,
  suppressed: PropTypes.bool,
};

export default RowView;
