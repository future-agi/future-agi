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
import Iconify from "../../components/iconify";
import { ShowComponent } from "../../components/show";
import SavedEvalsSkeleton from "../common/EvaluationDrawer/SavedEvalsSkeleton";
import SavedEvalsList from "../common/EvaluationDrawer/SavedEvalsList";
import PropTypes from "prop-types";
import { ConfirmDialog } from "src/components/custom-dialog";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "src/components/snackbar";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";
import ConfirmRunEvaluations from "../common/EvaluationDrawer/ConfirmRunEvaluations";
import logger from "src/utils/logger";
import { useTestDetailContext } from "./context/TestDetailContext";
import { useTestRunsGridStoreShallow } from "./states";
import CustomTooltip from "src/components/tooltip";
import { useTestRunsSelectedCount } from "./common";
import UpdateKeysDialog from "../agents/AgentConfiguration/UpdateKeysDialog";
import { useUpdateTestRuns } from "src/api/tests/testRuns";
import { useSelectedAgentDefinitionStore } from "./TestRuns/states";
import { ComponentApiMapping } from "./TestRuns/common";
import { AGENT_TYPES } from "../agents/constants";
import useTestRunDetails from "src/hooks/useTestRunDetails";
import TestOnboardingFocusPanel from "./TestOnboardingFocusPanel";
import { TEST_ONBOARDING_MODES } from "./testOnboardingModes";

