import {
  Box,
  Button,
  Divider,
  ListItemIcon,
  Menu,
  MenuItem,
  Typography,
  useMediaQuery,
} from "@mui/material";
import React, { useMemo, useRef, useState } from "react";
import { styled } from "@mui/system";
import axios, { endpoints } from "src/utils/axios";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "src/components/snackbar";
import CustomDialog from "../Common/CustomDialog/CustomDialog";
import DataMenuList from "../DataTab/DataMenuList/DataMenuList";
import DuplicateRowAction from "../DataTab/Duplicate/DuplicateRowAction";
import RunEvals from "../DataTab/RunEvals/RunEvals";
import "./developData.css";
import { useParams } from "react-router";
import SnackbarWithAction from "../Common/SnackbarWithAction";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import SvgColor from "src/components/svg-color";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";
import { useDevelopSelectedRowsStoreShallow } from "../states";
import PropTypes from "prop-types";
import { useGenerateRows } from "src/sections/common/AddData/GenerateRowsDialog";
import SyntheticMoreAction from "src/sections/common/AddData/SyntheticMoreAction";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { getCreatedRowsDatasetId } from "./createDatasetRowsResponse";
import CustomTooltip from "src/components/tooltip";
import AddToQueueDialog from "src/sections/annotations/queues/components/add-to-queue-dialog";
import AssignSplitsMenu from "../DataTab/Splits/AssignSplitsMenu";
import {
  useChangeSet,
  useChangeSetStore,
} from "../DataTab/Versions/changeSetStore";
import ArchiveRowsDialog from "../DataTab/Versions/ArchiveRowsDialog";
import TagRowsDialog from "../DataTab/Versions/TagRowsDialog";
import { DatasetExportDialog } from "src/sections/common/export/ExportDialog";
import { datasetSelectionFilter } from "src/sections/common/Selection/exportSelection";
import { usePendingChangesGate } from "../DataTab/Versions/PendingChangesGate";
import { writeExperimentRows } from "../DataTab/Versions/experimentRowsUrl";
import { rowIdsAfterGate } from "../DataTab/Versions/unsavedSelection";
import { useRunExperimentStoreShallow } from "../states";
import { selectionLabel } from "../DataTab/DataGrid/dataGridView";
import { KeyCap } from "src/sections/common/ux/KeyCap";
import {
  BULK_ROW_CAP,
  resolveSelectedRows,
} from "../DataTab/DataGrid/selectionRows";
import {
  getDataGridFilters,
  getDataGridSearch,
} from "../DataTab/DataGrid/useDataGridView";
import { nextPageOf } from "../DataTab/DataGrid/selectionRows";
import {
  parseVersionParam,
  useAsOfVersion,
} from "src/api/develop/dataset-versions";
import { useSearchParams } from "react-router-dom";
import {
  tableRequestFor,
  useAsOfView,
} from "../DataTab/Versions/versionedSource";
import { restorableRows } from "../DataTab/Versions/versionText";
import {
  duplicateOps,
  duplicateToast,
  mergeToast,
  newDatasetToast,
} from "../DataTab/Versions/selectionCopies";
import { revealNewRows } from "../DataTab/DataGrid/newRowsReveal";
import { notify } from "src/sections/common/ux/toast";
import { useRestoreRowsDialog } from "../DataTab/Versions/RestoreRowsDialog";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import DeleteRowAction from "../DataTab/Delete/DeleteRowAction";
import { datasetCaps } from "src/sections/develop/realBackendCaps";
import {
  deleteRowsClassic,
  duplicateRowsClassic,
  refusalSentence,
  rowsWord,
} from "../DataTab/Versions/classicWrites";

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

const StyledBox = styled(Box)(({ theme }) => ({
  gap: "12px",
  display: "flex",
  alignItems: "center",
  paddingX: "16px",
  borderRadius: theme.shape.borderRadius,
}));

const StyledTypography = styled(Typography)(({ theme }) => ({
  color: theme.palette.primary.main,
  fontWeight: theme.typography["fontWeightMedium"], // "500",
  ...theme.typography["s1"],
}));

const StyledButton = styled(Button)(({ theme }) => ({
  color: theme.palette.text.primary,
  fontWeight: theme.typography["fontWeightRegular"], // "500",
  ...theme.typography["s1"],
  "&:hover": {
    backgroundColor: theme.palette.action.hover,
  },
}));

