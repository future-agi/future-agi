import React, { useMemo, useState } from "react";
import { Stack, Grid, Box, Typography, Button } from "@mui/material";
import PromptTTSInput from "../../RunPrompt/Components/PromptTTSInput";
import CustomModelDropdownControl from "src/components/custom-model-dropdown/CustomModelDropdownControl";
import NewModelRenderWithParamsTool from "./NewModelRenderWithParamsTool";
import { findInvalidVariables, MODEL_TYPES } from "../../RunPrompt/common";
import PropTypes from "prop-types";
import { useFieldArray, useWatch } from "react-hook-form";
import RadioField from "src/components/RadioField/RadioField";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color/svg-color";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import { getRandomId } from "src/utils/utils";
import CustomToolModal from "../../RunPrompt/Modals/CustomToolModal";
import { useRunPromptOptions } from "src/api/develop/develop-detail";
const toolsChoiceOptions = [
  {
    label: "Auto",
    value: "auto",
  },
  {
    label: "Required",
    value: "required",
  },
  {
    label: "None",
    value: "none",
  },
];
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
  const [isAddToolModalOpen, setIsAddToolModalOpen] = useState(false);
  const [selectedTool, setSelectedTool] = useState(null);
  const { data: runPromptOptions } = useRunPromptOptions();
  const toolsOptions = useMemo(() => {
    return (
      runPromptOptions?.available_tools?.map((t) => ({
        label: t.name,
        value: t.id,
        tool: t,
      })) ?? []
    );
  }, [runPromptOptions?.available_tools]);
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
  const watchModelPromptConfiguration = useWatch({
    control,
    name: `promptConfig.${index}.configuration`,
  });
  const watchedTools = useWatch({
    control,
    name: `promptConfig.${index}.configuration.tools`,
  });
  const { replace } = useFieldArray({
    control,
    name: `promptConfig.${index}.configuration.tools`,
  });

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

      <Stack spacing={0.5}>
        <Typography variant="s1" fontWeight={"fontWeightMedium"}>
          Add Tool
        </Typography>
        <Typography variant="s2" fontWeight={"fontWeightRegular"}>
          If your application should have a function
        </Typography>
      </Stack>
      <Box sx={{ marginTop: -2 }}>
        <RadioField
          optionColor={"text.primary"}
          optionFontWeight={"fontWeightMedium"}
          optionDirection="row"
          parentSx={{ marginLeft: -2 }}
          fieldName={`promptConfig.${index}.configuration.toolChoice`}
          control={control}
          options={toolsChoiceOptions}
        />
        <ShowComponent
          condition={watchModelPromptConfiguration?.toolChoice === "required"}
        >
          <Box
            sx={{
              display: "flex",
              gap: 1,
              alignItems: "center",
              width: "100%",
              mt: 3,
            }}
          >
            <FormSearchSelectFieldState
              size="small"
              label="Select a tool"
              options={toolsOptions}
              placeholder="Select a tool"
              multiple
              checkbox
              value={watchedTools?.map((t) => t?.tool?.value) || []}
              sx={{ flex: 1 }}
              onChange={(e) => {
                const selectedValues = e.target.value; // array of selected ids
                const selectedTools = toolsOptions.filter((tool) =>
                  selectedValues.includes(tool.value),
                );
                const newFields = selectedTools.map((tool) => ({
                  id: getRandomId(),
                  tool,
                }));
                replace(newFields); // replace the entire field array
              }}
            />

            <Button
              size="medium"
              variant="outlined"
              startIcon={
                <SvgColor
                  sx={{
                    color: "text.secondary",
                    height: 20,
                    width: 20,
                  }}
                  src={"/assets/icons/ic_add.svg"}
                />
              }
              onClick={() => setIsAddToolModalOpen(true)}
              sx={{
                "& .MuiButton-icon > svg": { marginRight: 0 },
                minWidth: "unset",
                borderColor: "divider",
                height: 40,
                ml: "auto",
              }}
            >
              <Typography
                variant="s1"
                color={"text.primary"}
                fontWeight={"fontWeightMedium"}
              >
                Create tool
              </Typography>
            </Button>
          </Box>
        </ShowComponent>
      </Box>

      <CustomToolModal
        editTool={selectedTool}
        setEditTool={setSelectedTool}
        open={isAddToolModalOpen || Boolean(selectedTool)}
        onClose={() => {
          setIsAddToolModalOpen(false);
          setSelectedTool(null);
        }}
      />
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
