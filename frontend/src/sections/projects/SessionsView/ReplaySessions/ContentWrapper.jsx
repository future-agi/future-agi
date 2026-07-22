import React, { lazy, Suspense, useMemo, useEffect } from "react";
import PropTypes from "prop-types";
import StepsTracker from "../../../../components/StepsTracker/StepsTracker";
import { Box, Button, Stack } from "@mui/material";
import { replaySessionsSteps, REPLAY_TYPES } from "./constants";
import {
  resetReplaySessionsStore,
  resetSessionsGridStore,
  useReplaySessionsStoreShallow,
} from "./store";
import { useMutation } from "@tanstack/react-query";
const CreateScenariosForm = lazy(() => import("./CreateScenariosForm"));
const CreatedScenarios = lazy(() => import("./CreatedScenarios"));
// const AddToScenarioGroups = lazy(() => import("./AddToScenarioGroups"));
import { ShowComponent } from "src/components/show";
import { LoadingButton } from "@mui/lab";
import Loading from "./Loading";
import { FormProvider, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  buildEvaluationMappingFormSchema,
  getReplaySessionsFormDefaultValues,
  getRunSimulationPayload,
  replaySessionsFormSchema,
} from "./common";
import { useNavigate, useParams } from "react-router";
import { useReplayDrawerContext } from "./replayDrawerContent";
import { useReplayConfiguration } from "./useReplayConfiguration";
import { SCENARIO_STATUS } from "src/pages/dashboard/scenarios/common";
import { useEvaluationKeys } from "./useEvaluationKeys";
import { useStepDirty } from "./useStepDirty";
import CustomTooltip from "src/components/tooltip";
const MapVariables = lazy(() => import("./MapVariables"));