const LatestSelectionBar = () => {
  const { dataset } = useParams();
  const gridRef = useRef(null);
  const [isData, setIsData] = useState(false);
  const [isDuplicate, setIsDuplicate] = useState(false);
  const [isRunEval, setIsRunEval] = useState(false);
  const [noOfCopies, setNoOfCopies] = useState(0);
  const [anchorEl, setAnchorEl] = React.useState(null);
  const [anchorElSubmenu, setAnchorElSubmenu] = React.useState(null);
  const [targetDatasetId, setTargetDatasetId] = useState("");
  const [name, setName] = useState("");
  const { refreshGrid } = useDevelopDetailContext();
  const { selectAll, toggledNodes, resetSelectedRows } =
    useDevelopSelectedRowsStoreShallow((s) => ({
      selectAll: s.selectAll,
      toggledNodes: s.toggledNodes,
      resetSelectedRows: s.resetSelectedRows,
    }));

  const { role } = useAuthContext();

  const { gridApi } = useDevelopDetailContext();

  const navigate = useNavigate();
  // §7 collapses everything after New experiment below 1,280 px of content;
  // with the 200 px nav that is a 1,700 px window here.
  const narrow = useMediaQuery("(max-width:1699px)");
  const { addOp } = useChangeSet(dataset);
  const gate = usePendingChangesGate();
  // L1 (the capability contract): on the real backend rows are deleted and copied
  // at once (delete_row, duplicate-rows); the actions that need versions,
  // splits or row status are not offered.
  const caps = datasetCaps();
  const classic = caps.gridWrites === "classic";
  const versionedActions = caps.selectionVersionedActions;
  const [confirmDelete, setConfirmDelete] = useState(false);
  const { initiateCreateMode } = useRunExperimentStoreShallow((st) => ({
    initiateCreateMode: st.initiateCreateMode,
  }));
  // Rows behind the selection (resolved once per action; select-all re-reads
  // the filtered table, ≤ 500 rows).
  const [resolving, setResolving] = useState(false);
  const [splitMenu, setSplitMenu] = useState(null); // {anchorEl, rows}
  const [queueMenu, setQueueMenu] = useState(null); // {anchorEl, rowIds}
  const [moreAnchor, setMoreAnchor] = useState(null);
  // the interface contract: row status (Approve / Needs review / Archive… / Restore).
  const [statusAnchor, setStatusAnchor] = useState(null);
  const [archiveRows, setArchiveRows] = useState(null);
  const [tagRows, setTagRows] = useState(null);
  const [exportFilter, setExportFilter] = useState(null);

  const withRows = async (fn, { keepNew = false } = {}) => {
    setResolving(true);
    try {
      const rows = await resolveSelectedRows({
        datasetId: dataset,
        api: gridApi.current,
        selectAll,
        toggledNodes,
      });
      fn(rows.filter((r) => !r.pending || (keepNew && r.pending === "new")));
    } catch {
      enqueueSnackbar("Couldn't read the selected rows.", { variant: "error" });
    } finally {
      setResolving(false);
    }
  };

  const handleClose = () => {
    setAnchorEl(null);
    setAnchorElSubmenu(null);
  };

  // the interface contract: status changes are pending changes too (Save version keeps them).
  const STATUS_DONE = {
    approved: "approved",
    needs_review: "marked for review",
  };
  const setStatus = (status, { restore = false } = {}) =>
    withRows(
      (rows) => {
        if (!rows.length) return;
        addOp({
          op: "set_status",
          row_ids: rows.map((r) => r.row_id),
          status,
          ...(restore ? { restore: true } : {}),
        });
        enqueueSnackbar(
          `${rows.length} row${rows.length === 1 ? "" : "s"} ${restore ? "restored" : STATUS_DONE[status]} · saved with the next version`,
          { variant: "info" },
        );
        unCheckedHandler();
      },
      { keepNew: true },
    );
  const archive = (reason) => {
    const rows = archiveRows ?? [];
    setArchiveRows(null);
    if (!rows.length) return;
    addOp({
      op: "set_status",
      row_ids: rows.map((r) => r.row_id),
      status: "archived",
      reason,
    });
    enqueueSnackbar(
      `${rows.length} row${rows.length === 1 ? "" : "s"} archived · saved with the next version`,
      { variant: "info" },
    );
    unCheckedHandler();
  };

  // Delete is a pending change (restorable until the version is saved).
  const deletePending = () =>
    withRows((rows) => {
      if (!rows.length) return;
      addOp({ op: "delete_rows", row_ids: rows.map((r) => r.row_id) });
      trackEvent(Events.deleteRowSuccessful, {
        [PropertyName.deleteRow]: { selectedRow: rows.map((r) => r.row_id) },
      });
      enqueueSnackbar(
        `${rows.length} row${rows.length === 1 ? "" : "s"} marked for deletion · restore from the Row column until you save`,
        { variant: "info" },
      );
      unCheckedHandler();
    });

  // Row-copy actions send explicit ids, so a filtered "select all" copies
  // exactly the rows in view.
  // An explicit selection is known synchronously; "select all" re-reads the
  // filtered table. `withIds(fn)` calls fn with the ids either way.
  const withIds = (fn) => {
    if (!selectAll)
      return fn(toggledNodes.filter((id) => !String(id).startsWith("new-")));
    return resolveSelectedRows({
      datasetId: dataset,
      api: gridApi.current,
      selectAll,
      toggledNodes,
    }).then((rows) => fn(rows.filter((r) => !r.pending).map((r) => r.row_id)));
  };

  // L1: delete now (real backend). A confirm first: there is no restore.
  const { mutate: onDeleteRowsNow, isPending: isDeletingNow } = useMutation({
    mutationFn: () =>
      Promise.resolve(
        withIds((ids) =>
          ids.length
            ? deleteRowsClassic({ datasetId: dataset, rowIds: ids }).then(
                () => ids,
              )
            : [],
        ),
      ),
    onSuccess: (ids) => {
      setConfirmDelete(false);
      if (!ids?.length) return;
      trackEvent(Events.deleteRowSuccessful, {
        [PropertyName.deleteRow]: { selectedRow: ids },
      });
      unCheckedHandler();
      refreshGrid();
      enqueueSnackbar(`${rowsWord(ids.length)} deleted`, {
        variant: "success",
      });
    },
    onError: (e) => {
      setConfirmDelete(false);
      enqueueSnackbar(refusalSentence(e, "Couldn't delete the rows."), {
        variant: "error",
      });
    },
  });
  const onDelete = () => (classic ? setConfirmDelete(true) : deletePending());

  const { mutate: onDuplicateDatasetRows, isPending: isDuplicateLoading } =
    useMutation({
      mutationFn: () =>
        withIds((selectedIds) =>
          classic
            ? duplicateRowsClassic({
                datasetId: dataset,
                rowIds: selectedIds,
                copies: noOfCopies,
              })
            : axios.post(endpoints.develop.duplicateDatasetRows(dataset), {
                num_copies: Number(noOfCopies),
                row_ids: selectedIds,
                selected_all_rows: false,
                // v2.1 g8: the copies come back as operations for the page's
                // pending changes (Save version keeps them, Undo drops them).
                pending: true,
              }),
        ),
      onSuccess: (res) => {
        const selectedIds = toggledNodes;
        if (classic) {
          // L1: dev wrote the copies already (no pending change, no Undo).
          const r = res?.data?.result ?? {};
          setIsDuplicate(false);
          setTimeout(unCheckedHandler, 100);
          refreshGrid();
          enqueueSnackbar(
            `Added ${rowsWord(Number(r.total_new_rows) || 0)} (copies of ${rowsWord(Number(r.source_rows) || selectedIds.length)})`,
            { variant: "success" },
          );
          return;
        }

        trackEvent(Events.duplicateRowSuccessful, {
          [PropertyName.duplicateRow]: {
            rowId: selectedIds[0],
            num_copies: Number(noOfCopies),
          },
        });
        setIsDuplicate(false);
        setTimeout(unCheckedHandler, 100);
        const result = res?.data?.result ?? {};
        const ops = duplicateOps(
          result.operations,
          `${Date.now().toString(36)}-`,
        );
        ops.forEach((op) => addOp(op));
        const tempIds = ops.map((o) => o.temp_id);
        revealNewRows(dataset, tempIds);
        refreshGrid();
        notify(duplicateToast(result), {
          key: "rows-duplicate",
          undo: {
            onUndo: () =>
              useChangeSetStore
                .getState()
                .addOp(dataset, { op: "delete_rows", row_ids: tempIds }),
          },
        });
      },
      // the interface contract: a 400 (no selected row is in the latest version) says why.
      onError: (e) => {
        setIsDuplicate(false);
        enqueueSnackbar(
          typeof e?.result === "string"
            ? e.result
            : e?.message ?? "Couldn't duplicate the rows.",
          { variant: "error" },
        );
      },
    });

  const { mutate: onCreateDatasetRows, isPending: isCreateDatasetLoading } =
    useMutation({
      mutationFn: (newDatasetName) =>
        withIds((selectedIds) => {
          const resolvedName = newDatasetName || name;
          trackEvent(Events.addRowToNewDatasetSuccessful, {
            [PropertyName.rowToNewDataset]: {
              row_id: selectedIds[0],
              name: resolvedName,
            },
          });
          return axios.post(endpoints.develop.createDatasetRows(dataset), {
            row_ids: selectedIds,
            selected_all_rows: false,
            name: resolvedName,
          });
        }),
      onSuccess: ({ data }) => {
        const createdDatasetId = getCreatedRowsDatasetId(data);

        handleClose();
        setTimeout(unCheckedHandler, 100);
        refreshGrid();
        enqueueSnackbar(
          <SnackbarWithAction
            message={newDatasetToast(selectedCount, name)}
            buttonText="View Dataset"
            onClick={() =>
              navigate(`/dashboard/develop/${createdDatasetId}?tab=data`)
            }
          />,
          {
            variant: "success",
          },
        );
      },
    });

  const { mutate: onRunEvals, isPending: runEvalsLoading } = useMutation({
    mutationFn: () =>
      withIds((selectedIds) => {
        const selectedEvalIds = gridRef.current?.api?.getSelectedRows();

        const evalIds = selectedEvalIds
          .filter((item) => item?.originType == "eval")
          .map((row) => row.field);
        const runPromptIds = selectedEvalIds
          .filter((item) => item?.originType == "run_prompt")
          .map((row) => row.field);

        const requests = [];
        if (evalIds.length) {
          requests.push(
            axios.post(endpoints.develop.evaluateRows(), {
              user_eval_metric_ids: evalIds,
              row_ids: selectedIds,
              selected_all_rows: false,
            }),
          );
        }
        if (runPromptIds.length) {
          requests.push(
            axios.post(endpoints.develop.evaluateRunRows(), {
              run_prompt_ids: runPromptIds,
              row_ids: selectedIds,
              selected_all_rows: false,
            }),
          );
        }
        trackEvent(Events.rowEvaluationsRunSuccessful, {
          [PropertyName.rowEval]: {
            user_eval_metric_ids: evalIds,
            run_prompt_ids: runPromptIds,
            row_ids: selectedIds,
          },
        });
        return Promise.all(requests);
      }),
    onSuccess: (responses) => {
      handleClose();
      setTimeout(unCheckedHandler, 100);
      refreshGrid();
      // The server counts the cells it started (a re-run prompt's evals
      // follow it); the toast says what is running, not that it finished.
      // Distinct cells: a prompt's evals that were also picked count once.
      const results = (responses ?? []).map((r) => r?.data?.result ?? {});
      const columns = new Set(results.flatMap((r) => r.columns ?? []));
      const rows = Math.max(0, ...results.map((r) => Number(r.rows) || 0));
      const cells = columns.size
        ? columns.size * rows
        : results.reduce((sum, r) => sum + (Number(r.cells) || 0), 0);
      enqueueSnackbar(
        cells
          ? `Running ${cells} cell${cells === 1 ? "" : "s"}`
          : "Evals & Prompts started",
        { variant: "success" },
      );
    },
  });

  const { mutate: onMergeRows, isPending: isMergeRowsLoading } = useMutation({
    mutationFn: (selectedTargetDatasetId) =>
      withIds((selectedIds) =>
        axios.post(endpoints.develop.mergeDatasetRows(dataset), {
          target_dataset_id: selectedTargetDatasetId || targetDatasetId,
          row_ids: selectedIds,
          selected_all_rows: false,
        }),
      ),
    onSuccess: (res, selectedTargetDatasetId) => {
      const resolvedTargetDatasetId =
        selectedTargetDatasetId || targetDatasetId;

      handleClose();
      setTimeout(unCheckedHandler, 100);
      refreshGrid();
      enqueueSnackbar(
        <SnackbarWithAction
          message={mergeToast(res?.data?.result ?? {})}
          buttonText="View Dataset"
          onClick={() =>
            navigate(`/dashboard/develop/${resolvedTargetDatasetId}?tab=data`)
          }
        />,
        {
          variant: "success",
        },
      );
    },
    // v2.1 g8: a refusal (no column matches by name) says why.
    onError: (e) => {
      handleClose();
      enqueueSnackbar(
        typeof e?.result === "string"
          ? e.result
          : e?.message ?? "Couldn't add the rows to that dataset.",
        { variant: "error" },
      );
    },
  });

  const unCheckedHandler = () => {
    resetSelectedRows();
    gridApi.current?.deselectAll();
  };

  const context = gridApi.current?.getGridOption("context");
  const gridTotal = context?.filteredRowCount ?? context?.totalRowCount ?? 0;
  const selectedCount = useMemo(() => {
    if (selectAll) {
      return (context?.totalRowCount ?? 0) - toggledNodes.length;
    } else {
      return toggledNodes.length;
    }
  }, [toggledNodes, selectAll, context?.totalRowCount]);
  const filtered =
    getDataGridFilters(dataset).length > 0 || Boolean(getDataGridSearch());
  const countLabel = selectionLabel({
    selectAll,
    count: selectedCount,
    total: gridTotal,
    filtered,
  });
  const overCap = selectedCount > BULK_ROW_CAP;
  const canUpdate = RolePermission.DATASETS[PERMISSIONS.UPDATE][role];

  // L5 G3: unsaved (new) rows go through the door too: `Save version &
  // continue` follows them into the saved version (the save's row_id_map),
  // so the wizard opens on exactly these rows; `Use vN without them` drops
  // the rows that version does not have.
  const startExperiment = () =>
    withRows(
      (rows) =>
        gate("experiment", (saved) => {
          // A temp id of a pending add_row is unsaved even when its grid
          // node was not loaded (the change set is the source of truth).
          const tempIds = new Set(
            (useChangeSetStore.getState().sets?.[dataset]?.ops ?? [])
              .filter((o) => o.op === "add_row")
              .map((o) => o.temp_id),
          );
          const ids = rowIdsAfterGate(
            rows.map((r) => ({
              id: r.row_id,
              unsaved: r.pending === "new" || tempIds.has(r.row_id),
            })),
            saved,
          );
          if (!ids.length) {
            enqueueSnackbar(
              "None of the selected rows is in the saved version.",
              { variant: "info" },
            );
            return;
          }
          // v2.1 g8: `rows=` keeps the selection across a reload.
          writeExperimentRows(ids);
          initiateCreateMode({ prefill: { row_ids: ids } });
          enqueueSnackbar(
            `New experiment on ${ids.length} selected row${ids.length === 1 ? "" : "s"}`,
            { variant: "info" },
          );
        }),
      { keepNew: true },
    );

  const runButton = (
    <StyledButton
      variant="text"
      size="small"
      disabled={!canUpdate}
      startIcon={
        <SvgColor
          src={`/assets/icons/action_buttons/ic_run_prompt.svg`}
          sx={{ width: "18px", height: "18px", color: "text.secondary" }}
        />
      }
      onClick={() => {
        trackEvent(Events.rowEvaluationsClicked);
        setIsRunEval(!isRunEval);
      }}
    >
      Run
    </StyledButton>
  );
  const duplicateButton = (
    <StyledButton
      variant="text"
      size="small"
      disabled={!canUpdate}
      startIcon={
        <Iconify icon="gg:duplicate" sx={{ color: "text.secondary" }} />
      }
      onClick={() => {
        trackEvent(Events.duplicateRowClicked);
        setIsDuplicate(!isDuplicate);
      }}
    >
      Duplicate
    </StyledButton>
  );
  const deleteButton = (
    <StyledButton
      variant="text"
      size="small"
      disabled={!RolePermission.DATASETS[PERMISSIONS.DELETE][role] || resolving}
      startIcon={
        <SvgColor
          src={`/assets/icons/components/ic_delete.svg`}
          sx={{ width: "20px", height: "20px", color: "text.secondary" }}
        />
      }
      onClick={() => {
        trackEvent(Events.deleteRowClicked);
        onDelete();
      }}
    >
      Delete
    </StyledButton>
  );
  const addToDatasetMenu = (
    <Box>
      <DataMenuList
        handleClose={handleClose}
        setAnchorEl={setAnchorEl}
        anchorEl={anchorEl}
        setAnchorElSubmenu={setAnchorElSubmenu}
        anchorElSubmenu={anchorElSubmenu}
        setName={setName}
        loading={isCreateDatasetLoading || isMergeRowsLoading}
        onCreateDatasetRows={onCreateDatasetRows}
        onMergeRows={onMergeRows}
        setTargetDatasetId={setTargetDatasetId}
      />
    </Box>
  );

  return (
    <StyledBox>
      <Box sx={{ display: "flex", alignItems: "center" }}>
        <StyledTypography sx={{ whiteSpace: "nowrap" }}>
          {countLabel}
        </StyledTypography>
      </Box>

      <Divider orientation="vertical" flexItem />
      {versionedActions && (
        <StyledButton
          variant="text"
          size="small"
          disabled={!canUpdate || resolving}
          startIcon={
            <Iconify
              icon="mdi:tag-multiple-outline"
              sx={{ color: "text.secondary" }}
            />
          }
          endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
          onClick={(e) => {
            const anchorEl = e.currentTarget;
            setSplitMenu({ anchorEl, rows: null });
            withRows((rows) => setSplitMenu({ anchorEl, rows }));
          }}
        >
          Assign split
        </StyledButton>
      )}
      <CustomTooltip
        show={overCap}
        title={`Select up to ${BULK_ROW_CAP} rows to add to a queue`}
        placement="bottom"
        size="small"
        arrow
      >
        <span>
          <StyledButton
            variant="text"
            size="small"
            disabled={overCap || resolving}
            startIcon={
              <Iconify
                icon="mdi:playlist-plus"
                sx={{ color: "text.secondary" }}
              />
            }
            onClick={(e) => {
              const anchorEl = e.currentTarget;
              withRows((rows) =>
                setQueueMenu({ anchorEl, rowIds: rows.map((r) => r.row_id) }),
              );
            }}
          >
            Add to queue
          </StyledButton>
        </span>
      </CustomTooltip>
      {/* L1: New experiment on rows, Generate, Download (n) and Status &
          tags need row ids, versions or row status that dev does not have:
          not on the real backend. */}
      {versionedActions && (
        <>
          <StyledButton
            variant="text"
            size="small"
            disabled={!canUpdate || resolving}
            startIcon={
              <SvgColor
                src="/assets/icons/action_buttons/ic_experiment.svg"
                sx={{ width: 18, height: 18, color: "text.secondary" }}
              />
            }
            onClick={startExperiment}
          >
            New experiment
          </StyledButton>
          {/* the interface contract-8a: a model drafts rows like the selected ones (reviewed first). */}
          <StyledButton
            variant="text"
            size="small"
            disabled={!canUpdate || resolving}
            startIcon={<Iconify icon="mdi:creation-outline" width={16} />}
            onClick={() =>
              withRows(
                (rows) =>
                  rows.length &&
                  useGenerateRows.getState().open({
                    datasetId: dataset,
                    rowIds: rows.map((r) => r.row_id),
                  }),
              )
            }
            data-testid="selection-generate"
          >
            {`Generate more like these (${selectedCount})`}
          </StyledButton>
          {/* HO D6 (the interface contract): the selected rows through Download… (every one). */}
          <StyledButton
            variant="text"
            size="small"
            disabled={resolving}
            startIcon={<Iconify icon="mdi:download-outline" width={16} />}
            onClick={() =>
              withIds(
                (ids) =>
                  ids.length && setExportFilter(datasetSelectionFilter(ids)),
              )
            }
            data-testid="selection-download"
          >
            {`Download (${selectedCount})`}
          </StyledButton>
          <StyledButton
            variant="text"
            size="small"
            disabled={!canUpdate || resolving}
            startIcon={<Iconify icon="mdi:list-status" width={16} />}
            endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
            onClick={(e) => setStatusAnchor(e.currentTarget)}
            data-testid="selection-status"
          >
            Status & tags
          </StyledButton>
          <Menu
            anchorEl={statusAnchor}
            open={Boolean(statusAnchor)}
            onClose={() => setStatusAnchor(null)}
          >
            <MenuItem
              onClick={() => {
                setStatusAnchor(null);
                setStatus("approved");
              }}
            >
              <ListItemIcon>
                <Iconify icon="mdi:check-decagram-outline" width={16} />
              </ListItemIcon>
              Approve
            </MenuItem>
            <MenuItem
              onClick={() => {
                setStatusAnchor(null);
                setStatus("needs_review");
              }}
            >
              <ListItemIcon>
                <Iconify icon="mdi:account-clock-outline" width={16} />
              </ListItemIcon>
              Mark as needs review
            </MenuItem>
            <MenuItem
              onClick={() => {
                setStatusAnchor(null);
                withRows((rows) => rows.length && setArchiveRows(rows));
              }}
            >
              <ListItemIcon>
                <Iconify icon="mdi:archive-outline" width={16} />
              </ListItemIcon>
              Archive…
            </MenuItem>
            <MenuItem
              onClick={() => {
                setStatusAnchor(null);
                withRows((rows) => rows.length && setTagRows(rows), {
                  keepNew: true,
                });
              }}
            >
              <ListItemIcon>
                <Iconify icon="mdi:tag-multiple-outline" width={16} />
              </ListItemIcon>
              Tag…
            </MenuItem>
            <MenuItem
              onClick={() => {
                setStatusAnchor(null);
                setStatus("approved", { restore: true });
              }}
            >
              <ListItemIcon>
                <Iconify icon="mdi:archive-arrow-up-outline" width={16} />
              </ListItemIcon>
              Restore
            </MenuItem>
          </Menu>
          <TagRowsDialog
            open={Boolean(tagRows)}
            count={tagRows?.length ?? 0}
            onClose={() => setTagRows(null)}
            onApply={({ add, remove }) => {
              const rows = tagRows ?? [];
              setTagRows(null);
              if (!rows.length) return;
              addOp({
                op: "set_tags",
                row_ids: rows.map((r) => r.row_id),
                add,
                remove,
              });
              enqueueSnackbar(
                `${rows.length} row${rows.length === 1 ? "" : "s"} ${add.length ? `tagged ${add.join(", ")}` : `untagged ${remove.join(", ")}`} · saved with the next version`,
                { variant: "info" },
              );
              unCheckedHandler();
            }}
          />
          <ArchiveRowsDialog
            open={Boolean(archiveRows)}
            count={archiveRows?.length ?? 0}
            onClose={() => setArchiveRows(null)}
            onArchive={archive}
          />
        </>
      )}

      {/* shared-edit(F37): scoped generation from the resolved selection. */}
      <SyntheticMoreAction
        datasetId={dataset}
        count={selectedCount}
        disabled={!canUpdate || resolving}
        resolveRows={withRows}
      />
      {narrow ? (
        <>
          <StyledButton
            variant="text"
            size="small"
            endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
            onClick={(e) => setMoreAnchor(e.currentTarget)}
          >
            More
          </StyledButton>
          <Menu
            anchorEl={moreAnchor}
            open={Boolean(moreAnchor)}
            onClose={() => setMoreAnchor(null)}
          >
            <MenuItem
              disabled={!canUpdate}
              onClick={() => {
                setMoreAnchor(null);
                trackEvent(Events.rowEvaluationsClicked);
                setIsRunEval(true);
              }}
            >
              <ListItemIcon>
                <SvgColor
                  src="/assets/icons/action_buttons/ic_run_prompt.svg"
                  sx={{ width: 16, height: 16 }}
                />
              </ListItemIcon>
              Run
            </MenuItem>
            <MenuItem
              disabled={!canUpdate}
              onClick={() => {
                setMoreAnchor(null);
                trackEvent(Events.duplicateRowClicked);
                setIsDuplicate(true);
              }}
            >
              <ListItemIcon>
                <Iconify icon="gg:duplicate" width={16} />
              </ListItemIcon>
              Duplicate
            </MenuItem>
            <MenuItem
              disabled={!RolePermission.DATASETS[PERMISSIONS.DELETE][role]}
              onClick={() => {
                setMoreAnchor(null);
                trackEvent(Events.deleteRowClicked);
                onDelete();
              }}
            >
              <ListItemIcon>
                <SvgColor
                  src="/assets/icons/components/ic_delete.svg"
                  sx={{ width: 16, height: 16 }}
                />
              </ListItemIcon>
              Delete
            </MenuItem>
          </Menu>
          {addToDatasetMenu}
        </>
      ) : (
        <>
          {runButton}
          {duplicateButton}
          {addToDatasetMenu}
          {deleteButton}
        </>
      )}
      <Divider orientation="vertical" flexItem />

      <StyledButton
        variant="text"
        size="small"
        onClick={() => unCheckedHandler()}
        aria-label="Clear selection"
        sx={{
          padding: 0,
          minWidth: 0,
          marginRight: 2,
          color: "text.secondary",
          gap: 0.75,
        }}
      >
        Clear <KeyCap>Esc</KeyCap>
      </StyledButton>

      <DatasetExportDialog
        open={Boolean(exportFilter)}
        onClose={() => setExportFilter(null)}
        datasetId={dataset}
        currentFilter={exportFilter}
      />
      <AssignSplitsMenu
        anchorEl={splitMenu?.anchorEl ?? null}
        onClose={() => setSplitMenu(null)}
        datasetId={dataset}
        loading={Boolean(splitMenu && !splitMenu.rows)}
        rowIds={(splitMenu?.rows ?? []).map((r) => r.row_id)}
        rowSplits={(splitMenu?.rows ?? []).map((r) => r.split_ids)}
      />
      {queueMenu && (
        <AddToQueueDialog
          anchorEl={queueMenu.anchorEl}
          onClose={() => setQueueMenu(null)}
          sourceType="dataset_row"
          sourceIds={queueMenu.rowIds}
          itemName="Row"
          onSuccess={() => setQueueMenu(null)}
        />
      )}

      {isRunEval && (
        <CustomDialog
          title="Run Evals & Prompts"
          actionButton="Run"
          open={isRunEval}
          className="dialogButton"
          loading={runEvalsLoading}
          isData={isData}
          onClose={() => {
            setIsData(false);
            setIsRunEval((prev) => !prev);
          }}
          onClickAction={() => onRunEvals()}
        >
          <RunEvals gridRef={gridRef} setIsData={setIsData} />
        </CustomDialog>
      )}

      {confirmDelete && (
        <CustomDialog
          title={`Delete Row${selectedCount > 1 ? "s" : ""}`}
          color="error"
          actionButton="Delete"
          open={confirmDelete}
          preTitleIcon="solar:trash-bin-trash-bold"
          onClose={() => setConfirmDelete(false)}
          loading={isDeletingNow}
          onClickAction={() => onDeleteRowsNow()}
        >
          <DeleteRowAction selectedCount={selectedCount} />
        </CustomDialog>
      )}

      {isDuplicate && (
        <CustomDialog
          title="Duplicate Dataset Rows"
          actionButton="Duplicate"
          open={isDuplicate}
          className="dialogButton"
          onClose={() => setIsDuplicate((prev) => !prev)}
          loading={isDuplicateLoading}
          onClickAction={() =>
            gate("structural", () => onDuplicateDatasetRows())
          }
        >
          <DuplicateRowAction setNoOfCopies={setNoOfCopies} />
        </CustomDialog>
      )}
    </StyledBox>
  );
};