const TestEvaluationPage = ({
  onClose,
  executionIds = null,
  onboardingMode = null,
  onboardingAddLabel = null,
  onboardingCopy = null,
  onboardingCurrentStep = null,
  onboardingEyebrow = null,
  onboardingRunLabel = null,
  onboardingSecondaryAddLabel = null,
  onboardingSteps = null,
  tourAnchor = null,
  onSuccessOfAdditionOfEvals = null,
  onAddEvaluation = null,
  onEditEvaluation = null,
}) => {
  const { role } = useAuthContext();
  const { testId } = useParams();
  const [openConfirmRunEvaluations, setOpenConfirmRunEvaluations] =
    useState(false);
  const { setSelectedAgentDefinitionVersion } =
    useSelectedAgentDefinitionStore();

  const { toggledNodes, selectAll, setToggledNodes, setSelectAll } =
    useTestRunsGridStoreShallow((s) => ({
      toggledNodes: s.toggledNodes,
      selectAll: s.selectAll,
      setToggledNodes: s.setToggledNodes,
      setSelectAll: s.setSelectAll,
    }));

  const { refreshTestRunGrid } = useTestDetailContext();

  const selectedCount = useTestRunsSelectedCount();

  const [openUpdateKeysDialog, setOpenUpdateKeysDialog] = useState(false);

  const { data: testData, loading } = useTestRunDetails(testId);
  const isPendingTestDetail = loading?.isPending;
  const agentType =
    testData?.agent_definition_detail?.agent_type ??
    testData?.agentDefinitionDetail?.agentType ??
    AGENT_TYPES.CHAT;
  const queryClient = useQueryClient();
  const canEditEvals =
    RolePermission.EVALS[PERMISSIONS.EDIT_CREATE_DELETE_EVALS][role];

  const { mutate: updateTestRuns } = useUpdateTestRuns(testId, {
    onMutate: async (data) => {
      const previousTestDetailData = queryClient.getQueryData([
        "test-runs-detail",
        testId,
      ]);

      queryClient.setQueryData(["test-runs-detail", testId], (old) => {
        logger.debug("setQueryData", { old, data });
        return {
          ...old,
          data: {
            ...old?.data,
            enableToolEvaluation: data?.enable_tool_evaluation,
          },
        };
      });

      return { previousTestDetailData };
    },
    onError: (error, _, context) => {
      queryClient.setQueryData(
        ["test-runs-detail", testId],
        context.previousTodos,
      );
      if (
        error?.result?.errorCode === ComponentApiMapping.ToolEvaluationApiKey
      ) {
        setOpenUpdateKeysDialog(true);
      } else {
        enqueueSnackbar(`${error?.result}`, {
          variant: "error",
        });
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({
        queryKey: ["test-runs-detail", testId],
      });
    },
  });

  const { mutate: deleteEval, isPending } = useMutation({
    mutationFn: (evalId) =>
      axios.delete(endpoints.runTests.deleteEvals(testId, evalId)),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["test-runs-detail", testId],
      });
      enqueueSnackbar("Eval deleted successfully", {
        variant: "success",
      });
      setOpenConfirmDialog(false);
    },
  });

  const clearSelection = () => {
    setToggledNodes([]);
    setSelectAll(false);
  };

  const { mutate: runEvals, isPending: isRunningEvals } = useMutation({
    mutationFn: (data) => axios.post(endpoints.runTests.runEvals(testId), data),
    onSuccess: () => {
      setOpenConfirmRunEvaluations(false);
      onClose();
      if (executionIds) {
        onSuccessOfAdditionOfEvals?.();
      } else {
        clearSelection();
        refreshTestRunGrid?.();
      }

      enqueueSnackbar("Evals run successfully", {
        variant: "success",
      });
    },
  });

  const evals = useMemo(
    () =>
      (
        testData?.simulate_eval_configs_detail ??
        testData?.simulateEvalConfigsDetail ??
        testData?.evals_detail ??
        testData?.evalsDetail ??
        []
      ).map((evalItem) => ({
        ...evalItem,
        selected: true,
      })),
    [testData],
  );

  const handleDeleteEval = (evalId) => {
    setOpenConfirmDialog(evalId);
  };

  const [openConfirmDialog, setOpenConfirmDialog] = useState(false);
  const isEvalRouteMode =
    onboardingMode === TEST_ONBOARDING_MODES.CREATE_EVAL ||
    onboardingMode === TEST_ONBOARDING_MODES.SAVE_EVAL;
  const hasEvals = (evals?.length ?? 0) > 0;
  const hasSelectedRuns = executionIds
    ? executionIds?.length > 0
    : selectedCount > 0;
  const evalRouteCopy =
    onboardingCopy ||
    (onboardingMode === TEST_ONBOARDING_MODES.CREATE_EVAL
      ? {
          title: "Create eval coverage",
          description:
            "Choose or create one evaluation and map it to this test so future runs produce a quality signal.",
        }
      : {
          title: "Save the first evaluation",
          description:
            "Add one evaluation to this test, then run it against selected rows to confirm the signal works.",
        });

  const handleAddEvaluationClick = () => {
    if (testId) {
      trackEvent(Events.runTestAddEvalClicked, {
        [PropertyName.id]: testId,
      });
    }
    onAddEvaluation?.();
  };

  const evalRoutePrimaryAction = hasEvals
    ? {
        label: onboardingRunLabel || "Run Evaluation",
        onClick: () => setOpenConfirmRunEvaluations(true),
        disabled: !canEditEvals || !hasSelectedRuns,
      }
    : {
        label: onboardingAddLabel || "Add Evaluation",
        onClick: handleAddEvaluationClick,
        disabled: !canEditEvals,
      };
  const evalRouteSecondaryAction = hasEvals
    ? {
        label: onboardingSecondaryAddLabel || "Add another evaluation",
        onClick: handleAddEvaluationClick,
        disabled: !canEditEvals,
      }
    : null;
  const evalRouteSteps = onboardingSteps || [
    { label: "Test", complete: Boolean(testId) },
    { label: "Evaluation", complete: hasEvals },
    { label: "Run", complete: hasEvals && hasSelectedRuns },
  ];

  const onToggleToolCallCheck = (e) => {
    const value = e.target.checked;
    if (agentType === AGENT_TYPES.VOICE) {
      const agentVersionDetails =
        testData?.agent_version ?? testData?.agentVersion;
      const configurationSnapshot =
        agentVersionDetails?.configuration_snapshot ??
        agentVersionDetails?.configurationSnapshot;
      if (!configurationSnapshot) {
        enqueueSnackbar("There was error getting agent version details", {
          variant: "error",
        });
        return;
      }
      const vapiApiKey =
        configurationSnapshot?.api_key ?? configurationSnapshot?.apiKey;
      const vapiAssistantId =
        configurationSnapshot?.assistant_id ??
        configurationSnapshot?.assistantId;
      if ((!vapiApiKey || !vapiAssistantId) && value) {
        setOpenUpdateKeysDialog(true);
        return;
      }
    }
    //@ts-ignore
    updateTestRuns({
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
        <IconButton
          hidden={isEvalRouteMode}
          onClick={onClose}
          sx={{
            display: isEvalRouteMode ? "none" : "inline-flex",
            p: 0.5,
            color: "text.primary",
          }}
        >
          <Iconify icon="mingcute:close-line" width={20} />
        </IconButton>
      </Box>
      <ShowComponent condition={selectedCount === 0}>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontSize: "12px", mb: 2 }}
        >
          Newly added evaluations will be applied in new test run
        </Typography>
      </ShowComponent>
      <Box sx={{ mb: 2 }} />

      <TestOnboardingFocusPanel
        currentStep={onboardingCurrentStep || "Evaluation"}
        description={evalRouteCopy.description}
        eyebrow={onboardingEyebrow || undefined}
        hidden={!isEvalRouteMode}
        blocker={hasEvals && !hasSelectedRuns ? "Select a run first" : null}
        primaryAction={evalRoutePrimaryAction}
        secondaryAction={evalRouteSecondaryAction}
        singleActionFocus={isEvalRouteMode}
        steps={evalRouteSteps}
        title={evalRouteCopy.title}
        tourAnchor={tourAnchor}
      />

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
        <ShowComponent condition={isPendingTestDetail}>
          <SavedEvalsSkeleton />
        </ShowComponent>
        <ShowComponent
          condition={!isPendingTestDetail && (evals?.length ?? 0) === 0}
        >
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
              Add evaluations to measure test quality
            </Typography>
            <Button
              size="small"
              variant="contained"
              startIcon={<Iconify icon="mdi:plus" width={16} />}
              onClick={handleAddEvaluationClick}
              disabled={!canEditEvals}
              hidden={isEvalRouteMode}
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
        <ShowComponent
          condition={!isPendingTestDetail && (evals?.length ?? 0) > 0}
        >
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
        hidden={isEvalRouteMode}
        sx={{
          mt: 2,
          pt: 2,
          borderTop: "1px solid",
          borderColor: "divider",
          display: isEvalRouteMode ? "none" : "flex",
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
                testData?.enable_tool_evaluation ??
                testData?.enableToolEvaluation ??
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
              Enable Tool Call Evaluation (tool calls during the calls will be
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
            show={
              executionIds ? executionIds?.length === 0 : selectedCount === 0
            }
            title="Please select at least one Test Run from grid to run evaluation"
            arrow
            placement="top"
            size="small"
          >
            <span>
              <Button
                variant="contained"
                color="primary"
                size="small"
                onClick={() => setOpenConfirmRunEvaluations(true)}
                disabled={
                  (executionIds
                    ? executionIds?.length === 0
                    : selectedCount === 0) || !canEditEvals
                }
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
              </Button>
            </span>
          </CustomTooltip>
        </Box>
      </Box>
      <ConfirmRunEvaluations
        open={openConfirmRunEvaluations}
        onClose={() => setOpenConfirmRunEvaluations(false)}
        onConfirm={(evalsToRun) => {
          logger.info("evalsToRun", executionIds ? executionIds : toggledNodes);
          //@ts-ignore
          runEvals({
            eval_config_ids: evalsToRun.map((e) => e.id),
            test_execution_ids: executionIds ? executionIds : toggledNodes,
            select_all: selectAll,
            enable_tool_evaluation:
              testData?.enable_tool_evaluation ??
              testData?.enableToolEvaluation,
          });
        }}
        selectedUserEvalList={evals}
        loading={isRunningEvals}
      />
      <ConfirmDialog
        open={openConfirmDialog}
        onClose={() => setOpenConfirmDialog(false)}
        onConfirm={() => {}}
        title="Delete Evaluation"
        content="This will also remove all its results. This action cannot be undone."
        action={
          <LoadingButton
            variant="contained"
            color="error"
            size="small"
            sx={{ lineHeight: 1 }}
            loading={isPending}
            onClick={() => deleteEval(openConfirmDialog)}
          >
            Confirm
          </LoadingButton>
        }
      />
      <UpdateKeysDialog
        open={openUpdateKeysDialog}
        onComplete={(createVersionResponse) => {
          if (createVersionResponse) {
            const version = createVersionResponse?.data?.version;
            setSelectedAgentDefinitionVersion({
              value: version?.id,
              label: version?.versionName,
            });
            //@ts-ignore
            updateTestRuns({
              enable_tool_evaluation: true,
              version: version?.id,
            });
          }
          setOpenUpdateKeysDialog(false);
        }}
        onClose={() => setOpenUpdateKeysDialog(false)}
        agentDetails={testData?.agent_version ?? testData?.agentVersion}
        agentDefinitionId={
          testData?.agent_definition ?? testData?.agentDefinition
        }
      />
    </Box>
  );
};

TestEvaluationPage.propTypes = {
  onClose: PropTypes.func.isRequired,
  executionIds: PropTypes.arrayOf(PropTypes.string),
  onboardingMode: PropTypes.string,
  onboardingAddLabel: PropTypes.string,
  onboardingCopy: PropTypes.shape({
    description: PropTypes.string.isRequired,
    title: PropTypes.string.isRequired,
  }),
  onboardingCurrentStep: PropTypes.string,
  onboardingEyebrow: PropTypes.string,
  onboardingRunLabel: PropTypes.string,
  onboardingSecondaryAddLabel: PropTypes.string,
  onboardingSteps: PropTypes.arrayOf(
    PropTypes.shape({
      complete: PropTypes.bool,
      label: PropTypes.string.isRequired,
    }),
  ),
  tourAnchor: PropTypes.string,
  onSuccessOfAdditionOfEvals: PropTypes.func,
  onAddEvaluation: PropTypes.func,
  onEditEvaluation: PropTypes.func,
};

TestEvaluationPage.defaultProps = {
  executionIds: null,
  onboardingMode: null,
  onboardingAddLabel: null,
  onboardingCopy: null,
  onboardingCurrentStep: null,
  onboardingEyebrow: null,
  onboardingRunLabel: null,
  onboardingSecondaryAddLabel: null,
  onboardingSteps: null,
  tourAnchor: null,
  onSuccessOfAdditionOfEvals: null,
  onAddEvaluation: null,
  onEditEvaluation: null,
};

export default TestEvaluationPage;
