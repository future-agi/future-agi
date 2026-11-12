import { Box, Button, Chip } from "@mui/material";
import ActionBar from "src/sections/common/ux/oneview/ActionBar";
import ViewMenu from "src/sections/common/ux/oneview/ViewMenu";
import useDataToolbarView from "../DataTab/DataGrid/useDataToolbarView";
import useDataGridLayout, {
  DATA_GRID_SURFACE,
} from "../DataTab/DataGrid/useDataGridLayout";
import { useChangeSetStore } from "../DataTab/Versions/changeSetStore";
import { datasetCaps, gridWritesClassic } from "src/sections/develop/realBackendCaps";
import axios, { endpoints } from "src/utils/axios";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";
import useRowViewUrl from "../DataTab/DatapointDrawerV2/useRowViewUrl";
import FloatingSelectionBar from "../DataTab/DataGrid/FloatingSelectionBar";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import { useQuery } from "@tanstack/react-query";
import React, { useState } from "react";
import { useParams } from "react-router";
import { useSearchParams } from "react-router-dom";
import { notify } from "src/sections/common/ux/toast";
import { DatasetExportDialog } from "src/sections/common/export/ExportDialog";
import useDatasetAccess from "src/sections/common/Access/useDatasetAccess";
import {
  getDataGridFilters,
  getDataGridSearch,
} from "../DataTab/DataGrid/useDataGridView";
import AddRowDrawer from "src/sections/develop/AddRowDrawer/AddRowDrawer";
import { ADD_ROWS_OPTIONS } from "src/sections/develop/importOptions";
import {
  addRowsTiles,
  isLaunchBuild,
  isSurfaceShown,
} from "src/launch/launchSwitch";
import { PROPOSED_ADD_ROWS_COPY } from "src/sections/develop/importAvailabilityCopy";
import AddColumnDrawer from "../AddColumn/AddColumnDrawer";
import DevelopDataSelectionActive from "./DevelopDataSelectionActive";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import {
  useRunPromptStoreShallow,
  useAddColumnDrawerStoreShallow,
  useDevelopSelectedRowsStoreShallow,
  useRunEvaluationStoreShallow,
  useDatasetOriginStore,
} from "../states";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import PropTypes from "prop-types";
import SyntheticSummaryDrawer from "../../develop/AddRowDrawer/EditSyntheticData/SyntheticSummaryDrawer";
import { useEditSyntheticDataStore } from "../../develop/AddRowDrawer/EditSyntheticData/state";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import CustomTooltip from "src/components/tooltip";
import Iconify from "src/components/iconify";
import SchemaDrawer from "src/sections/common/Values/SchemaDrawer";
import { toolbarAccessGate } from "src/sections/develop-detail/DataTab/dataAccessGate";

// Toolbar doors that start a run (they need Run access as well as Edit).
const RUN_TITLES = new Set(["Run Prompt", "Evaluate"]);