/**
 * the interface contract (DV-06) · the selection bar as of an older version: read-only.
 * Only the count, `Download (n)` (the rows as they were in that version),
 * `Restore n deleted rows…` (when the selection holds rows deleted after
 * it, or by it under `compare=in`) and Clear. Every write is absent.
 */
const AsOfSelectionBar = ({ asOfNumber, versionId }) => {
  const { dataset } = useParams();
  const { gridApi } = useDevelopDetailContext();
  const view = useAsOfView(dataset);
  const { selectAll, toggledNodes, resetSelectedRows } =
    useDevelopSelectedRowsStoreShallow((s) => ({
      selectAll: s.selectAll,
      toggledNodes: s.toggledNodes,
      resetSelectedRows: s.resetSelectedRows,
    }));
  const [exportFilter, setExportFilter] = useState(null);
  const filters = getDataGridFilters(dataset);
  const search = getDataGridSearch();
  // Select all: every row of the as-of table (N3, same narrowing), paged.
  const allRows = useQuery({
    queryKey: [
      "dataset-detail",
      dataset,
      "as-of-selection",
      versionId,
      view.compare,
      view.change,
      filters,
      search,
    ],
    enabled: Boolean(selectAll && versionId),
    queryFn: async () => {
      const out = [];
      for (let page = 0; page !== null; ) {
        const req = tableRequestFor({
          datasetId: dataset,
          versionNumber: asOfNumber,
          versionId,
          compare: view.compare,
          change: view.change,
          params: {
            page_size: BULK_ROW_CAP,
            current_page_index: page,
            filters: JSON.stringify(filters),
            sort: JSON.stringify([]),
            ...(search && {
              search: JSON.stringify({
                key: search,
                type: ["text", "image", "audio"],
              }),
            }),
          },
        });
        const { data } = await axios.get(req.url, { params: req.params });
        const rows = data?.result?.table ?? [];
        out.push(...rows);
        page = nextPageOf(data?.result?.metadata, page, rows.length);
      }
      return out;
    },
  });
  const rows = selectAll
    ? (allRows.data ?? []).filter((r) => !toggledNodes.includes(r.row_id))
    : toggledNodes.map(
        (id) => gridApi.current?.getRowNode?.(id)?.data ?? { row_id: id },
      );
  const restore = restorableRows(rows, asOfNumber);
  const { exportFrom, exportRows } = asOfExport(rows, asOfNumber);
  const context = gridApi.current?.getGridOption("context");
  const gridTotal = context?.filteredRowCount ?? context?.totalRowCount ?? 0;
  const count = selectAll
    ? allRows.data
      ? rows.length
      : Math.max(0, (context?.totalRowCount ?? 0) - toggledNodes.length)
    : toggledNodes.length;
  // Rows not in the viewed version are not counted unless the export reads
  // them from its parent (see asOfExport).
  const downloadCount = selectAll && !allRows.data ? count : exportRows.length;
  const countLabel = selectionLabel({
    selectAll,
    count,
    total: gridTotal,
    filtered: filters.length > 0 || Boolean(search),
  });
  const clear = () => {
    resetSelectedRows();
    gridApi.current?.deselectAll();
  };
  const openRestore = () =>
    restore && useRestoreRowsDialog.getState().open(restore);
  useRegisterCommands(
    "selection-as-of",
    restore
      ? [
          {
            id: "selection.restoreRows",
            label: "Restore selected rows…",
            icon: "mdi:restore",
            run: openRestore,
          },
        ]
      : [],
    [restore?.rows?.length, restore?.from],
  );
  const busy = Boolean(selectAll && allRows.isLoading);
  return (
    <StyledBox data-testid="selection-bar-as-of">
      <Box sx={{ display: "flex", alignItems: "center" }}>
        <StyledTypography sx={{ whiteSpace: "nowrap" }}>
          {countLabel}
        </StyledTypography>
      </Box>
      <Divider orientation="vertical" flexItem />
      {downloadCount > 0 ? (
        <StyledButton
          variant="text"
          size="small"
          disabled={busy || !exportRows.length}
          startIcon={<Iconify icon="mdi:download-outline" width={16} />}
          onClick={() =>
            setExportFilter(
              datasetSelectionFilter(exportRows.map((r) => r.row_id)),
            )
          }
          data-testid="selection-download"
        >
          {`Download (${downloadCount})`}
        </StyledButton>
      ) : null}
      {restore ? (
        <StyledButton
          variant="text"
          size="small"
          startIcon={<Iconify icon="mdi:restore" width={16} />}
          onClick={openRestore}
          data-testid="selection-restore-rows"
        >
          {`Restore ${plural(restore.rows.length, "deleted row")}…`}
        </StyledButton>
      ) : null}
      <Divider orientation="vertical" flexItem />
      <StyledButton
        variant="text"
        size="small"
        onClick={clear}
        aria-label="Clear selection"
        sx={{
          padding: 0,
          minWidth: 0,
          marginRight: 2,
          color: "text.secondary",
          gap: 0.75,
        }}
      >
        Clear <KeyCap>Esc</KeyCap>
      </StyledButton>
      <DatasetExportDialog
        open={Boolean(exportFilter)}
        onClose={() => setExportFilter(null)}
        datasetId={dataset}
        currentFilter={exportFilter}
        versionNumber={exportFrom}
      />
    </StyledBox>
  );
};

