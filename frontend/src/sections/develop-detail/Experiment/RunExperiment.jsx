import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Divider,
  Drawer,
  IconButton,
  Link,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState, useEffect, useRef, useMemo } from "react";
import { useForm, useFormState, useWatch } from "react-hook-form";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import Iconify from "src/components/iconify";
import HelperText from "../Common/HelperText";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  getDefaultPromptConfigByModelType,
  getExperimentDefaultValue,
  getNewExperimentValidationSchema,
} from "./common";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useGetExperimentDetails } from "src/hooks/useGetExperimentDetails";
import { enqueueSnackbar } from "src/components/snackbar";
import { useNavigate, useParams } from "react-router";
import { ConfirmDialog } from "src/components/custom-dialog";
import { invalidateDatasetListCache } from "src/utils/cacheUtils";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import { useRunExperimentStoreShallow } from "../states";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";
import {
  useDatasetColumnConfig,
  useDatasetDerivedVariables,
  useGetJsonColumnSchema,
} from "src/api/develop/develop-detail";
import RunExperimentFormSkeleton from "./RunExperimentFormSkeleton";
import { useColumnInfo } from "../../../api/develop/develop-detail";
import { ShowComponent } from "src/components/show";
import StepperForExperiment from "./StepperForExperiment";
import ConfigureStepExperimentCreation from "./StepperComponentExperiment/ConfigureStepExperimentCreation";
import BasicStepExperimentCreation from "./StepperComponentExperiment/BasicStepExperimentCreation";
import EvaluationStepExperimentCreation from "./StepperComponentExperiment/EvaluationStepExperimentCreation";
import { MODEL_TYPES } from "../RunPrompt/common";
import { useSearchParams } from "react-router-dom";
import { useDebounce } from "src/hooks/use-debounce";
import { useExperimentDerivedVariables } from "../../../api/experiment/use-experiment-derived-variables";
import { useGetExperimentJSONSchema } from "../../../api/experiment/use-get-exp-jsonschema";
const getDefaultValue = () => {
  return {
    columnId: null,
    name: "",
    outputFormat: "string",
    experimentType: "llm",
    promptConfig: [],
    userEvalMetrics: [],
  };
};

