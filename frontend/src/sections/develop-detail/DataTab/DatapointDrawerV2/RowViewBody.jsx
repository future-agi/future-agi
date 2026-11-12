import React, { useMemo } from "react";
import PropTypes from "prop-types";
import {
  useDevelopFilterStore,
  useDevelopSearchStore,
} from "src/sections/develop-detail/states";
import { useDevelopDetailContext } from "src/sections/develop-detail/Context/DevelopDetailContext";
import EmptyState from "src/sections/common/ux/EmptyState";
import RowActivityTab from "./RowActivityTab";
import RowAnnotationsTab from "./RowAnnotationsTab";
import RowFieldsTab from "./RowFieldsTab";
import RowRunsTab from "./RowRunsTab";
import RowLayoutTab from "src/sections/common/RowLayouts/RowLayoutTab";
import {
  RowBanner,
  RowNotFound,
  RowSkeleton,
  deletedRowText,
} from "./RowViewStates";
import { defaultExpectedFor } from "./rowModel";
import { useQueryClient } from "@tanstack/react-query";
import { copyToClipboard } from "src/utils/utils";
import { addColumnFilter } from "../DataGrid/gridFilterActions";
import { notify } from "src/sections/common/ux/toast";
import { useColumnFromPathStore } from "src/sections/common/Values/columnFromPathStore";
import {
  ValueActionsContext,
  pathFilterEntry,
  splitColumnPath,
  variableOf,
} from "src/sections/common/Values/valueActions";
import { isSurfaceShown } from "src/launch/launchSwitch";

const fieldColumns = (model) =>
  ["input", "expected", "metadata", "noRole", "outputs", "human"]
    .flatMap((k) => model?.[k] ?? [])
    .map((f) => f?.column)
    .filter((c) => c?.id && c?.name);

// A new row has no runs, annotations or history until a version saves it.
// IT-R3 G5 P1: one sentence for every tab that needs a saved row
// (`This row is not saved yet. [Save version]`), then what the tab holds.
const NOT_SAVED_TITLE = "This row is not saved yet";
const NEW_ROW_EMPTY = {
  runs: {
    icon: "mdi:flask-outline",
    title: NOT_SAVED_TITLE,
    body: (draft) =>
      `Save ${draft}, then run an experiment on it: each run's answer to this row shows here.`,
  },
  annotations: {
    icon: "mdi:account-check-outline",
    title: NOT_SAVED_TITLE,
    body: (draft) =>
      `Save ${draft} first. Then you can add this row to an annotation queue.`,
  },
  activity: {
    icon: "mdi:history",
    title: NOT_SAVED_TITLE,
    body: (draft) =>
      `This row's history starts when you save ${draft}, the version that adds it.`,
  },
};

/**
 * The Row view's body: state banners (hidden by filters, as of an older
 * version, pending delete, view only), then the active tab.
 */