/**
 * the interface contract review: what `Download (n)` exports as of vN. Rows this version
 * deleted (`change.kind = "deleted"`, compare=in) are not in vN: when the
 * selection holds only such rows they are exported as they were in its
 * parent (where the grid shows them from, and where Restore takes them
 * from); in a mixed selection only the rows in vN are exported.
 * @returns {{exportFrom: number, exportRows: object[]}}
 */
export function asOfExport(rows = [], asOfNumber) {
  const gone = (r) => r?.row_meta?.change?.kind === "deleted";
  const goneRows = rows.filter(gone);
  if (rows.length && goneRows.length === rows.length && asOfNumber > 1)
    return { exportFrom: asOfNumber - 1, exportRows: rows };
  return { exportFrom: asOfNumber, exportRows: rows.filter((r) => !gone(r)) };
}

AsOfSelectionBar.propTypes = {
  asOfNumber: PropTypes.number.isRequired,
  versionId: PropTypes.string,
};

/**
 * The floating selection bar of the Data grid: the full bar on the latest
 * version; as of an older version (`?version=N`, the interface contract) the read-only bar.
 */
const DevelopDataSelectionActive = () => {
  const { dataset } = useParams();
  const [searchParams] = useSearchParams();
  const asked = parseVersionParam(searchParams.get("version"));
  const asOfState = useAsOfVersion(dataset);
  // Until N1 answers, `?version=` counts as as-of (no write flashes in).
  const asOf = asked != null && (asOfState.isLoading || asOfState.isAsOf);
  if (asOf)
    return (
      <AsOfSelectionBar
        asOfNumber={asked}
        versionId={asOfState.isAsOf ? asOfState.version?.id : undefined}
      />
    );
  return <LatestSelectionBar />;
};

DevelopDataSelectionActive.propTypes = {};

export default DevelopDataSelectionActive;