export default function ContentWrapper({
  scenarioDetail,
  isScenarioDetailLoading: _isScenarioDetailLoading,
  createdReplay,
}) {
  const {
    currentStep,
    validatedSteps,
    setCurrentStep,
    setValidatedStep,
    expandView,
    setExpandView,
    replayType,
    selectedGroup,
    setIsConfirmationModalOpen,
    setOpenReplaySessionDrawer,
    setCreatedScenario,
    createdScenario,
  } = useReplaySessionsStoreShallow((s) => {
    return {
      currentStep: s.currentStep,
      validatedSteps: s.validatedSteps,
      setCurrentStep: s.setCurrentStep,
      setValidatedStep: s.setValidatedStep,
      expandView: s.expandView,
      setExpandView: s.setExpandView,
      replayType: s.replayType,
      selectedGroup: s.selectedGroup,
      setIsConfirmationModalOpen: s.setIsConfirmationModalOpen,
      setOpenReplaySessionDrawer: s.setOpenReplaySessionDrawer,
      setCreatedScenario: s.setCreatedScenario,
      createdScenario: s.createdScenario,
    };
  });

  const { gridApi } = useReplayDrawerContext();
  const config = useReplayConfiguration();
  const { observeId } = useParams();

  const {
    projectEvals,
    requiredKeys,
    optionalKeys,
    filteredRequiredKeys,
    isLoading: isProjectEvalsLoading,
  } = useEvaluationKeys(observeId, scenarioDetail, currentStep);

  // Dynamic schema based on current step
  const dynamicSchema = useMemo(() => {
    if (currentStep === 2) {
      return buildEvaluationMappingFormSchema(filteredRequiredKeys);
    }
    return replaySessionsFormSchema;
  }, [currentStep, filteredRequiredKeys]);

  // Merge default values from both steps
  const mergedDefaultValues = useMemo(() => {
    const step0Defaults = getReplaySessionsFormDefaultValues(createdReplay);
    const step2Defaults = {
      model: "",
      config: {
        mapping: {},
        reasonColumn: true,
      },
      errorLocalizer: false,
      kbId: "",
      projectEvals: projectEvals,
    };
    return {
      ...step2Defaults,
      ...step0Defaults,
    };
  }, [createdReplay, projectEvals]);

  const methods = useForm({
    defaultValues: mergedDefaultValues,
    resolver: zodResolver(dynamicSchema),
    mode: "onChange",
  });

  const {
    reset,
    formState: { isValid, dirtyFields },
    getValues,
  } = methods;

  // Use custom hook to check if step 0 is dirty
  const isStepDirty = useStepDirty(dirtyFields);
  const isStep0Dirty = isStepDirty(0);

  useEffect(() => {
    if (!isProjectEvalsLoading && projectEvals !== undefined) {
      const currentValues = getValues();
      const currentProjectEvals = currentValues?.projectEvals;

      const hasChanged =
        !currentProjectEvals ||
        currentProjectEvals.length !== projectEvals.length ||
        JSON.stringify(currentProjectEvals) !== JSON.stringify(projectEvals);

      if (hasChanged) {
        reset(
          {
            ...currentValues,
            projectEvals: projectEvals,
          },
          {
            keepDirty: false,
          },
        );
      }
    }
  }, [projectEvals, isProjectEvalsLoading, reset, getValues]);

  const navigate = useNavigate();

  const { mutate: createScenarios, isPending: isCreatingScenarios } =
    useMutation({
      mutationFn: (data) => {
        return config?.apiEndpoints?.createScenarios?.(data);
      },
      onSuccess: (data) => {
        // reset form data with submitted values
        reset(getValues(), {
          keepDirty: false,
        });
        // Call custom handler if provided
        setCreatedScenario(data?.data?.result);
        if (config?.handlers?.onScenarioCreated) {
          config.handlers.onScenarioCreated(data);
        }
        setValidatedStep(0, true);
        setCurrentStep(1);
      },
    });

  const handleCreateScenarios = () => {
    // Check if form is dirty (only step 0 fields)
    const shouldCallAPI =
      isStep0Dirty ||
      !createdScenario ||
      (createdScenario && scenarioDetail?.status === SCENARIO_STATUS.FAILED);

    if (shouldCallAPI) {
      methods.handleSubmit((transformedData) => {
        createScenarios({
          ...transformedData,
          graph: null,
          generate_graph: true,
          replay_type: config?.module,
        });
      })();
    } else {
      setValidatedStep(0, true);
      setCurrentStep(1);
    }
  };

  const { mutate: runSimulation, isPending: isRunningSimulation } = useMutation(
    {
      mutationFn: (data) => {
        return config?.apiEndpoints?.runSimulation?.(data);
      },
      onSuccess: (data) => {
        setValidatedStep(3, true);
        resetReplaySessionsStore();
        navigate(`/dashboard/simulate/test/${data?.data?.id}/runs`);
        resetSessionsGridStore();
        gridApi?.deselectAll();
      },
    },
  );

  const handleMapVariablesSubmit = () => {
    const payload = getRunSimulationPayload(
      {
        ...getValues(),
        createdScenario,
        replaySessionId: createdReplay?.id,
      },
      config?.projectDetail,
      requiredKeys,
      optionalKeys,
      projectEvals,
    );
    runSimulation(payload);
  };

  const handleNextStep = () => {
    if (currentStep === 2) {
      // Trigger form submission
      handleMapVariablesSubmit();
      return;
    }
    setCurrentStep(2);
    setValidatedStep(1, true);
    setExpandView(false);
    return;
  };

  const handleBackCancel = () => {
    if (currentStep === 0) {
      if (isStep0Dirty || createdScenario) {
        setIsConfirmationModalOpen(true);
      } else {
        setOpenReplaySessionDrawer(config?.module, false);
        resetReplaySessionsStore();
      }
    } else if (currentStep === 1) {
      if (expandView) {
        setExpandView(false);
      } else {
        setCurrentStep(0);
      }
    } else if (
      currentStep === 2 &&
      replayType === REPLAY_TYPES.EXISTING_GROUP
    ) {
      setCurrentStep(1);
    } else {
      setExpandView(false);
      setCurrentStep(currentStep - 1 || 0);
    }
  };

  const canProceedToNextStep = useMemo(() => {
    if (
      currentStep === 1 &&
      scenarioDetail?.status !== SCENARIO_STATUS.COMPLETED
    ) {
      return false;
    }
    if (currentStep === 2 && replayType === REPLAY_TYPES.EXISTING_GROUP) {
      return Boolean(selectedGroup);
    }
    if (currentStep === 2 && !isValid) {
      return false;
    }
    return true;
  }, [currentStep, replayType, scenarioDetail, selectedGroup, isValid]);

  return (
    <FormProvider {...methods}>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          height: "100%",
          overflow: "hidden",
        }}
      >
        <Box sx={{ flexShrink: 0 }}>
          <StepsTracker
            steps={replaySessionsSteps[replayType]}
            currentStep={currentStep}
            validatedSteps={validatedSteps}
            setCurrentStep={setCurrentStep}
            setValidatedStep={setValidatedStep}
          />
        </Box>
        <Box
          sx={{
            flex: 1,
            my: 2,
            overflowY: "auto",
            overflowX: "hidden",
            minHeight: 0,
          }}
        >
          <ShowComponent condition={currentStep === 0}>
            <Suspense fallback={<Loading />}>
              <CreateScenariosForm />
            </Suspense>
          </ShowComponent>
          <ShowComponent condition={currentStep === 2}>
            <Suspense fallback={<Loading />}>
              <MapVariables scenarioDetail={scenarioDetail} />
            </Suspense>
          </ShowComponent>
          <ShowComponent condition={currentStep === 1}>
            <Suspense fallback={<Loading />}>
              <CreatedScenarios
                scenarioDetail={scenarioDetail}
                testDetail={createdScenario?.runTest}
              />
            </Suspense>
          </ShowComponent>
          {/* <ShowComponent
            condition={
              currentStep === 2 && replayType === REPLAY_TYPES.EXISTING_GROUP
            }
          >
            <Suspense fallback={<Loading />}>
              <AddToScenarioGroups />
            </Suspense>
          </ShowComponent> */}
        </Box>
        <Stack
          sx={{
            flexShrink: 0, // Prevent footer from shrinking
            backgroundColor: "background.paper",
            mt: "auto",
          }}
          direction={"row"}
          alignItems={"center"}
          gap={2}
        >
          <Button onClick={handleBackCancel} variant="outlined" fullWidth>
            {currentStep === 0
              ? config?.textContent?.cancelButton || "Cancel"
              : config?.textContent?.backButton || "Back"}
          </Button>
          <ShowComponent condition={currentStep === 0}>
            <LoadingButton
              variant="contained"
              color="primary"
              fullWidth
              onClick={handleCreateScenarios}
              loading={isCreatingScenarios}
              disabled={!isValid}
            >
              {config?.textContent?.createScenariosButton || "Create Scenarios"}
            </LoadingButton>
          </ShowComponent>
          <ShowComponent condition={currentStep === 1}>
            <CustomTooltip
              show={scenarioDetail?.status === SCENARIO_STATUS.FAILED}
              title="Scenario creation failed. Please review the scenario details and try again."
              type="black"
              size="small"
            >
              <Box
                sx={{
                  width: "100%",
                }}
              >
                <Button
                  variant="contained"
                  color="primary"
                  fullWidth
                  onClick={handleNextStep}
                  disabled={!canProceedToNextStep}
                >
                  {config?.textContent?.nextButton || "Next"}
                </Button>
              </Box>
            </CustomTooltip>
          </ShowComponent>
          {/* <ShowComponent
            condition={
              currentStep === 2 && replayType === REPLAY_TYPES.EXISTING_GROUP
            }
          >
            <LoadingButton
              variant="outlined"
              color="primary"
              fullWidth
              disabled={!canProceedToNextStep}
              onClick={handleAddToScenarioGroup}
              loading={isAddingToScenarioGroup}
            >
              {config?.textContent?.addToScenarioGroupButton ||
                "Add to scenario group"}
            </LoadingButton>
          </ShowComponent> */}
          <ShowComponent condition={currentStep === 2}>
            <LoadingButton
              variant="contained"
              color="primary"
              fullWidth
              onClick={handleNextStep}
              loading={isRunningSimulation}
              disabled={!canProceedToNextStep}
            >
              {config?.textContent?.runSimulationButton || "Run Simulation"}
            </LoadingButton>
          </ShowComponent>
        </Stack>
      </Box>
    </FormProvider>
  );
}

ContentWrapper.propTypes = {
  scenarioDetail: PropTypes.object,
  isScenarioDetailLoading: PropTypes.bool,
  createdReplay: PropTypes.object,
};