const RowViewBody = ({
  datasetId,
  rowId,
  row,
  nav,
  model,
  tab,
  setTab,
  view,
  asOfVersion,
  deletedRow = null,
  onRestoreRow,
  onBackToLatest,
  edit,
  actions,
  viewer,
  canAnnotate,
  canRun,
  pendingDelete,
  onRevertDelete,
  onOpenHistory,
  onOpenVersion,
  onAddToQueue,
  onClose,
  onOpenRow,
  isNew = false,
  isImported = false,
  draftVersion = null,
  onSaveVersion,
  hasConversationTab = false,
}) => {
  const resetFilters = useDevelopFilterStore((s) => s.resetFilters);
  const queryClient = useQueryClient();
  const setSearch = useDevelopSearchStore((s) => s.setSearch);
  const { gridApi } = useDevelopDetailContext();
  // IT-R2 G5 P5: a nested value can become a column, a filter or a
  // {{variable}} from where it is read.
  const valueActions = useMemo(() => {
    const columns = fieldColumns(model);
    return {
      addColumn: (path) =>
        useColumnFromPathStore.getState().openFor({ datasetId, path }),
      filterBy: (path, value) => {
        const { column, rel } = splitColumnPath(path);
        const col = columns.find((c) => c.name === column);
        if (!col) return;
        // The grid's one-click filter (chips, editable in the panel).
        const entry = pathFilterEntry(col.id, rel, value, null);
        addColumnFilter(gridApi?.current, {
          columnId: entry.columnId,
          ...entry.filterConfig,
        });
        // The open row re-reads its place in the narrowed set.
        queryClient.invalidateQueries({
          queryKey: ["develop", "row-view", datasetId],
        });
      },
      useAsVariable: (path) => {
        copyToClipboard(variableOf(path));
        notify(`Copied ${variableOf(path)} · paste it into a prompt`, {
          variant: "success",
          key: "copy-variable",
        });
      },
    };
  }, [model, datasetId, gridApi, queryClient]);
  // RV#28: `Go to row 1` = the first row the grid shows that is not this
  // one (IT-R3 G5 P1: never a button that reopens the same missing row).
  const firstRowId = () => {
    const api = gridApi?.current;
    const count = api?.getDisplayedRowCount?.() ?? 0;
    for (let i = 0; i < Math.min(count, 50); i += 1) {
      const node = api.getDisplayedRowAtIndex?.(i);
      const id = node?.data?.row_id ?? node?.id ?? null;
      if (id && id !== rowId) return id;
    }
    return null;
  };
  const onFirstRow =
    onOpenRow && firstRowId()
      ? () => {
          const id = firstRowId();
          if (id) onOpenRow(id);
        }
      : undefined;

  if (nav.notFound)
    return (
      <RowNotFound
        onOpenHistory={onOpenHistory}
        onClose={onClose}
        onFirstRow={onFirstRow}
      />
    );
  if (nav.isError)
    return (
      <div role="alert">
        <EmptyState
          title="Could not load this row"
          body="The row may be unavailable or you may not have access. Retry, or return to the dataset."
          actions={[
            { label: "Retry", onClick: () => nav.refetch?.() },
            { label: "Back to dataset", onClick: onClose },
          ]}
        />
      </div>
    );
  if (!row) return <RowSkeleton />;

  const pendingEdits = model.input
    .concat(model.expected, model.metadata, model.noRole)
    .some((f) => f.edited);
  const banners = [];
  if (nav.outside)
    banners.push(
      <RowBanner
        key="outside"
        tone="amber"
        action="Clear filters"
        onAction={() => {
          resetFilters();
          setSearch("");
          // The toolbar search and filter panel refresh the grid this way.
          gridApi?.current?.onFilterChanged?.();
        }}
        testId="row-banner-outside"
      >
        {pendingEdits
          ? "This row no longer matches the filters."
          : "This row is hidden by the current filters."}
      </RowBanner>,
    );
  // integration: a row deleted after the viewed version says so, with
  // `Restore this row…` (absent for viewers).
  if (asOfVersion && deletedRow)
    banners.push(
      <RowBanner
        key="asof"
        tone={deletedRow.inLatest ? undefined : "red"}
        icon="mdi:lock-outline"
        testId="row-banner-asof-deleted"
        action={deletedRow.canRestore ? "Restore this row…" : undefined}
        onAction={onRestoreRow}
      >
        {deletedRowText(deletedRow)}
      </RowBanner>,
    );
  else if (asOfVersion)
    banners.push(
      // RV#33: the page banner announces the version; the panel only says
      // it can't be edited.
      <RowBanner key="asof" icon="mdi:lock-outline" testId="row-banner-asof">
        Read-only
      </RowBanner>,
    );
  if (pendingDelete)
    banners.push(
      <RowBanner
        key="del"
        tone="red"
        action="Restore"
        onAction={onRevertDelete}
        testId="row-banner-deleted"
      >
        Deleted in the pending version · read-only
      </RowBanner>,
    );
  if (viewer && !asOfVersion)
    banners.push(
      <RowBanner key="viewer" testId="row-banner-viewer">
        You have view access · editing is off
      </RowBanner>,
    );

  // The text Expected the Runs tab diffs outputs against.
  const firstOutput = model.outputs[0];
  const exp = defaultExpectedFor(
    firstOutput ?? { column: { data_type: "text" } },
    model.expected,
  );

  if (isNew && NEW_ROW_EMPTY[tab]) {
    const empty = NEW_ROW_EMPTY[tab];
    const draft = draftVersion ? `draft v${draftVersion}` : "a version";
    return (
      <EmptyState
        compact
        icon={empty.icon}
        title={empty.title}
        body={empty.body(draft)}
        actions={[
          ...(onSaveVersion && !viewer
            ? [{ label: "Save version", onClick: onSaveVersion }]
            : []),
          { label: "Back to the row", onClick: () => setTab("row") },
        ]}
      />
    );
  }
  if (isImported && tab === "row")
    banners.push(
      <RowBanner
        key="draft"
        icon="mdi:file-document-edit-outline"
        testId="row-banner-draft"
      >
        {`Imported into draft v${draftVersion ?? "?"} · read-only until you save it`}
      </RowBanner>,
    );

  return (
    <ValueActionsContext.Provider value={viewer ? null : valueActions}>
      {banners}
      {tab === "row" && (
        <RowLayoutTab
          datasetId={datasetId}
          rowId={rowId}
          row={row}
          model={model}
          edit={edit}
          viewer={viewer}
          asOfVersion={asOfVersion}
          pendingDelete={pendingDelete}
          isNew={isNew}
        >
          <RowFieldsTab
            model={model}
            row={row}
            view={view}
            edit={edit}
            runsCount={row.row_meta?.counts?.runs ?? 0}
            onOpenRuns={() => setTab("runs")}
            onOpenAnnotations={() => setTab("annotations")}
            onOpenConversation={
              hasConversationTab ? () => setTab("conversation") : undefined
            }
            onRerun={actions.rerun}
            rerunningId={actions.rerunningId}
          />
        </RowLayoutTab>
      )}
      {tab === "runs" && (
        <RowRunsTab
          datasetId={datasetId}
          rowId={rowId}
          expected={exp ? { name: exp.column.name, value: exp.value } : null}
          lastChangedVersion={row.row_meta?.last_changed_in_version}
          // integration: no new experiment from a row read as of an older version.
          onNewExperiment={
            viewer ||
            canRun === false ||
            asOfVersion ||
            // L6 launch switch: no New experiment door without the drawer,
            // and none that preselects this row (data:rows:these).
            !isSurfaceShown("exp.new.scope")
              ? undefined
              : actions.newExperiment
          }
        />
      )}
      {tab === "annotations" && (
        <RowAnnotationsTab
          datasetId={datasetId}
          rowId={rowId}
          onAddToQueue={onAddToQueue}
          canEdit={canAnnotate ?? !viewer}
        />
      )}
      {tab === "activity" && (
        <RowActivityTab
          datasetId={datasetId}
          rowId={rowId}
          onOpenVersion={onOpenVersion}
        />
      )}
    </ValueActionsContext.Provider>
  );
};

