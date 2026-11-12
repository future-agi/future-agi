import React, { useMemo } from "react";
import { Stack, Grid } from "@mui/material";
import PromptTTSInput from "../../RunPrompt/Components/PromptTTSInput";
import CustomModelDropdownControl from "src/components/custom-model-dropdown/CustomModelDropdownControl";
import NewModelRenderWithParamsTool from "./NewModelRenderWithParamsTool";
import { findInvalidVariables, MODEL_TYPES } from "../../RunPrompt/common";
import PropTypes from "prop-types";
import { useWatch } from "react-hook-form";

// A TTS / Image prompt item: the text to speak or draw and its models. No
// tool block: a media run makes one generation per try and calls no tools
// (options-truth L7b typed:add-tool), so none is offered.
const ConfigureStepTTSItem = ({
  index,
  field: _field,
  allColumns,
  setValue,
  clearErrors,
  errors,
  watch,
  control,
  jsonSchemas,
  getValues,
  unregister,
  totalPrompts,
  remove,
  watchModelType,
  derivedVariables,
}) => {
  const watchedMessages =
    useWatch({
      name: `promptConfig.${index}.messages`,
      control,
    }) || [];

  const watchModels = useWatch({
    control,
    name: `promptConfig.${index}.model`,
  });

  const allInvalidVariables = useMemo(() => {
    const invalids = [];
    const safeMessages = Array.isArray(watchedMessages) ? watchedMessages : [];
    safeMessages.forEach(({ content }) => {
      if (Array.isArray(content)) {
        content.forEach((part) => {
          if (part.type === "text" && part.text) {
            const found = findInvalidVariables(
              part.text,
              allColumns,
              jsonSchemas,
              derivedVariables,
            );
            invalids.push(...found);
          }
        });
      }
    });
    return invalids;
  }, [
    JSON.stringify(watchedMessages),
    allColumns,
    jsonSchemas,
    derivedVariables,
  ]);

  const handleRemoveModel = (modelIdx) => {
    const currentModels = getValues(`promptConfig.${index}.model`) || [];
    if (currentModels.length > modelIdx) {
      const updatedModels = currentModels.filter((_, i) => i !== modelIdx);
      setValue(`promptConfig.${index}.model`, updatedModels);
      if (typeof unregister === "function") {
        const removedModel = currentModels[modelIdx];
        if (removedModel && removedModel.value) {
          unregister(`promptConfig.${index}.modelParams.${removedModel.value}`);
        }
      }
    }
  };

  return (
    <Stack sx={{ gap: 3, backgroundColor: "background.paper", padding: 2 }}>
      <PromptTTSInput
        allColumns={allColumns}
        derivedVariables={derivedVariables}
        allInvalidVariables={allInvalidVariables}
        setValue={setValue}
        fieldPrefix={`promptConfig.${index}.messages`}
        clearErrors={clearErrors}
        errors={errors}
        watch={watch}
        control={control}
        jsonSchemas={jsonSchemas}
        title={`Prompt ${index + 1}`}
        onDelete={
          totalPrompts === 1
            ? null
            : () => {
                remove(index);
              }
        }
      />

      <CustomModelDropdownControl
        control={control}
        fieldName="model"
        modelObjectKey={`model`}
        voiceObjectKey={`voice`}
        steps={2}
        fieldPrefix={`promptConfig.${index}`}
        label="Models"
        searchDropdown
        size="small"
        fullWidth
        required
        inputSx={{
          "&.MuiInputLabel-root, .MuiInputLabel-shrink": {
            fontWeight: "fontWeightMedium",
            color: "text.secondary",
          },
          "&.Mui-focused.MuiInputLabel-shrink": {
            color: "text.secondary",
          },
          "& .MuiInputLabel-root.Mui-focused": {
            color: "text.secondary",
          },
        }}
        showIcon
        multiple
        allowSelectingVoices={watchModelType === MODEL_TYPES.TTS}
        hideCreateLabel={true}
        showButtons={true}
        extraParams={{ model_type: watchModelType }}
      />
      <Grid container spacing={2} columns={2}>
        {Array.isArray(watchModels) &&
          watchModels.length > 0 &&
          watchModels.map((model, modelIdx) => (
            <Grid item xs={1} key={model.value}>
              <NewModelRenderWithParamsTool
                selectedModel={model}
                modelIndex={modelIdx}
                index={modelIdx}
                control={control}
                setValue={setValue}
                useWatch={watch}
                modelParamsFieldName={`promptConfig.${index}.modelParams.${model.value}`}
                modelType={watchModelType}
                voiceFieldName={`promptConfig.${index}.model.${modelIdx}.voices`}
                onRemove={() => handleRemoveModel(modelIdx)}
                customBgColor="background.neutral"
              />
            </Grid>
          ))}
      </Grid>
    </Stack>
  );
};

ConfigureStepTTSItem.propTypes = {
  index: PropTypes.number.isRequired,
  field: PropTypes.object.isRequired,
  allColumns: PropTypes.array,
  setValue: PropTypes.func,
  clearErrors: PropTypes.func,
  errors: PropTypes.object,
  watch: PropTypes.func,
  control: PropTypes.object,
  jsonSchemas: PropTypes.any,
  getValues: PropTypes.func,
  unregister: PropTypes.func,
  totalPrompts: PropTypes.number,
  remove: PropTypes.func,
  watchModelType: PropTypes.string,
  derivedVariables: PropTypes.object,
};

export default ConfigureStepTTSItem;
