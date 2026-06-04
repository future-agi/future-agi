import { Box, Button, InputAdornment, Stack, Typography } from "@mui/material";
import React, { useState } from "react";
import { useForm } from "react-hook-form";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { FieldWrapper } from "./ShareComponents";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import {
  createEditOptimizerSchema,
  OPTIMIZER_OPTIONS,
  OptimizerConfigurationMapping,
  OPTIMIZER_TYPE,
} from "./common";
import { zodResolver } from "@hookform/resolvers/zod";
import { ShowComponent } from "src/components/show";
import { LoadingButton } from "@mui/lab";
import SvgColor from "src/components/svg-color";
import ConfigureKeysModal from "src/components/ConfigureApiKeysModal/ConfigureKeysModal";
import CustomModelDropdownControl from "src/components/custom-model-dropdown/CustomModelDropdownControl";
import logger from "src/utils/logger";
import EachTheoremComponent from "./EachTheoremComponent";
import { useMutation } from "@tanstack/react-query";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";

const getSelectedOptimizerIcon = (optimizerValue) => {
  return (
    OPTIMIZER_OPTIONS.find((e) => e.value === optimizerValue).icon ??
    OPTIMIZER_OPTIONS[0].icon
  );
};

const CreateEditOptimizationForm = ({ onClose, defaultValues, onSuccess }) => {
  const {
    control,
    handleSubmit,
    formState: { isValid, errors },
    watch,
    setValue,
    reset,
    trigger,
  } = useForm({
    defaultValues: defaultValues
      ? { ...defaultValues }
      : {
          name: "",
          model: "",
          optimiserType: "",
          configuration: {},
        },
    resolver: zodResolver(createEditOptimizerSchema),
    mode: "onChange",
  });
  const handleOnChange = () => {
    trigger("configuration.minExamples"); //we need to trigger both fields to validate the relation as they depend on each other
    trigger("configuration.maxExamples");
  };
  const handleCloseForm = () => {
    reset();
    onClose();
  };

  const { executionId } = useParams();
  const { mutate: createOptimization, isPending: isCreatingOptimization } =
    useMutation({
      mutationFn: (data) =>
        axios.post(endpoints.optimizeSimulate.createOptimization, data),
      onSuccess: (data) => {
        enqueueSnackbar("Optimization Created Successfully", {
          variant: "success",
        });
        onSuccess?.(data);

        onClose();
      },
    });
  const handleSubmitForm = (data) => {
    const configuration = data.configuration || {};
    const payload = {
      name: data.name,
      model: data.model,
      optimiser_type: data.optimiserType,
      configuration: {
        ...(configuration.numVariations !== undefined && {
          num_variations: configuration.numVariations,
        }),
        ...(configuration.minExamples !== undefined && {
          min_examples: configuration.minExamples,
        }),
        ...(configuration.maxExamples !== undefined && {
          max_examples: configuration.maxExamples,
        }),
        ...(configuration.nTrials !== undefined && {
          n_trials: configuration.nTrials,
        }),
        ...(configuration.numGradients !== undefined && {
          num_gradients: configuration.numGradients,
        }),
        ...(configuration.errorsPerGradient !== undefined && {
          errors_per_gradient: configuration.errorsPerGradient,
        }),
        ...(configuration.promptsPerGradient !== undefined && {
          prompts_per_gradient: configuration.promptsPerGradient,
        }),
        ...(configuration.taskDescription !== undefined && {
          task_description: configuration.taskDescription,
        }),
        ...(configuration.mutateRounds !== undefined && {
          mutate_rounds: configuration.mutateRounds,
        }),
        ...(configuration.refineIterations !== undefined && {
          refine_iterations: configuration.refineIterations,
        }),
        ...(configuration.beamSize !== undefined && {
          beam_size: configuration.beamSize,
        }),
        ...(configuration.numRounds !== undefined && {
          num_rounds: configuration.numRounds,
        }),
        ...(configuration.maxMetricCalls !== undefined && {
          max_metric_calls: configuration.maxMetricCalls,
        }),
      },
      test_execution_id: executionId,
    };
    logger.debug(payload);
    createOptimization(payload);
  };

  const optimizerValue = watch("optimiserType");
  const [isApiConfigurationOpen, setIsApiConfigurationOpen] = useState(null);

  const handleOnChangeOptimizer = (e) => {
    const value = e.target.value;
    if (value !== optimizerValue) {
      setValue(
        "configuration",
        { ...OptimizerConfigurationMapping[value] },
        {
          shouldDirty: false,
          shouldValidate: false,
        },
      );
    }
    setValue("optimiserType", value, {
      shouldDirty: true,
      shouldValidate: true,
    });
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <FieldWrapper helperText="Name your optimization run">
        <FormTextFieldV2
          control={control}
          fieldName="name"
          label="Name"
          required
          placeholder="Optimization Name"
          size="small"
          fullWidth
          sx={{
            "& .MuiInputLabel-root": {
              fontWeight: 500,
            },
          }}
        />
      </FieldWrapper>
      <FieldWrapper helperText="Choose from our advanced optimizers to refine your prompt.">
        <FormSearchSelectFieldControl
          control={control}
          onChange={handleOnChangeOptimizer}
          options={OPTIMIZER_OPTIONS.map((model) => {
            return {
              ...model,
              component: <EachTheoremComponent model={model} />,
            };
          })}
          InputProps={{
            startAdornment: optimizerValue && (
              <InputAdornment position="start">
                <SvgColor
                  sx={{ color: "primary.main", width: 20, height: 20 }}
                  src={getSelectedOptimizerIcon(optimizerValue)}
                />
              </InputAdornment>
            ),
          }}
          error={errors?.optimiserType && errors.optimiserType.message}
          fieldName={"optimiserType"}
          label={"Choose Optimizer"}
          size={"small"}
          placeholder={"eg: Bayesian Search"}
          fullWidth
          required
        />
      </FieldWrapper>
      <FieldWrapper helperText="Model used for optimization.">
        <ConfigureKeysModal
          open={Boolean(isApiConfigurationOpen)}
          selectedModel={isApiConfigurationOpen}
          onClose={() => setIsApiConfigurationOpen(null)}
        />
        <CustomModelDropdownControl
          control={control}
          fieldName="model"
          hoverPlacement="bottom"
          label="Language Model"
          searchDropdown
          modelObjectKey={null}
          size="small"
          fullWidth
          extraParams={{ model_type: "llm" }}
          onModelConfigOpen={(selectedModel) => {
            setIsApiConfigurationOpen(selectedModel);
          }}
          required
          showIcon
          inputSx={{
            "&.MuiInputLabel-root, .MuiInputLabel-shrink": {
              fontWeight: "fontWeightMedium",
              color: "text.disabled",
            },
            "&.Mui-focused.MuiInputLabel-shrink": {
              color: "text.disabled",
            },
            "& .MuiInputLabel-root.Mui-focused": {
              color: "text.secondary",
            },
          }}
          hideCreateLabel={true}
          placeholder={"Choose a Model (eg: gpt-5)"}
        />
      </FieldWrapper>
      {optimizerValue && (
        <Stack
          spacing={2}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            backgroundColor: "background.default",
            padding: 2,
            paddingTop: 0,
            borderRadius: 0.5,
            maxHeight: "300px",
            overflowY: "scroll",
          }}
        >
          <Stack
            sx={{
              position: "sticky",
              top: 0,
              backgroundColor: "background.default",
              zIndex: 100,
              paddingTop: 2,
            }}
          >
            <Typography variant="s2" fontWeight={"fontWeightMedium"}>
              Add Parameters
            </Typography>
            <Typography
              variant="s3"
              fontWeight={"fontWeightRegular"}
              color={"text.secondary"}
            >
              These are the recommended parameters which give a good balance
              between speed and quality of the prompt optimization run
            </Typography>
          </Stack>
          <Stack
            spacing={2.5}
            sx={{
              border: "1px solid",
              bgColor: "divider",
              borderColor: "divider",
              borderRadius: 0.5,
              py: 2,
              px: 1.75,
            }}
          >
            <ShowComponent
              condition={optimizerValue === OPTIMIZER_TYPE.RANDOM_SEARCH}
            >
              <FieldWrapper helperText="Number of random variations to generate">
                <FormTextFieldV2
                  control={control}
                  fieldName="configuration.numVariations"
                  label="Number Variations"
                  fullWidth
                  size="small"
                  fieldType="number"
                  required
                />
              </FieldWrapper>
            </ShowComponent>
            <ShowComponent
              condition={optimizerValue === OPTIMIZER_TYPE.BAYESIAN}
            >
              <FieldWrapper helperText="Minimum number of examples to use">
                <FormTextFieldV2
                  control={control}
                  fieldName="configuration.minExamples"
                  label="Min examples"
                  fullWidth
                  size="small"
                  fieldType="number"
                  required
                  onChange={handleOnChange}
                />
              </FieldWrapper>
            </ShowComponent>
            <ShowComponent
              condition={optimizerValue === OPTIMIZER_TYPE.BAYESIAN}
            >
              <FieldWrapper helperText="Maximum number of examples to use">
                <FormTextFieldV2
                  control={control}
                  fieldName="configuration.maxExamples"
                  label="Max examples"
                  fullWidth
                  size="small"
                  fieldType="number"
                  required
                  onChange={handleOnChange}
                />
              </FieldWrapper>
            </ShowComponent>
            <ShowComponent
              condition={optimizerValue === OPTIMIZER_TYPE.BAYESIAN}
            >
              <FieldWrapper helperText="Number of optimization trials to run">
                <FormTextFieldV2
                  control={control}
                  fieldName="configuration.nTrials"
                  label="No.of trials"
                  fullWidth
                  size="small"
                  fieldType="number"
                  required
                />
              </FieldWrapper>
            </ShowComponent>
            <ShowComponent
              condition={optimizerValue === OPTIMIZER_TYPE.PROTEGI}
            >
              <FieldWrapper helperText="Number of random variations to generate">
                <FormTextFieldV2
                  control={control}
                  fieldName="configuration.numGradients"
                  label="Number of gradients"
                  fullWidth
                  size="small"
                  fieldType="number"
                  required
                />
              </FieldWrapper>
            </ShowComponent>
            <ShowComponent
              condition={optimizerValue === OPTIMIZER_TYPE.PROTEGI}
            >
              <FieldWrapper helperText="Number of errors to analyze per gradient">
                <FormTextFieldV2
                  control={control}
                  fieldName="configuration.errorsPerGradient"
                  label="Errors per gradient"
                  fullWidth
                  size="small"
                  fieldType="number"
                  required
                />
              </FieldWrapper>
            </ShowComponent>
            <ShowComponent
              condition={optimizerValue === OPTIMIZER_TYPE.PROTEGI}
            >
              <FieldWrapper helperText="Number of prompts to generate per gradient">
                <FormTextFieldV2
                  control={control}
                  fieldName="configuration.promptsPerGradient"
                  label="Prompts per gradient"
                  fullWidth
                  size="small"
                  fieldType="number"
                  required
                />
              </FieldWrapper>
            </ShowComponent>
            <ShowComponent condition={!!optimizerValue}>
              <FormTextFieldV2
                placeholder={"(eg: Improve the agent's response quality)"}
                control={control}
                fieldName="configuration.taskDescription"
                label="Optimization Objective"
                multiline
                minRows={4}
                maxRows={10}
                inputProps={{
                  style: {
                    minHeight: "82px",
                    overflowY: "auto",
                    whiteSpace: "pre-wrap",
                  },
                }}
                fullWidth
              />
            </ShowComponent>
            <ShowComponent
              condition={optimizerValue === OPTIMIZER_TYPE.PROMPTWIZARD}
            >
              <FieldWrapper helperText="Number of mutation rounds">
                <FormTextFieldV2
                  control={control}
                  fieldName="configuration.mutateRounds"
                  label="Mutated Rounds"
                  fullWidth
                  size="small"
                  fieldType="number"
                  required
                />
              </FieldWrapper>
            </ShowComponent>
            <ShowComponent
              condition={optimizerValue === OPTIMIZER_TYPE.PROMPTWIZARD}
            >
              <FieldWrapper helperText="Number of refinement iterations per round">
                <FormTextFieldV2
                  control={control}
                  fieldName="configuration.refineIterations"
                  label="Refined Iterations"
                  fullWidth
                  size="small"
                  fieldType="number"
                  required
                />
              </FieldWrapper>
            </ShowComponent>
            <ShowComponent
              condition={
                optimizerValue === OPTIMIZER_TYPE.PROTEGI ||
                optimizerValue === OPTIMIZER_TYPE.PROMPTWIZARD
              }
            >
              <FieldWrapper helperText="Number of candidates to keep at each step">
                <FormTextFieldV2
                  control={control}
                  fieldName="configuration.beamSize"
                  label="Beam size"
                  fullWidth
                  size="small"
                  fieldType="number"
                  required
                />
              </FieldWrapper>
            </ShowComponent>
            <ShowComponent
              condition={
                optimizerValue === OPTIMIZER_TYPE.PROTEGI ||
                optimizerValue === OPTIMIZER_TYPE.METAPROMPT
              }
            >
              <FieldWrapper helperText="Number of optimization rounds to run">
                <FormTextFieldV2
                  control={control}
                  fieldName="configuration.numRounds"
                  label="Number of Rounds"
                  fullWidth
                  size="small"
                  fieldType="number"
                  required
                />
              </FieldWrapper>
            </ShowComponent>
            <ShowComponent condition={optimizerValue === OPTIMIZER_TYPE.GEPA}>
              <FieldWrapper helperText="Max Metric Calls">
                <FormTextFieldV2
                  control={control}
                  fieldName="configuration.maxMetricCalls"
                  label="Max Metric Calls"
                  fullWidth
                  size="small"
                  fieldType="number"
                  required
                />
              </FieldWrapper>
            </ShowComponent>
          </Stack>
        </Stack>
      )}

      <Box
        sx={{
          display: "flex",
          flexDirection: "row",
          justifyContent: "flex-end",
          gap: 2,
        }}
      >
        <Button onClick={handleCloseForm} variant="outlined" size="small">
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          color="primary"
          size="small"
          loading={isCreatingOptimization}
          onClick={handleSubmit(handleSubmitForm)}
          disabled={!isValid}
          startIcon={<SvgColor src="/assets/icons/navbar/ic_get_started.svg" />}
        >
          Start Optimizing your agent
        </LoadingButton>
      </Box>
    </Box>
  );
};

CreateEditOptimizationForm.propTypes = {
  onClose: PropTypes.func,
  defaultValues: PropTypes.object,
  onSuccess: PropTypes.func,
};
export default CreateEditOptimizationForm;
