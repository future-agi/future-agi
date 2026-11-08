import { Box, Button, Typography, useTheme } from "@mui/material";
import React, { useCallback, useState } from "react";
import SvgColor from "src/components/svg-color";
import {
  useTestEvaluationStoreShallow,
  useTestRunsGridStoreShallow,
} from "../states";
import { useTestDetailContext } from "../context/TestDetailContext";
import { useTestRunsSelectedCount } from "../common";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import { enqueueSnackbar } from "notistack";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";

const ACTION_TYPES = {
  RERUN: "rerun",
  DELETE: "delete",
};

const TestRunsSelection = () => {
  const { toggledNodes, selectAll, setToggledNodes, setSelectAll } =
    useTestRunsGridStoreShallow((s) => ({
      toggledNodes: s.toggledNodes,
      selectAll: s.selectAll,
      setToggledNodes: s.setToggledNodes,
      setSelectAll: s.setSelectAll,
    }));
  const [actionType, setActionType] = useState();
  const { setOpenTestEvaluation } = useTestEvaluationStoreShallow((s) => ({
    setOpenTestEvaluation: s.setOpenTestEvaluation,
  }));

  const { testId } = useParams();
  const theme = useTheme();

  const { refreshTestRunGrid } = useTestDetailContext();

  const selectedCount = useTestRunsSelectedCount();

  const clearSelection = useCallback(() => {
    setToggledNodes([]);
    setSelectAll(false);
  }, [setToggledNodes, setSelectAll]);

  const rerunExecutions = useMutation({
    mutationFn: () =>
      axios.post(endpoints.runTests.rerunSimulation(testId), {
        test_execution_ids: toggledNodes,
        select_all: selectAll,
        rerun_type: "call_and_eval",
      }),
    onSuccess: () => {
      enqueueSnackbar("Simulation re-run initiated successfully", {
        variant: "success",
      });
      clearSelection();
      refreshTestRunGrid?.();
    },
  });
  const deleteExecutions = useMutation({
    mutationFn: () =>
      axios.post(endpoints.runTests.deleteSimulation(testId), {
        test_execution_ids: toggledNodes,
        select_all: selectAll,
      }),
    onSuccess: () => {
      enqueueSnackbar("Simulations deleted successfully", {
        variant: "success",
      });
      clearSelection();
      refreshTestRunGrid?.();
    },
  });

  const handleSubmit = () => {
    if (actionType === ACTION_TYPES.RERUN) {
      rerunExecutions.mutate();
    } else if (actionType === ACTION_TYPES.DELETE) {
      deleteExecutions.mutate();
    }
  };
  return (
    <Box
      sx={{
        paddingY: 0.5,
        paddingX: 2,
        display: "flex",
        alignItems: "center",
        gap: 1,
      }}
    >
      <Button
        onClick={() => setActionType(ACTION_TYPES.RERUN)}
        startIcon={<SvgColor src="/assets/icons/navbar/ic_get_started.svg" />}
        size="small"
        variant="outlined"
      >
        <Typography
          typography={"s1"}
          fontWeight={"fontWeightMedium"}
        >{`Re-Run Simulation (${selectedCount})`}</Typography>
      </Button>
      <Button
        size="small"
        variant="outlined"
        sx={{
          ...theme.typography.s2_1,
        }}
        startIcon={<SvgColor src="/assets/icons/ic_completed.svg" />}
        onClick={() => setOpenTestEvaluation(true)}
      >
        <Typography
          typography={"s1"}
          fontWeight={"fontWeightMedium"}
        >{`Run Evals (${selectedCount})`}</Typography>
      </Button>
      <Button
        size="small"
        variant="outlined"
        sx={{
          ...theme.typography.s2_1,
        }}
        startIcon={<SvgColor src="/assets/icons/ic_delete.svg" />}
        onClick={() => setActionType(ACTION_TYPES.DELETE)}
      >
        <Typography
          typography={"s1"}
          fontWeight={"fontWeightMedium"}
        >{`Delete (${selectedCount})`}</Typography>
      </Button>
      <Button
        size="small"
        variant="outlined"
        sx={{ ...theme.typography.s1 }}
        onClick={() => {
          clearSelection();
        }}
      >
        <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
          Cancel
        </Typography>
      </Button>
      <ModalWrapper
        open={!!actionType}
        onClose={() => setActionType(null)}
        title={
          actionType === ACTION_TYPES.RERUN
            ? `Re-Run Simulation${selectedCount !== 1 ? "s" : ""}`
            : `Delete Simulation${selectedCount !== 1 ? "s" : ""}`
        }
        subTitle={
          actionType === ACTION_TYPES.RERUN
            ? `Re-running these simulations will execute them again with the same configuration. Are you sure you want to proceed?`
            : `Deleting these runs will remove all simulation results and call details permanently. Are you sure you want to proceed?`
        }
        isValid={true}
        modalWidth="560px"
        onSubmit={handleSubmit}
        onCancelBtn={() => setActionType(null)}
        actionBtnTitle={actionType === ACTION_TYPES.RERUN ? "Re-Run" : "Delete"}
        actionBtnSx={
          actionType === ACTION_TYPES.DELETE
            ? {
                bgcolor: "status.danger.solid",
                color: "common.white",
                "&:hover": { bgcolor: "status.danger.solid" },
              }
            : {}
        }
        isLoading={
          actionType === ACTION_TYPES.RERUN
            ? rerunExecutions.isPending
            : deleteExecutions.isPending
        }
      />
    </Box>
  );
};

export default TestRunsSelection;