const RunExperimentForm = ({
  allColumns,
  jsonSchemas = {},
  derivedVariables = {},
  onClose,
  control,
  handleSubmit,
  setFormIsDirty,
  onSuccessfulSubmit,
  onSubmitSuccess,
  setValue,
  resetField: _resetField,
  getValues,
  errors,
  reset,
  refreshGrid,
  clearErrors,
  unregister,
  watch,
  selectedExperiment,
  setError,
  trigger,
}) => {
  const formState = useFormState({
    control,
  });

  const { dataset: datasetParam } = useParams();
  const [searchParam] = useSearchParams();
  const dataset = datasetParam || searchParam.get("datasetId");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const theme = useTheme();

  const { mutate, isPending } = useMutation({
    mutationFn: async (d) => {
      if (selectedExperiment) {
        await axios.put(
          endpoints.develop.experiment.update(selectedExperiment),
          { ...d },
        );
        // Always trigger a full re-run so the experiment executes even when
        // no configuration diff was detected by the PUT endpoint.
        return axios.post(endpoints.develop.experiment.rerun, {
          experiment_ids: [selectedExperiment],
        });
      } else {
        return axios.post(endpoints.develop.experiment.create(), d);
      }
    },
    onSuccess: () => {
      if (selectedExperiment) {
        enqueueSnackbar("Experiment run started successfully", {
          variant: "success",
        });
      } else {
        enqueueSnackbar("Experiment created successfully", {
          variant: "success",
        });
      }

      // Invalidate dataset list cache to ensure dropdown refreshes
      invalidateDatasetListCache(queryClient);
      if (!selectedExperiment) {
        refreshGrid(null, true);

        navigate(`/dashboard/develop/${dataset}?tab=experiments`);
      }

      reset();
      onSubmitSuccess ? onSubmitSuccess() : onClose();
      onSuccessfulSubmit?.();
    },
    onError: (error) => {
      const errorMessage =
        error?.response?.data?.error ||
        error?.response?.data?.message ||
        "Failed to run experiment. Please try again.";
      enqueueSnackbar(errorMessage, { variant: "error" });
    },
  });

  const onSubmit = (data) => {
    if (setFormIsDirty) setFormIsDirty(false);
    trackEvent(Events.derivedDatasetCreated, {
      [PropertyName.derivedDataset]: {
        "Derived Dataset Name": data?.name,
        // "Model Used": data?.promptConfig
        //   ?.map((prompt) => prompt?.model?.join(","))
        //   .join(","),
      },
    });

    const updatedData = {
      ...(!selectedExperiment && {
        dataset_id: dataset,
        name: data?.name,
        experiment_type: data?.experimentType,
      }),
      column_id: data?.columnId,
      // outputFormat: data?.outputFormat,
      prompt_config: data?.promptConfig,
      user_eval_metrics: data?.userEvalMetrics,
    };

    mutate(updatedData);
  };

  const { currentStep, nextStep, prevStep, setStepValidated } =
    useRunExperimentStoreShallow((state) => ({
      currentStep: state.currentStep,
      reset: state.reset,
      nextStep: state.nextStep,
      prevStep: state.prevStep,
      setStepValidated: state.setStepValidated,
    }));
  const isLastStep = selectedExperiment ? currentStep === 1 : currentStep === 2;

  const watchedName = useWatch({ control, name: "name" });
  const watchedModelType = useWatch({ control, name: "experimentType" });
  const watchedPromptConfig = useWatch({ control, name: "promptConfig" });
  const debouncedName = useDebounce(watchedName, 500);

  useEffect(() => {
    if (!selectedExperiment && debouncedName?.trim()) {
      trigger("name");
    }
  }, [debouncedName, selectedExperiment]);

  const getStepFields = (experimentType, promptConfigLength) => {
    const step1Fields = ["name", "experimentType"];
    const step3Fields = ["userEvalMetrics"];
    const configFields = [];

    for (let i = 0; i < promptConfigLength; i++) {
      configFields.push(`promptConfig.${i}.model`);
      if (experimentType === "llm") {
        configFields.push(`promptConfig.${i}.unmappedVariables`);
      }

      if (experimentType === "tts") {
        configFields.push(`promptConfig.${i}.messages`);
      } else if (experimentType === "stt") {
        configFields.push(`promptConfig.${i}.voiceInputColumnId`);
      } else if (experimentType === "img") {
        configFields.push(`promptConfig.${i}.messages`);
      }
    }

    configFields.push("promptConfig");

    if (selectedExperiment) {
      return [configFields, step3Fields];
    }
    return [step1Fields, configFields, step3Fields];
  };

  useEffect(() => {
    if (watchedModelType) {
      clearErrors("experimentType");
    }
  }, [watchedModelType, clearErrors]);

  useEffect(() => {
    if (!watchedPromptConfig?.length) return;

    watchedPromptConfig.forEach((config, index) => {
      if (config?.model?.length > 0) {
        clearErrors(`promptConfig.${index}.model`);
      }

      if (watchedModelType === "stt" && config?.voiceInputColumnId) {
        clearErrors(`promptConfig.${index}.voiceInputColumnId`);
      }

      if (
        ["tts", "img"].includes(watchedModelType) &&
        config?.messages?.length > 0 &&
        !errors?.promptConfig?.[index]?.messages
      ) {
        clearErrors(`promptConfig.${index}.messages`);
      }
      if (watchedModelType === "llm" && config?.unmappedVariables === 0) {
        clearErrors(`promptConfig.${index}.unmappedVariables`);
      }
      // TH-4334: once the user swaps the draft version to a saved one,
      // drop the card-level draft error immediately instead of waiting
      // for the next Next-click `trigger()` to revalidate.
      if (watchedModelType === "llm" && config?.isDraft === false) {
        clearErrors(`promptConfig.${index}.isDraft`);
      }
    });

    const allPromptsHaveModel = watchedPromptConfig.every(
      (config) => config?.model?.length > 0,
    );
    if (watchedPromptConfig.length > 0 && allPromptsHaveModel) {
      clearErrors("promptConfig");
    }
  }, [watchedPromptConfig, watchedModelType, clearErrors]);
  const handleNextStep = async () => {
    const promptConfigLength = watchedPromptConfig?.length || 0;
    const stepFields = getStepFields(watchedModelType, promptConfigLength);

    let fieldsToValidate = stepFields[currentStep];
    if (
      currentStep === 0 &&
      !selectedExperiment &&
      !errors.name &&
      watchedName?.trim()
    ) {
      fieldsToValidate = fieldsToValidate.filter((f) => f !== "name");
    }

    const isValid = await trigger(fieldsToValidate);

    if (isValid) {
      setStepValidated(currentStep, true);
      nextStep();
    } else {
      setStepValidated(currentStep, false);
    }
  };

  const handlePromptConfigChange = (e) => {
    const updatedType = e.target.value;
    setValue(
      "promptConfig",
      updatedType === MODEL_TYPES.LLM
        ? []
        : [getDefaultPromptConfigByModelType(updatedType)],
    );
    let outputFormat = "string";
    if (updatedType === MODEL_TYPES.TTS) outputFormat = "audio";
    if (updatedType === MODEL_TYPES.IMAGE) outputFormat = "image";
    setValue("outputFormat", outputFormat);
    clearErrors([
      "promptConfig",
      "experimentType",
      "outputFormat",
      "userEvalMetrics",
    ]);
  };

  return (
    <AudioPlaybackProvider>
      <Box
        sx={{
          gap: "20px",
          display: "flex",
          flexDirection: "column",
          height: "100%",
          width: "100%",
          backgroundColor: "background.paper",
          p: 2,
        }}
      >
        <Box
          display={"flex"}
          flexDirection={"row"}
          alignItems={"flex-start"}
          justifyContent={"space-between"}
        >
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 0.5,
            }}
          >
            <Typography
              fontWeight={"fontWeightSemiBold"}
              typography={"m3"}
              color="text.primary"
            >
              Run Experiment
            </Typography>
            <HelperText
              sx={{
                color: "text.primary",
                typography: "s1",
                fontWeight: "fontWeightRegular",
              }}
              text="Test, validate, and compare different prompt configurations"
            />
          </Box>
          <Stack direction={"row"} alignItems={"center"} gap={1.5}>
            <Link
              href="https://docs.futureagi.com/docs/dataset/features/experiments"
              underline="always"
              color="primary.main"
              target="_blank"
              rel="noopener noreferrer"
              fontWeight="fontWeightMedium"
              sx={{ color: "blue.500", textDecorationColor: "blue.500" }}
            >
              Learn more
            </Link>
            <IconButton
              onClick={onClose}
              sx={{
                padding: (theme) => theme.spacing(0.25),
                color: "text.primary",
              }}
            >
              <Iconify icon="mingcute:close-line" />
            </IconButton>
          </Stack>
        </Box>
        <Divider flexItem orientation="horizontal" />
        <form
          style={{
            flex: 1,

            display: "flex",
            flexDirection: "column",
            overflowY: "auto",
            overflowX: "hidden",
            scrollbarWidth: "none", // for Firefox
            msOverflowStyle: "none",
          }}
          onSubmit={(e) => {
            e.preventDefault();
            handleSubmit(onSubmit)();
          }}
        >
          <Box
            sx={{
              mb: 1,
              position: "sticky",
              top: 0,
              backgroundColor: "background.paper",
              zIndex: 100,
              paddingY: 1,
            }}
          >
            <StepperForExperiment />
          </Box>

          <ShowComponent condition={currentStep === 0 && !selectedExperiment}>
            <BasicStepExperimentCreation
              control={control}
              title={"Experiment type"}
              subTitle={"Select the type to experiment with"}
              handleModelTypeChange={handlePromptConfigChange}
              isValidatingName={formState.isValidating}
              isNameValid={!errors.name && !!watchedName?.trim()}
            />
          </ShowComponent>
          <ShowComponent
            condition={
              selectedExperiment ? currentStep === 0 : currentStep === 1
            }
          >
            <ConfigureStepExperimentCreation
              control={control}
              setValue={setValue}
              allColumns={allColumns}
              getValues={getValues}
              errors={errors}
              unregister={unregister}
              clearErrors={clearErrors}
              watch={watch}
              setError={setError}
              derivedVariables={derivedVariables}
              jsonSchemas={jsonSchemas}
              isEditing={Boolean(selectedExperiment)}
            />
          </ShowComponent>
          <ShowComponent
            condition={
              selectedExperiment ? currentStep === 1 : currentStep === 2
            }
          >
            <EvaluationStepExperimentCreation
              allColumns={allColumns}
              control={control}
              errors={errors}
              isEditingExperiment={Boolean(selectedExperiment)}
            />
          </ShowComponent>

          <Box
            p={2}
            display="flex"
            justifyContent="flex-end"
            gap={2}
            bottom={2}
            right={0}
            flexShrink={0}
            marginTop={"auto"}
            backgroundColor="background.paper"
            width={"100%"}
          >
            <Button
              onClick={prevStep}
              disabled={currentStep === 0}
              variant="outlined"
              size="medium"
              sx={{
                color: "text.primary",
                padding: theme.spacing(0.125, 1.5),
                fontWeight: "fontWeightMedium",
                borderRadius: theme.spacing(0.5),
                width: "143px",
                borderColor: "divider",
              }}
            >
              Back
            </Button>

            {!isLastStep ? (
              <Button
                onClick={handleNextStep}
                //  disabled={!canProceed}
                variant="contained"
                color="primary"
                size="medium"
                sx={{
                  padding: theme.spacing(0.125, 1.5),
                  fontWeight: "fontWeightMedium",
                  borderRadius: theme.spacing(0.5),
                  borderColor: "divider",
                  width: "143px",
                }}
              >
                Next
              </Button>
            ) : (
              <LoadingButton
                onClick={handleSubmit(onSubmit)}
                variant="contained"
                loading={isPending}
                color="primary"
                size="medium"
                sx={{
                  padding: theme.spacing(0.125, 1.5),
                  fontWeight: "fontWeightMedium",
                  borderRadius: theme.spacing(0.5),
                  borderColor: "divider",
                  width: "143px",
                }}
              >
                Run Experiment
              </LoadingButton>
            )}
          </Box>
        </form>
      </Box>
    </AudioPlaybackProvider>
  );
};

