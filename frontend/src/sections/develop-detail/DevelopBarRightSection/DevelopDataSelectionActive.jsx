import { Box, Button, Divider, Typography } from "@mui/material";
import React, { useMemo, useRef, useState } from "react";
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
import { useDevelopSelectedRowsStoreShallow } from "../states";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

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
  const { selectAll, toggledNodes, resetSelectedRows } =
    useDevelopSelectedRowsStoreShallow((s) => ({
      selectAll: s.selectAll,
      toggledNodes: s.toggledNodes,
      resetSelectedRows: s.resetSelectedRows,
    }));

  const { role } = useAuthContext();

  const { gridApi } = useDevelopDetailContext();

  const navigate = useNavigate();

  const handleClose = () => {
    setAnchorEl(null);
    setAnchorElSubmenu(null);
  };

  const { mutate: onDeleteDatasetRow, isPending: isDeleting } = useMutation({
    mutationFn: () => {
      const selectedIds = toggledNodes;

      return axios.delete(endpoints.develop.deleteDatasetRow(dataset), {
        data: { row_ids: selectedIds, selected_all_rows: selectAll },
      });
    },
    onSuccess: () => {
      const selectedIds = toggledNodes;
      trackEvent(Events.deleteRowSuccessful, {
        [PropertyName.deleteRow]: {
          selectedRow: selectedIds,
        },
      });
      setIsDelete(false);
      unCheckedHandler();
      refreshGrid();
      enqueueSnackbar("Rows deleted successfully", {
        variant: "success",
      });
    },
  });

  const { mutate: onDuplicateDatasetRows, isPending: isDuplicateLoading } =
    useMutation({
      mutationFn: () => {
        const selectedIds = toggledNodes;

        return axios.post(endpoints.develop.duplicateDatasetRows(dataset), {
          num_copies: Number(noOfCopies),
          row_ids: selectedIds,
          selected_all_rows: selectAll,
        });
      },
      onSuccess: () => {
        const selectedIds = toggledNodes;

        trackEvent(Events.duplicateRowSuccessful, {
          [PropertyName.duplicateRow]: {
            rowId: selectedIds[0],
            num_copies: Number(noOfCopies),
          },
        });
        setIsDuplicate(false);
        setTimeout(unCheckedHandler, 100);
        refreshGrid();
        enqueueSnackbar("Rows duplicated successfully", {
          variant: "success",
        });
      },
    });

  const { mutate: onCreateDatasetRows, isPending: isCreateDatasetLoading } =
    useMutation({
      mutationFn: () => {
        const selectedIds = toggledNodes;
        trackEvent(Events.addRowToNewDatasetSuccessful, {
          [PropertyName.rowToNewDataset]: {
            row_id: selectedIds[0],
            name: name,
          },
        });
        return axios.post(endpoints.develop.createDatasetRows(dataset), {
          row_ids: selectedIds,
          selected_all_rows: selectAll,
          name: name,
        });
      },
      onSuccess: ({ data }) => {
        handleClose();
        setTimeout(unCheckedHandler, 100);
        refreshGrid();
        enqueueSnackbar(
          <SnackbarWithAction
            message="Datapoint added to the created dataset"
            buttonText="View Dataset"
            onClick={() =>
              navigate(
                `/dashboard/develop/${data?.result?.newDatasetId}?tab=data`,
              )
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

  const { mutate: onMergeRows } = useMutation({
    mutationFn: () => {
      const selectedIds = toggledNodes;

      return axios.post(endpoints.develop.mergeDatasetRows(dataset), {
        target_dataset_id: targetDatasetId,
        row_ids: selectedIds,
        selected_all_rows: selectAll,
      });
    },
    onSuccess: () => {
      handleClose();
      setTimeout(unCheckedHandler, 100);
      refreshGrid();
      enqueueSnackbar(
        <SnackbarWithAction
          message="Datapoint added to the chosen dataset"
          buttonText="View Dataset"
          onClick={() =>
            navigate(`/dashboard/develop/${targetDatasetId}?tab=data`)
          }
        />,
        {
          variant: "success",
        },
      );
    },
  });

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
        disabled={!RolePermission.DATASETS[PERMISSIONS.UPDATE][role]}
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

      <Box>
        <DataMenuList
          handleClose={handleClose}
          setAnchorEl={setAnchorEl}
          anchorEl={anchorEl}
          setAnchorElSubmenu={setAnchorElSubmenu}
          anchorElSubmenu={anchorElSubmenu}
          setName={setName}
          loading={isCreateDatasetLoading}
          onCreateDatasetRows={onCreateDatasetRows}
          onMergeRows={onMergeRows}
          setTargetDatasetId={setTargetDatasetId}
        />
      </Box>
      <StyledButton
        variant="text"
        size="small"
        disabled={!RolePermission.DATASETS[PERMISSIONS.DELETE][role]}
        startIcon={
          <SvgColor
            src={`/assets/icons/components/ic_delete.svg`}
            sx={{ width: "20px", height: "20px", color: "text.secondary" }}
          />
        }
        onClick={() => {
          trackEvent(Events.deleteRowClicked);
          setIsDelete(!isDelete);
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
          onClose={() => setIsDuplicate((prev) => !prev)}
          loading={isDuplicateLoading}
          onClickAction={() => onDuplicateDatasetRows()}
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
          onClose={() => setIsDelete((prev) => !prev)}
          loading={isDeleting}
          onClickAction={() => onDeleteDatasetRow()}
        >
          <DeleteRowAction selectedCount={selectedCount} />
        </CustomDialog>
      )}
    </StyledBox>
  );
};

DevelopDataSelectionActive.propTypes = {};

export default DevelopDataSelectionActive;
