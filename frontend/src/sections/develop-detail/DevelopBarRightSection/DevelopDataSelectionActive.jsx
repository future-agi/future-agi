import { Box, Button, Divider, Typography } from "@mui/material";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { styled } from "@mui/system";
import axios, { endpoints } from "src/utils/axios";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "src/components/snackbar";
import CustomDialog from "../Common/CustomDialog/CustomDialog";
import DataMenuList from "../DataTab/DataMenuList/DataMenuList";
import DuplicateRowAction from "../DataTab/Duplicate/DuplicateRowAction";
import RunEvals from "../DataTab/RunEvals/RunEvals";
import DeleteRowAction from "../DataTab/Delete/DeleteRowAction";
import "./developData.css";
import { useParams } from "react-router";
import SnackbarWithAction from "../Common/SnackbarWithAction";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import SvgColor from "src/components/svg-color";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";
import { useDevelopSelectedRowsStore, useDevelopSelectedRowsStoreShallow } from "../states";
import {
  assertRowSelectionCurrent,
  captureRowSelection,
  resolveSelectedRowIds,
} from "../DataTab/datasetRowSelection";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { getCreatedRowsDatasetId } from "./createDatasetRowsResponse";

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

const DevelopDataSelectionActive = () => {
  const { dataset } = useParams();
  const gridRef = useRef(null);
  const [isData, setIsData] = useState(false);
  const [isDuplicate, setIsDuplicate] = useState(false);
  const [isDelete, setIsDelete] = useState(false);
  const [isRunEval, setIsRunEval] = useState(false);
  const [noOfCopies, setNoOfCopies] = useState(0);
  const [anchorEl, setAnchorEl] = React.useState(null);
  const [anchorElSubmenu, setAnchorElSubmenu] = React.useState(null);
  const [targetDatasetId, setTargetDatasetId] = useState("");
  const [name, setName] = useState("");
  const { refreshGrid } = useDevelopDetailContext();
  const { selectAll, toggledNodes, resetSelectedRows, selectionRevision } =
    useDevelopSelectedRowsStoreShallow((s) => ({
      selectAll: s.selectAll,
      toggledNodes: s.toggledNodes,
      resetSelectedRows: s.resetSelectedRows,
      selectionRevision: s.selectionRevision,
    }));

  const { role } = useAuthContext();

  const { gridApi } = useDevelopDetailContext();

  const navigate = useNavigate();
  const confirmationRef = useRef(null);
  const liveView = useRef({ dataset, active: true });
  liveView.current.dataset = dataset;

  useEffect(() => {
    liveView.current.active = true;
    return () => { liveView.current.active = false; confirmationRef.current = null; };
  }, []);

  useEffect(() => {
    confirmationRef.current = null;
    setIsDelete(false);
    setIsDuplicate(false);
  }, [dataset, selectionRevision]);

  const assertActionCurrent = (action) => {
    if (!action || confirmationRef.current !== action || !liveView.current.active ||
        liveView.current.dataset !== action.selection.scope.dataset) {
      throw new Error("Selection changed. Select the rows again.");
    }
    assertRowSelectionCurrent(action.selection, liveView.current.dataset, gridApi.current);
  };

  const openRowAction = (type) => {
    try {
      confirmationRef.current = { type, selection: captureRowSelection(dataset, gridApi.current) };
      setIsDelete(type === "delete");
      setIsDuplicate(type === "duplicate");
    } catch (error) {
      enqueueSnackbar(error.message, { variant: "warning" });
    }
  };

  const closeRowAction = () => {
    confirmationRef.current = null;
    setIsDelete(false);
    setIsDuplicate(false);
  };

  const onRowActionError = (_error, action) => {
    if (!liveView.current.active || confirmationRef.current !== action) return;
    closeRowAction();
    enqueueSnackbar("The row action could not be completed. Select the rows again and retry.", {
      variant: "error",
    });
  };

  const clearCompletedSelection = (action) => {
    if (useDevelopSelectedRowsStore.getState().selectionRevision === action.selection.revision) {
      resetSelectedRows();
      gridApi.current?.deselectAll();
    }
  };

  const handleClose = () => {
    setAnchorEl(null);
    setAnchorElSubmenu(null);
  };

  const { mutate: onDeleteDatasetRow, isPending: isDeleting, variables: deletingAction } = useMutation({
    mutationFn: async (action) => {
      const selectedIds = await resolveSelectedRowIds(action.selection, () => assertActionCurrent(action));
      // Recheck after the resolver's promise settles and immediately before dispatch.
      assertActionCurrent(action);
      await axios.delete(endpoints.develop.deleteDatasetRow(action.selection.scope.dataset), {
        data: { row_ids: selectedIds, selected_all_rows: false },
      });
      return selectedIds;
    },
    onError: onRowActionError,
    onSuccess: (selectedIds, action) => {
      if (!liveView.current.active || liveView.current.dataset !== action.selection.scope.dataset) return;
      trackEvent(Events.deleteRowSuccessful, {
        [PropertyName.deleteRow]: {
          selectedRow: selectedIds,
        },
      });
      if (confirmationRef.current === action) closeRowAction();
      clearCompletedSelection(action);
      refreshGrid();
      enqueueSnackbar("Rows deleted successfully", {
        variant: "success",
      });
    },
  });

  const { mutate: onDuplicateDatasetRows, isPending: isDuplicateLoading, variables: duplicatingAction } =
    useMutation({
      mutationFn: async (action) => {
        const selectedIds = await resolveSelectedRowIds(action.selection, () => assertActionCurrent(action));
        assertActionCurrent(action);
        await axios.post(endpoints.develop.duplicateDatasetRows(action.selection.scope.dataset), {
          num_copies: action.copies,
          row_ids: selectedIds,
          selected_all_rows: false,
        });
        return selectedIds;
      },
      onError: onRowActionError,
      onSuccess: (selectedIds, action) => {
        if (!liveView.current.active || liveView.current.dataset !== action.selection.scope.dataset) return;

        trackEvent(Events.duplicateRowSuccessful, {
          [PropertyName.duplicateRow]: {
            rowId: selectedIds[0],
            num_copies: action.copies,
          },
        });
        if (confirmationRef.current === action) closeRowAction();
        clearCompletedSelection(action);
        refreshGrid();
        enqueueSnackbar("Rows duplicated successfully", {
          variant: "success",
        });
      },
    });

  const { mutate: onCreateDatasetRows, isPending: isCreateDatasetLoading } =
    useMutation({
      mutationFn: (newDatasetName) => {
        const selectedIds = toggledNodes;
        const resolvedName = newDatasetName || name;
        trackEvent(Events.addRowToNewDatasetSuccessful, {
          [PropertyName.rowToNewDataset]: {
            row_id: selectedIds[0],
            name: resolvedName,
          },
        });
        return axios.post(endpoints.develop.createDatasetRows(dataset), {
          row_ids: selectedIds,
          selected_all_rows: selectAll,
          name: resolvedName,
        });
      },
      onSuccess: ({ data }) => {
        const createdDatasetId = getCreatedRowsDatasetId(data);

        handleClose();
        setTimeout(unCheckedHandler, 100);
        refreshGrid();
        enqueueSnackbar(
          <SnackbarWithAction
            message="Datapoint added to the created dataset"
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
    mutationFn: () => {
      const selectedEvalIds = gridRef.current?.api?.getSelectedRows();
      const selectedIds = toggledNodes;

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
            selected_all_rows: selectAll,
          }),
        );
      }
      if (runPromptIds.length) {
        requests.push(
          axios.post(endpoints.develop.evaluateRunRows(), {
            run_prompt_ids: runPromptIds,
            row_ids: selectedIds,
            selected_all_rows: selectAll,
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
    },
    onSuccess: () => {
      handleClose();
      setTimeout(unCheckedHandler, 100);
      refreshGrid();
      enqueueSnackbar("Evals & Prompts run successfully", {
        variant: "success",
      });
    },
  });

  const { mutate: onMergeRows, isPending: isMergeRowsLoading } = useMutation({
    mutationFn: (selectedTargetDatasetId) => {
      const selectedIds = toggledNodes;
      const resolvedTargetDatasetId =
        selectedTargetDatasetId || targetDatasetId;

      return axios.post(endpoints.develop.mergeDatasetRows(dataset), {
        target_dataset_id: resolvedTargetDatasetId,
        row_ids: selectedIds,
        selected_all_rows: selectAll,
      });
    },
    onSuccess: (_data, selectedTargetDatasetId) => {
      const resolvedTargetDatasetId =
        selectedTargetDatasetId || targetDatasetId;

      handleClose();
      setTimeout(unCheckedHandler, 100);
      refreshGrid();
      enqueueSnackbar(
        <SnackbarWithAction
          message="Datapoint added to the chosen dataset"
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
  });

  const deletePending = isDeleting && deletingAction === confirmationRef.current;
  const duplicatePending = isDuplicateLoading && duplicatingAction === confirmationRef.current;

  const unCheckedHandler = () => {
    resetSelectedRows();
    gridApi.current?.deselectAll();
  };

  const selectedCount = useMemo(() => {
    const context = gridApi.current?.getGridOption("context");
    if (selectAll) {
      return (context?.totalRowCount ?? 0) - toggledNodes.length;
    } else {
      return toggledNodes.length;
    }
  }, [toggledNodes, selectAll, gridApi]);

  return (
    <StyledBox>
      <Box sx={{ display: "flex", alignItems: "center" }}>
        <StyledTypography>{selectedCount} Selected</StyledTypography>
      </Box>

      <Divider orientation="vertical" flexItem />
      <StyledButton
        variant="text"
        size="small"
        disabled={!RolePermission.DATASETS[PERMISSIONS.UPDATE][role]}
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

      <StyledButton
        variant="text"
        size="small"
        disabled={deletePending || duplicatePending || !RolePermission.DATASETS[PERMISSIONS.UPDATE][role]}
        startIcon={
          <Iconify icon="gg:duplicate" sx={{ color: "text.secondary" }} />
        }
        onClick={() => {
          trackEvent(Events.duplicateRowClicked);
          openRowAction("duplicate");
        }}
      >
        Duplicate
      </StyledButton>

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
      <StyledButton
        variant="text"
        size="small"
        disabled={deletePending || duplicatePending || !RolePermission.DATASETS[PERMISSIONS.DELETE][role]}
        startIcon={
          <SvgColor
            src={`/assets/icons/components/ic_delete.svg`}
            sx={{ width: "20px", height: "20px", color: "text.secondary" }}
          />
        }
        onClick={() => {
          trackEvent(Events.deleteRowClicked);
          openRowAction("delete");
        }}
      >
        Delete
      </StyledButton>
      <Divider orientation="vertical" flexItem />

      <StyledButton
        variant="text"
        size="small"
        onClick={() => unCheckedHandler()}
        sx={{
          padding: 0,
          minWidth: 0,
          marginRight: 2,
          color: "text.primary",
        }}
      >
        Cancel
      </StyledButton>

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

      {isDuplicate && (
        <CustomDialog
          title="Duplicate Dataset Rows"
          actionButton="Duplicate"
          open={isDuplicate}
          className="dialogButton"
          onClose={closeRowAction}
          loading={duplicatePending}
          onClickAction={() => {
            const action = confirmationRef.current;
            if (!action || action.type !== "duplicate") return;
            action.copies = Number(noOfCopies);
            onDuplicateDatasetRows(action);
          }}
        >
          <DuplicateRowAction setNoOfCopies={setNoOfCopies} />
        </CustomDialog>
      )}

      {isDelete && (
        <CustomDialog
          title={`Delete Row${selectedCount > 1 ? `s` : ""}`}
          color="error"
          actionButton="Delete"
          open={isDelete}
          preTitleIcon="solar:trash-bin-trash-bold"
          onClose={closeRowAction}
          loading={deletePending}
          onClickAction={() => {
            const action = confirmationRef.current;
            if (action?.type === "delete") onDeleteDatasetRow(action);
          }}
        >
          <DeleteRowAction selectedCount={selectedCount} />
        </CustomDialog>
      )}
    </StyledBox>
  );
};

DevelopDataSelectionActive.propTypes = {};

export default DevelopDataSelectionActive;
