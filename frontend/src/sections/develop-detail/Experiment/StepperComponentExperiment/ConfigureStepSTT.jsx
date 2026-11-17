import { Box, Grid, Stack, Typography } from "@mui/material";
import React from "react";
import PromptSTTInput from "../../RunPrompt/Components/PromptSTTInput";
import CustomModelDropdownControl from "src/components/custom-model-dropdown/CustomModelDropdownControl";
import PropTypes from "prop-types";
import { useWatch } from "react-hook-form";
import NewModelRenderWithParamsTool from "./NewModelRenderWithParamsTool";

const ConfigureStepSTT = ({
  allColumns,
  control,
  getValues,
  setValue,
  unregister,
}) => {
  const watchModelType = getValues("experimentType");
  const watchModels = useWatch({
    control,
    name: "promptConfig.0.model",
  });
  const handleRemoveModel = (index) => {
    const currentModels = getValues(`promptConfig.0.model`) || [];
    if (currentModels.length > index) {
      const updatedModels = currentModels.filter((_, i) => i !== index);
      setValue(`promptConfig.0.model`, updatedModels);
    }
  };

  return (
    <Stack spacing={2}>
      <PromptSTTInput
        allColumns={allColumns}
        control={control}
        getValues={getValues}
        setValue={setValue}
        messageFieldPrefix={`promptConfig.0.messages`}
        fieldPrefix={`promptConfig.0.voiceInputColumnId`}
      />
      <Box>
        <Typography
          sx={{ display: "flex", flexDirection: "row", alignItems: "center" }}
          typography="s1"
          fontWeight={"fontWeightMedium"}
        >
          Select Models
          <Typography type="span" sx={{ color: "status.danger.fg" }}>
            *
          </Typography>
        </Typography>
        <Typography
          typography={"s2"}
          fontWeight={"fontWeightRegular"}
          color="text.secondary"
        >
          Choose one or more models to compare in your experiment
        </Typography>
      </Box>
      <CustomModelDropdownControl
        control={control}
        fieldName="model"
        modelObjectKey={`model`}
        fieldPrefix={`promptConfig.0`}
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
        hideCreateLabel={true}
        showButtons={true}
        extraParams={{ model_type: watchModelType }}
      />

      <Grid container spacing={2} columns={2}>
        {Array.isArray(watchModels) &&
          watchModels.length > 0 &&
          watchModels.map((model, index) => (
            <Grid item xs={1} key={model.value}>
              <NewModelRenderWithParamsTool
                selectedModel={model}
                modelIndex={index}
                index={index}
                control={control}
                setValue={setValue}
                useWatch={useWatch}
                modelParamsFieldName={`promptConfig.0.modelParams.${model.value}`}
                modelType={watchModelType}
                onRemove={() => {
                  handleRemoveModel(index);
                  unregister(`promptConfig.0.modelParams.${model?.value}`);
                }}
                customBgColor="background.neutral"
              />
            </Grid>
          ))}
      </Grid>
    </Stack>
  );
};

export default ConfigureStepSTT;

ConfigureStepSTT.propTypes = {
  allColumns: PropTypes.array,
  control: PropTypes.object,
  getValues: PropTypes.func,
  setValue: PropTypes.func,
  unregister: PropTypes.func,
};