const DevelopDataRightSection = ({ hideScenarioFeatures = false }) => {
  const { refreshGrid } = useDevelopDetailContext();
  const [addRowDrawerOpen, setAddRowDrawerOpen] = useState(false);
  // the interface contract: `Download…` opens the export dialog with the grid's filter.
  const [exportOpen, setExportOpen] = useState(null);
  // The read-only Schema drawer (every dataset).
  const [r2Drawer, setR2Drawer] = useState(null);
  const { openRow } = useRowViewUrl();
  const setOpenAddColumnDrawer = useAddColumnDrawerStoreShallow(
    (s) => s.setOpenAddColumnDrawer,
  );
  const setOpenRunPrompt = useRunPromptStoreShallow((s) => s.setOpenRunPrompt);
  const { role } = useAuthContext();

  const setOpenRunEvaluation = useRunEvaluationStoreShallow(
    (s) => s.setOpenRunEvaluation,
  );
  const { setOpenSummaryDrawer } = useEditSyntheticDataStore();

  const { dataset } = useParams();
  // the interface contract §9 (M6): Download needs download access (a viewer needs a grant);
  // the disabled door names the server's reason.
  const access = useDatasetAccess(dataset);
  const downloadReason = access.can.download ? null : access.reason("download");
  // View (A1-10/11): density, Columns, saved views and Reset view in one
  // menu; the same column layout as the grid (commands are the toolbar's).
  const toolbarView = useDataToolbarView(dataset);
  const layout = useDataGridLayout(dataset, { commands: false });

  const { isSelected } = useDevelopSelectedRowsStoreShallow((s) => ({
    isSelected: s.selectAll || s.toggledNodes.length > 0,
  }));

  const { data: tableData } = useQuery(
    getDatasetQueryOptions(dataset, 0, [], [], "", {
      enabled: false,
      // react-query v5 passes the query, not its data.
      refetchInterval: (query) => {
        const isProcessing =
          query?.state?.data?.data?.result?.is_processing_data;

        if (isProcessing) return 2000;
        return false;
      },
    }),
  );

  const { processingComplete } = useDatasetOriginStore();

  const isData = Boolean(tableData?.data?.result?.table?.length);
  // As-of mode (`?version=5`, §3.2): every write is off while viewing an
  // older version.
  const [searchParams] = useSearchParams();
  const latestVersion = tableData?.data?.result?.metadata?.version?.number;
  const asOfVersion = Number.parseInt(searchParams.get("version") ?? "", 10);
  const asOf =
    Number.isFinite(asOfVersion) &&
    latestVersion != null &&
    asOfVersion !== latestVersion;
  const isSyntheticDataset = Boolean(
    tableData?.data?.result?.synthetic_dataset,
  );
  // Keep both response spellings conservative: either processing flag blocks writes.
  const processingData = Boolean(
    tableData?.data?.result?.is_processing_data ||
      tableData?.data?.result?.isProcessingData,
  );
  const datasetButtons = [
    {
      icon: "ic_add_row",
      title: "Add Row",
      action: () => setAddRowDrawerOpen(true),
      event: Events.addRowsClicked,
    },
    {
      icon: "ic_add_column",
      title: "Add Column",
      action: () => setOpenAddColumnDrawer(true),
      event: Events.addColumnsClicked,
    },
    {
      icon: "ic_configure",
      title: "Configure Synthetic Data",
      action: () => setOpenSummaryDrawer(true),
    },
    // R2: read-only buttons (open in as-of mode and for viewers too).
    // L4 verify 2: Declare / Transformations save a schema through
    // edit_dataset_behavior, which dev cannot store (400), so Schema is the
    // hidden dataset.versions surface's schema:* in a launch build.
    ...(isSurfaceShown("dataset.schema")
      ? [
          {
            icon: "mdi:file-tree-outline",
            title: "Schema",
            action: () => setR2Drawer("schema"),
            readOnly: true,
          },
        ]
      : []),
  ];

  const promptButtons = [
    {
      icon: "ic_run_prompt",
      title: "Run Prompt",
      action: () => setOpenRunPrompt(true),
      event: Events.datasetRunPromptClicked,
      hoverText: "Run a prompt over every row and add its output as a column",
      link: "https://docs.futureagi.com/docs/dataset/features/run-prompt",
    },
    {
      icon: "ic_evaluate",
      title: "Evaluate",
      action: () => setOpenRunEvaluation(true),
      event: Events.datasetRunEvaluationClicked,
      hoverText: "Score every row with an eval and add the result as a column",
      link: "https://docs.futureagi.com/docs/evaluation/features/evaluate",
    },
  ];

  const canUpdate = Boolean(RolePermission.DATASETS[PERMISSIONS.UPDATE][role]);
  // v2.1 g8 (access:gates): the dataset's own access level gates the
  // toolbar too. A member with View access can't add rows or columns; Run
  // prompt and Evaluate also need Run. The disabled door names the reason.
  const {
    editOff: accessEditOff,
    runOff: accessRunOff,
    editReason: accessEditReason,
    runReason: accessRunReason,
  } = toolbarAccessGate({ can: access.can, reason: access.reason });
  const writeReason = asOf
    ? `Read-only: you are viewing v${asOfVersion}. Back to latest to edit`
    : !canUpdate
      ? // v2.1 g8 (access:viewer-ceiling): the server's ceiling line
        // (`Workspace viewers can't edit datasets. Ask …`) at the door.
        access.reason("edit") || "Viewers can't edit this dataset"
      : accessEditOff
        ? accessEditReason
        : processingData
          ? "Rows are still processing"
          : "";
  const writeOff =
    asOf ||
    !tableData ||
    processingData ||
    (isSyntheticDataset && !processingComplete) ||
    !canUpdate ||
    accessEditOff;
  const runReason = !writeOff && accessRunOff ? accessRunReason : "";
  const needsRowsReason = !isData ? "Add rows first" : writeReason;
  // Order = how often they are reached for (A1-13): what makes new columns
  // first, then the dataset's settings and read-only views.
  const secondary = [
    ...(hideScenarioFeatures ? [] : promptButtons),
    ...datasetButtons.filter(
      (b) =>
        b.title !== "Add Row" &&
        (b.title !== "Configure Synthetic Data" || isSyntheticDataset),
    ),
  ].map((b) => ({
    ...b,
    label:
      {
        "Add Column": "Add column",
        "Run Prompt": "Run prompt",
        "Configure Synthetic Data": "Synthetic data settings",
      }[b.title] ?? b.title,
    off: b.readOnly
      ? !tableData
      : b.title === "Add Column"
        ? writeOff
        : writeOff || !isData || (RUN_TITLES.has(b.title) && accessRunOff),
    reason: b.readOnly
      ? ""
      : b.title === "Add Column"
        ? writeReason
        : needsRowsReason || (RUN_TITLES.has(b.title) ? runReason : ""),
  }));
  const runAction = (b) => {
    b.action?.();
    if (b.event) trackEvent(b.event, { [PropertyName.id]: dataset });
  };
  const addBlank = () => {
    // L1 (the capability contract): the real backend has no change-set; a blank row
    // is written at once (dev's add_empty_rows), then the grid refreshes.
    if (gridWritesClassic()) {
      axios.post(endpoints.develop.addEmptyRow(dataset), { num_rows: 1 }).then(
        () => {
          refreshGrid?.();
          notify("Added a blank row", {
            key: "row-add-blank",
            variant: "success",
          });
        },
        () =>
          notify("Couldn't add a row.", {
            key: "row-add-blank",
            variant: "error",
          }),
      );
      return;
    }
    const tempId = `new-${Date.now().toString(36)}`;
    useChangeSetStore.getState().addOp(dataset, {
      op: "add_row",
      temp_id: tempId,
      cells: {},
      split_ids: [],
    });
    openRow(tempId);
    // The Row view opens with the first field in edit; Undo drops the row.
    notify("Added a blank row · unsaved until you save a version", {
      key: "row-add-blank",
      undo: {
        onUndo: () =>
          useChangeSetStore
            .getState()
            .addOp(dataset, { op: "delete_rows", row_ids: [tempId] }),
      },
    });
  };
  useRegisterCommands(
    "dataset-grid-actions",
    [
      {
        id: "grid.addRow",
        label: "Add row",
        keywords: ["new row", "blank row"],
        icon: "mdi:plus",
        run: () => addBlank(),
        when: () => !writeOff,
      },
      {
        id: "grid.importRows",
        label: "Import rows (file, traces, another dataset)",
        icon: "mdi:database-import-outline",
        run: () => setAddRowDrawerOpen(true),
        when: () => !writeOff,
      },
    ],
    [writeOff, dataset],
  );

  const addRowSplit = (
    // IT-R1 G5 P3: one labelled `Add rows ▾` (was `Add row` + a bare
    // chevron). It opens the Add rows sheet: production traces first, the
    // blank row last; ⌘K `Add row` still adds a blank row in one step.
    <CustomTooltip
      show
      size="small"
      title={
        writeOff
          ? writeReason || "Add rows"
          : isLaunchBuild()
            ? PROPOSED_ADD_ROWS_COPY.toolbarHint
            : "From production traces, a file, a paste, another dataset… or a blank row"
      }
    >
      <span>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disableElevation
          disabled={writeOff}
          aria-haspopup="dialog"
          data-testid="add-rows-button"
          startIcon={<Iconify icon="mdi:plus" width={16} />}
          endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
          onClick={() => {
            trackEvent(Events.addRowsClicked, { [PropertyName.id]: dataset });
            setAddRowDrawerOpen(true);
          }}
          sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
        >
          Add rows
        </Button>
      </span>
    </CustomTooltip>
  );

  // Keep each toolbar capability independent.
  const addRowsDoor = addRowsTiles(ADD_ROWS_OPTIONS).length > 0;
  const savedViewsShown = isSurfaceShown("dataset.savedViews");
  const viewMenu = (
    <Box sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}>
      <ViewMenu
        surface={DATA_GRID_SURFACE}
        scopeId={dataset}
        density={{
          value: toolbarView.gridView.density,
          onChange: toolbarView.gridView.setDensity,
        }}
        columns={layout}
        sections={false}
        savedViews={
          savedViewsShown
            ? {
                state: toolbarView.viewState,
                onApply: toolbarView.applyView,
                canShare: canUpdate,
              }
            : undefined
        }
        isModified={toolbarView.modified}
        onReset={toolbarView.resetView}
      />
      {/* Nothing is silently gone (P5): once the user hid columns, `N
          hidden` sits by View and opens Columns on the hidden ones. */}
      {!layout.isDefault && layout.hiddenCount > 0 && (
        <CustomTooltip show size="small" title="Show the hidden columns">
          <Chip
            size="small"
            variant="outlined"
            label={`${layout.hiddenCount} hidden`}
            onClick={() => layout.openMenu({ hiddenOnly: true })}
            sx={{ typography: "s3", color: "text.secondary", height: 22 }}
          />
        </CustomTooltip>
      )}
    </Box>
  );

  const ICONS = {
    "Configure Synthetic Data": "mdi:creation-outline",
    "Add Column": "mdi:table-column-plus-after",
    Schema: "mdi:file-tree-outline",
    "Run Prompt": "mdi:play-box-outline",
    Evaluate: "mdi:check-decagram-outline",
  };
  const PRIORITY = {
    "Run Prompt": 2,
    Evaluate: 2,
    "Add Column": 3,
  };
  // MAIN-RULING-EXPORT-CONTRACT: CSV is available in every build. Reuse the
  // same permission/readiness checks for More, the palette and the real
  // selection bar; dataset.export gates only the dialog's advanced formats.
  const downloadAction = {
    id: "download",
    label: "Download…",
    icon: "mdi:download-outline",
    priority: 4,
    inMore: true,
    keywords: ["export", "csv", "json", "jsonl", "parquet", "fine-tune", "openai", "download"],
    disabled:
      !isData ||
      (isSyntheticDataset && !processingComplete) ||
      Boolean(downloadReason),
    reason: downloadReason ?? (!isData ? "Nothing to export yet" : undefined),
    run: () => {
      if (!access.can.download) return;
      trackEvent(Events.datasetDownloadClicked);
      setExportOpen({
        filters: getDataGridFilters(dataset),
        search: getDataGridSearch() ?? "",
      });
    },
  };
  // A1-13: one priority-plus bar. Every action keeps its handler and its
  // disabled reason; what does not fit folds into `More ▾`, and all of
  // them are in ⌘K by name (their old hover text is a ⌘K keyword).
  const barActions = [
    { id: "view", label: "View", priority: 1, render: () => viewMenu },
    ...(addRowsDoor
      ? [
          {
            id: "addRow",
            label: "Add rows",
            priority: 1,
            render: () => addRowSplit,
          },
        ]
      : []),
    ...secondary.map((b) => ({
      id: b.title.toLowerCase().replace(/\s+/g, "-"),
      label:
        b.title === "Configure Synthetic Data"
          ? "Configure synthetic data"
          : b.label,
      icon: ICONS[b.title] ?? "mdi:dots-horizontal",
      priority: PRIORITY[b.title] ?? 4,
      // IT-R4 G5 P6: only Run prompt stays inline; Evaluate and the rest
      // live in `More ▾` (and ⌘K) at every width.
      inMore: b.title !== "Run Prompt",
      disabled: b.off,
      reason: b.reason || undefined,
      keywords: b.hoverText ? [b.hoverText] : undefined,
      run: () => runAction(b),
    })),
    // Keep the legacy prompt/eval documentation reachable after moving actions
    // into the priority toolbar. These read-only guides never start a run.
    ...(hideScenarioFeatures
      ? []
      : promptButtons
          .filter((button) => button.link)
          .map((button) => ({
            id: `guide-${button.title.toLowerCase().replace(/\s+/g, "-")}`,
            label: `${button.title} guide`,
            icon: "mdi:book-open-outline",
            priority: 5,
            inMore: true,
            keywords: ["help", "docs", button.hoverText],
            run: () => window.open(button.link, "_blank", "noopener,noreferrer"),
          }))),
    downloadAction,
  ];

  return (
    <React.Fragment>
      <ActionBar
        surface="grid"
        actions={barActions}
        // The bar's room grows with the window (A1-13): at 1280 it is
        // today's calm footprint, `View · Add row ▾ · ⤓ · More ▾`; wider
        // windows show Run prompt, Evaluate, Add column… inline.
        sx={{
          minWidth: 0,
          ml: "auto",
          maxWidth: "max(320px, calc(100vw - 940px))",
        }}
      />
      {/* Selection never replaces the toolbar (DG#4): the bulk bar floats
          bottom-centre over the grid, above the status bar. */}
      {isSelected && (
        <FloatingSelectionBar>
          <Box sx={{ display: "flex", alignItems: "center" }}>
            {/* Real exports read ALL current rows, not the selection. Keep
                unsupported selected/versioned actions gated and label this
                ordinary-CSV fallback with its actual scope. */}
            {datasetCaps().real && (
              <CustomTooltip
                show={Boolean(downloadAction.reason)}
                size="small"
                title={downloadAction.reason ?? ""}
              >
                <span>
                  <Button
                    size="small"
                    color="inherit"
                    disabled={downloadAction.disabled}
                    onClick={downloadAction.run}
                    startIcon={<Iconify icon="mdi:download-outline" width={16} />}
                    data-testid="selection-download-all-rows"
                  >
                    Download all rows…
                  </Button>
                </span>
              </CustomTooltip>
            )}
            <DevelopDataSelectionActive />
          </Box>
        </FloatingSelectionBar>
      )}
      <AddRowDrawer
        open={addRowDrawerOpen}
        onClose={() => setAddRowDrawerOpen(false)}
      />
      <AddColumnDrawer hideScenarioFeatures={hideScenarioFeatures} />
      <SyntheticSummaryDrawer />
      <DatasetExportDialog
        open={Boolean(exportOpen)}
        onClose={() => setExportOpen(null)}
        datasetId={dataset}
        datasetName={tableData?.data?.result?.metadata?.dataset_name}
        currentFilter={exportOpen}
        versionNumber={asOf ? asOfVersion : null}
      />
      <SchemaDrawer
        open={r2Drawer === "schema"}
        onClose={() => setR2Drawer(null)}
        datasetId={dataset}
      />
    </React.Fragment>
  );
};

DevelopDataRightSection.displayName = "DevelopDataRightSection";

DevelopDataRightSection.propTypes = {
  hideScenarioFeatures: PropTypes.bool,
};

export default DevelopDataRightSection;