RowViewBody.propTypes = {
  datasetId: PropTypes.string.isRequired,
  rowId: PropTypes.string.isRequired,
  row: PropTypes.object,
  nav: PropTypes.object.isRequired,
  model: PropTypes.object.isRequired,
  tab: PropTypes.string.isRequired,
  setTab: PropTypes.func.isRequired,
  view: PropTypes.object.isRequired,
  asOfVersion: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  deletedRow: PropTypes.shape({
    version: PropTypes.number,
    latest: PropTypes.number,
    inLatest: PropTypes.bool,
    restoredIn: PropTypes.number,
    canRestore: PropTypes.bool,
  }),
  onRestoreRow: PropTypes.func,
  onBackToLatest: PropTypes.func,
  edit: PropTypes.object.isRequired,
  actions: PropTypes.object.isRequired,
  viewer: PropTypes.bool,
  // W4 §9 (M6): the dataset's access (rowAccessGate / useDatasetAccess).
  canAnnotate: PropTypes.bool,
  canRun: PropTypes.bool,
  pendingDelete: PropTypes.bool,
  onRevertDelete: PropTypes.func,
  onOpenHistory: PropTypes.func,
  onOpenVersion: PropTypes.func,
  onAddToQueue: PropTypes.func,
  onClose: PropTypes.func,
  onOpenRow: PropTypes.func,
  isNew: PropTypes.bool,
  isImported: PropTypes.bool,
  draftVersion: PropTypes.number,
  hasConversationTab: PropTypes.bool,
  onSaveVersion: PropTypes.func,
};

export default RowViewBody;
