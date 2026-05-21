import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  IconButton,
  Typography,
} from "@mui/material";
import React, { useMemo, useState } from "react";
import { useParams } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import SavedEvalsSkeleton from "src/sections/common/EvaluationDrawer/SavedEvalsSkeleton";
import SavedEvalsList from "src/sections/common/EvaluationDrawer/SavedEvalsList";
import ConfirmRunEvaluations from "src/sections/common/EvaluationDrawer/ConfirmRunEvaluations";
import PropTypes from "prop-types";
import { ConfirmDialog } from "src/components/custom-dialog";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "src/components/snackbar";
import { useSimulationDetailContext } from "./context/SimulationDetailContext";
import { useSimulationExecutionsGridStoreShallow } from "./states";
import CustomTooltip from "src/components/tooltip";

const SimulationEvaluationPage = ({
  onClose,
  onSuccess = null,
  onAddEvaluation = null,
  onEditEvaluation = null,
}) => {
  const { id: promptTemplateId } = useParams();
  const queryClient = useQueryClient();
  const { toggledNodes, selectAll } = useSimulationExecutionsGridStoreShallow(
    (s) => ({
      toggledNodes: s.toggledNodes,
      selectAll: s.selectAll,
    }),
  );

  const {
    simulationId,
    simulation,
    isLoading: isPendingSimulation,
    refetchSimulation,
  } = useSimulationDetailContext();

  const [openConfirmDialog, setOpenConfirmDialog] = useState(false);
  const [openConfirmRunEvaluations, setOpenConfirmRunEvaluations] =
    useState(false);

  const { mutate: deleteEval, isPending: isDeleting } = useMutation({
    mutationFn: (evalId) =>
      axios.delete(endpoints.runTests.deleteEvals(simulation?.id, evalId)),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["simulation-detail", promptTemplateId, simulation?.id],
      });
      enqueueSnackbar("Eval deleted successfully", {
        variant: "success",
      });
      setOpenConfirmDialog(false);
      refetchSimulation?.();
    },
    onError: (error) => {
      enqueueSnackbar(error?.response?.data?.error || "Failed to delete eval", {
        variant: "error",
      });
    },
  });

  const { mutate: updateSimulation } = useMutation({
    mutationFn: (data) =>
      axios.patch(
        endpoints.promptSimulation.detail(promptTemplateId, simulation?.id),
        data,
      ),
    onSuccess: () => {
      refetchSimulation?.();
    },
  });

  const { mutate: runEvals, isPending: isRunningEvals } = useMutation({
    mutationFn: (data = {}) =>
      axios.post(endpoints.runTests.runEvals(simulation?.id), data),
    onSuccess: () => {
      setOpenConfirmRunEvaluations(false);
      onClose();
      onSuccess?.();
      enqueueSnackbar("Evaluations started successfully", {
        variant: "success",
      });
    },
    onError: (error) => {
      enqueueSnackbar(
        error?.response?.data?.error || "Failed to run evaluations",
        { variant: "error" },
      );
    },
  });

  const evals = useMemo(
    () =>
      (
        simulation?.simulate_eval_configs_detail ||
        simulation?.simulateEvalConfigsDetail ||
        simulation?.evals_detail ||
        simulation?.evalsDetail ||
        []
      ).map((evalItem) => ({
        ...evalItem,
        selected: true,
      })),
    [simulation],
  );

  const handleDeleteEval = (evalId) => {
    setOpenConfirmDialog(evalId);
  };

  const handleAddEvaluationClick = () => {
    onAddEvaluation?.();
  };

  const onToggleToolCallCheck = (e) => {
    const value = e.target.checked;
    updateSimulation({
      enable_tool_evaluation: value,
    });
  };

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        p: 2,
      }}
    >
      {/* ── Header ── */}
      <Box
        display="flex"
        justifyContent="space-between"
        alignItems="center"
        mb={0.5}
      >
        <Typography fontSize={16} fontWeight={600}>
          All Evaluations
        </Typography>
        <IconButton onClick={onClose} sx={{ p: 0.5, color: "text.primary" }}>
          <Iconify icon="mingcute:close-line" width={20} />
        </IconButton>
      </Box>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ fontSize: "12px", mb: 2 }}
      >
        Newly added evaluations will be applied in new test run
      </Typography>

      {/* ── List ── */}
      <Box
        sx={{
          flex: 1,
          overflow: "auto",
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
        }}
      >
        <ShowComponent condition={isPendingSimulation}>
          <SavedEvalsSkeleton />
        </ShowComponent>

        <ShowComponent condition={!isPendingSimulation && evals?.length === 0}>
          <Box
            display="flex"
            flexDirection="column"
            alignItems="center"
            justifyContent="center"
            py={8}
            border="1px dashed"
            borderColor="divider"
            borderRadius={1}
          >
            <Typography fontSize={15} fontWeight={600} mb={0.5}>
              No evaluations added
            </Typography>
            <Typography fontSize={12} color="text.disabled" mb={2}>
              Add evaluations to measure simulation quality
            </Typography>
            <Button
              size="small"
              variant="contained"
              startIcon={<Iconify icon="mdi:plus" width={16} />}
              onClick={handleAddEvaluationClick}
              sx={{
                textTransform: "none",
                fontSize: "12px",
                px: 2,
                fontWeight: 500,
              }}
            >
              Add Evaluation
            </Button>
          </Box>
        </ShowComponent>

        <ShowComponent condition={!isPendingSimulation && evals?.length > 0}>
          <SavedEvalsList
            evals={evals}
            onAddClick={handleAddEvaluationClick}
            onEditEvalClick={(evalItem) => onEditEvaluation?.(evalItem)}
            onDeleteEvalClick={(evalItem) => handleDeleteEval(evalItem.id)}
            showRun={false}
          />
        </ShowComponent>
      </Box>

      {/* ── Footer: Tool-call toggle + Cancel / Run ── */}
      <Box
        sx={{
          mt: 2,
          pt: 2,
          borderTop: "1px solid",
          borderColor: "divider",
          display: "flex",
          flexDirection: "column",
          gap: 1.5,
          flexShrink: 0,
        }}
      >
        <FormControlLabel
          sx={{ ml: 0, mr: 0 }}
          control={
            <Checkbox
              checked={
                simulation?.enable_tool_evaluation ??
                simulation?.enableToolEvaluation ??
                false
              }
              onChange={onToggleToolCallCheck}
              size="small"
              sx={{ p: 0.5 }}
            />
          }
          label={
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ fontSize: "12px" }}
            >
              Enable Tool Call Evaluation (tool calls during the chat will be
              evaluated)
            </Typography>
          }
          labelPlacement="end"
        />
        <Box sx={{ display: "flex", justifyContent: "flex-end", gap: 1 }}>
          <Button
            variant="outlined"
            size="small"
            onClick={onClose}
            sx={{
              textTransform: "none",
              fontSize: "12px",
              fontWeight: 500,
              borderRadius: "6px",
              px: 2,
            }}
          >
            Cancel
          </Button>
          <CustomTooltip
            arrow={true}
            show={!selectAll && !toggledNodes?.length}
            size={"small"}
            title={"Please select at least one Test Run to run Evaluation"}
          >
            <span>
              <LoadingButton
                variant="contained"
                color="primary"
                size="small"
                loading={isRunningEvals}
                disabled={
                  !evals ||
                  evals.length === 0 ||
                  (!selectAll && toggledNodes?.length === 0)
                }
                onClick={() => setOpenConfirmRunEvaluations(true)}
                startIcon={
                  <Iconify icon="mdi:play-circle-outline" width={16} />
                }
                sx={{
                  textTransform: "none",
                  fontSize: "12px",
                  fontWeight: 500,
                  borderRadius: "6px",
                  px: 2,
                }}
              >
                Run Evaluation
              </LoadingButton>
            </span>
          </CustomTooltip>
        </Box>
      </Box>

      <ConfirmRunEvaluations
        open={openConfirmRunEvaluations}
        onClose={() => setOpenConfirmRunEvaluations(false)}
        selectedUserEvalList={evals}
        loading={isRunningEvals}
        onConfirm={(evalsToRun) => {
          runEvals({
            eval_config_ids: evalsToRun.map((e) => e.id),
            select_all: selectAll,
            test_execution_ids: toggledNodes,
            enable_tool_evaluation:
              simulation?.enable_tool_evaluation ??
              simulation?.enableToolEvaluation,
          });
        }}
      />

      <ConfirmDialog
        open={Boolean(openConfirmDialog)}
        onClose={() => setOpenConfirmDialog(false)}
        title="Delete Evaluation"
        content="This will also remove all its results. This action cannot be undone."
        action={
          <LoadingButton
            variant="contained"
            color="error"
            size="small"
            sx={{ lineHeight: 1 }}
            loading={isDeleting}
            onClick={() => deleteEval(openConfirmDialog)}
          >
            Confirm
          </LoadingButton>
        }
      />
    </Box>
  );
};

SimulationEvaluationPage.propTypes = {
  onClose: PropTypes.func,
  onSuccess: PropTypes.func,
  onAddEvaluation: PropTypes.func,
  onEditEvaluation: PropTypes.func,
};

export default SimulationEvaluationPage;