RunExperimentForm.propTypes = {
  allColumns: PropTypes.array,
  jsonSchemas: PropTypes.object,
  derivedVariables: PropTypes.object,
  onClose: PropTypes.func,
  control: PropTypes.object,
  handleSubmit: PropTypes.func,
  setFormIsDirty: PropTypes.func,
  onSuccessfulSubmit: PropTypes.func,
  onSubmitSuccess: PropTypes.func,
  setValue: PropTypes.func,
  resetField: PropTypes.func,
  getValues: PropTypes.func,
  errors: PropTypes.object,
  reset: PropTypes.func,
  importedPrompts: PropTypes.object,
  setImportedPrompts: PropTypes.func,
  refreshGrid: PropTypes.func,
  clearErrors: PropTypes.func,
  unregister: PropTypes.func,
  watch: PropTypes.func,
  setError: PropTypes.func,
  selectedExperiment: PropTypes.string,
  trigger: PropTypes.func,
};

const RunExperiment = ({
  tabRef: _tabRef,
  onSuccessfulSubmit,
  experimentColumns = null,
}) => {
  const { dataset: datasetParam } = useParams();
  const [searchParams] = useSearchParams();
  const dataset = datasetParam || searchParams.get("datasetId");
  const { refreshGrid } = useDevelopDetailContext();

  // Using individual store
  const {
    openRunExperiment,
    selectedExperiment,
    reset: resetRunExperimentStore,
  } = useRunExperimentStoreShallow((state) => ({
    openRunExperiment: state.openRunExperiment,
    selectedExperiment: state.selectedExperiment,
    reset: state.reset,
  }));

  const datasetColumns = useDatasetColumnConfig(
    dataset,
    false,
    !!selectedExperiment,
    !!selectedExperiment,
  );
  const allColumns = useMemo(() => {
    const cols = experimentColumns ?? datasetColumns;
    if (!cols) return cols;
    return cols.map((col) => ({
      ...col,
      field: col?.field ?? col?.id,
      headerName: col?.headerName ?? col?.name,
      value: col?.name,
    }));
  }, [experimentColumns, datasetColumns]);

  const { data: experimentJsonSchemas = {} } = useGetExperimentJSONSchema(
    !!selectedExperiment,
    selectedExperiment,
  );
  const { data: datasetJsonSchemas = {} } = useGetJsonColumnSchema(dataset);
  const { data: experimentDerivedVariablesData = {} } =
    useExperimentDerivedVariables(!!selectedExperiment, selectedExperiment);
  const { data: datasetDerivedVariablesData = {} } =
    useDatasetDerivedVariables(dataset);
  const jsonSchemas = experimentColumns
    ? experimentJsonSchemas
    : datasetJsonSchemas;
  const derivedVariables = experimentColumns
    ? experimentDerivedVariablesData
    : datasetDerivedVariablesData;

  const { data: suggestedName } = useQuery({
    queryKey: ["experiment-suggested-name", dataset],
    queryFn: () => axios.get(endpoints.develop.experiment.suggestName(dataset)),
    enabled: !selectedExperiment && openRunExperiment,
    select: (d) => d.data?.result?.suggestedName,
  });

  const onClose = () => {
    resetRunExperimentStore();
  };

  const [formIsDirty, setFormIsDirty] = useState(false);
  const hasSubmittedSuccessfullyRef = useRef(false);
  const [isConfirmationModalOpen, setConfirmationModalOpen] = useState(false);
  const {
    control,
    reset,
    handleSubmit,
    formState,
    getValues,
    setValue,
    resetField,
    clearErrors,
    watch,
    unregister,
    setError,
    trigger,
  } = useForm({
    defaultValues: getDefaultValue(),
    resolver: zodResolver(
      getNewExperimentValidationSchema(
        allColumns,
        Boolean(selectedExperiment),
        dataset,
      ),
    ),
    shouldFocusError: true,
  });

  const { isDirty, errors } = formState;
  const _selectedColumnId = useWatch({ control, name: "columnId" });
  const theme = useTheme();
  const [importedPrompts, setImportedPrompts] = useState({});
  const queryClient = useQueryClient();
  const { data: experimentData, isLoading: isLoadingExperiment } =
    useGetExperimentDetails(selectedExperiment);

  const { data: userEvalList, isLoading: isLoadingEvals } = useColumnInfo(
    experimentData?.columnId,
  );

  // Only reset when selectedExperiment changes - this is the key to preventing unwanted resets
  useEffect(() => {
    if (selectedExperiment && experimentData && !isDirty) {
      reset(
        getExperimentDefaultValue(experimentData, allColumns, userEvalList),
      );
    }
  }, [
    selectedExperiment,
    experimentData,
    allColumns,
    isLoadingEvals,
    userEvalList,
    reset,
    isLoadingExperiment,
    isDirty,
  ]);

  useEffect(() => {
    setFormIsDirty(isDirty);
  }, [isDirty]);

  useEffect(() => {
    if (suggestedName && !selectedExperiment) {
      setValue("name", suggestedName);
    }
  }, [suggestedName, selectedExperiment, setValue, openRunExperiment]);

  useEffect(() => {
    if (openRunExperiment) {
      return () => {
        setImportedPrompts({});
      };
    }
  }, [openRunExperiment]);

  const handleClose = () => {
    if (hasSubmittedSuccessfullyRef.current) {
      onCloseClick();
    } else if (formIsDirty) {
      setConfirmationModalOpen(true);
    } else {
      onCloseClick();
    }
  };

  const handleConfirmClose = () => {
    setConfirmationModalOpen(false);
    onCloseClick();
  };

  const handleCancelClose = () => {
    setConfirmationModalOpen(false);
  };

  const handleSubmitSuccess = () => {
    hasSubmittedSuccessfullyRef.current = true;
    onCloseClick();
  };

  const onCloseClick = () => {
    reset(getDefaultValue());
    queryClient.invalidateQueries({
      queryKey: ["experiment", selectedExperiment],
    });
    onClose();
    hasSubmittedSuccessfullyRef.current = false;
  };
  return (
    <>
      <Drawer
        anchor="right"
        open={openRunExperiment}
        onClose={handleClose}
        PaperProps={{
          sx: {
            height: "100vh",
            position: "fixed",
            zIndex: 2,
            boxShadow: "-10px 0px 100px #00000035",
            borderRadius: "10px",
            overflow: "visible",
            width: "732px",
            p: 0,
            backgroundColor: "transparent",
          },
        }}
        ModalProps={{
          BackdropProps: {
            style: { backgroundColor: "transparent" },
          },
        }}
        sx={{
          zIndex: 1099,
        }}
      >
        {openRunExperiment ? (
          isLoadingExperiment || isLoadingEvals ? (
            <RunExperimentFormSkeleton />
          ) : (
            <RunExperimentForm
              allColumns={allColumns?.filter((col) =>
                ["run_prompt", "others"].includes(
                  col?.originType?.toLowerCase(),
                ),
              )}
              jsonSchemas={jsonSchemas}
              derivedVariables={derivedVariables}
              onClose={handleClose}
              control={control}
              handleSubmit={handleSubmit}
              setFormIsDirty={setFormIsDirty}
              onSuccessfulSubmit={onSuccessfulSubmit}
              setError={setError}
              setValue={setValue}
              resetField={resetField}
              getValues={getValues}
              errors={errors}
              reset={reset}
              importedPrompts={importedPrompts}
              setImportedPrompts={setImportedPrompts}
              refreshGrid={refreshGrid}
              clearErrors={clearErrors}
              unregister={unregister}
              watch={watch}
              trigger={trigger}
              selectedExperiment={selectedExperiment}
              onSubmitSuccess={handleSubmitSuccess}
            />
          )
        ) : null}
      </Drawer>
      <ConfirmDialog
        content="Are you sure you want to close? Your work will be lost"
        action={
          <Button
            variant="contained"
            color="error"
            size="small"
            onClick={handleConfirmClose}
            sx={{ paddingX: theme.spacing(3) }}
          >
            Confirm
          </Button>
        }
        open={isConfirmationModalOpen}
        onClose={handleCancelClose}
        title="Confirm Action"
        message="Are you sure you want to proceed?"
      />
    </>
  );
};

RunExperiment.propTypes = {
  tabRef: PropTypes.object,
  onSuccessfulSubmit: PropTypes.func,
  experimentColumns: PropTypes.array,
};

export default RunExperiment;
