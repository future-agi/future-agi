import { Box, IconButton, Stack, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import { useFieldArray, useWatch } from "react-hook-form";
import { getUniqueColorPalette } from "src/utils/utils";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import ImportPromptBtn from "../RunPrompt/Components/ImportPromptBtn";
import SvgColor from "src/components/svg-color";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../AccordianElements";
import { findInvalidVariables, MODEL_TYPES } from "../RunPrompt/common";
import CustomModelDropdownControl from "src/components/custom-model-dropdown/CustomModelDropdownControl";
import ModelFieldItem from "./ModelFieldItem";
import ChooseModelType from "../RunPrompt/Components/ChooseModelType";
import { useRunPromptOptions } from "src/api/develop/develop-detail";
import { DefaultMessages } from "../../workbench/constant";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import InputSection from "../../common/SliderRow/InputSection";
import ConfigTool from "src/sections/develop-detail/RunPrompt/ToolConfig/ConfigTool";
import { ShowComponent } from "../../../components/show";
import LLMPrompt from "./components/LLMPrompt";
import PromptSTTInput from "../RunPrompt/Components/PromptSTTInput";
import PromptTTSInput from "../RunPrompt/Components/PromptTTSInput";
import { escapeModelKey } from "./utils";

const PromptTemplateCard = ({
  control,
  index,
  allColumns,
  jsonSchemas = {},
  derivedVariables = {},
  onRemove,
  handleOpenImportPrompt,
  promptImported,
  handleResetImportedPrompt,
  onGeneratePrompt,
  onImprovePrompt,
  setValue,
  getValues,
  clearErrors,
  unregister,
  errors,
  watch,
}) => {
  const { tagBackground, tagForeground } = getUniqueColorPalette(index);
  const { data: runPromptOptions } = useRunPromptOptions();
  const theme = useTheme();
  const [expanded, setExpanded] = useState(true);
  const { append } = useFieldArray({
    control,
    name: `promptConfig.${index}.messages`,
  });

  const watchedMessages = useWatch({
    control,
    name: `promptConfig.${index}.messages`,
  });

  const watchedModels = useWatch({
    control,
    name: `promptConfig.${index}.model`,
  });
  const modelTypePath = `promptConfig.${index}.modelType`;
  const watchedModelType = useWatch({
    control,
    name: modelTypePath,
  });
  const watchedOutputFormat = watchedModelType === "tts" ? "audio" : "string";

  const allInvalidVariables = useMemo(() => {
    const invalids = [];

    watchedMessages?.forEach(({ content }) => {
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    JSON.stringify(watchedMessages),
    allColumns,
    jsonSchemas,
    derivedVariables,
  ]);

  const handleRemoveModel = (modelIndex) => {
    const currentModels = getValues(`promptConfig.${index}.model`) || [];
    if (currentModels.length > modelIndex) {
      const updatedModels = currentModels.filter((_, i) => i !== modelIndex);
      setValue(`promptConfig.${index}.model`, updatedModels);
    }
    clearErrors(`promptConfig.${index}.voice`);
  };

  const handleRemoveMessage = (messageIndex) => {
    const currentMessages = getValues(`promptConfig.${index}.messages`) || [];
    if (currentMessages.length > messageIndex) {
      const updatedMessages = currentMessages.filter(
        (_, i) => i !== messageIndex,
      );
      setValue(`promptConfig.${index}.messages`, updatedMessages);
    }
  };

  useEffect(() => {
    if (watchedOutputFormat !== "audio") {
      setValue(`promptConfig.${index}.voice`, []);
      return;
    }

    const currentVoiceConfig = getValues(`promptConfig.${index}.voice`) || [];

    // Create a new list of voice configs, one per model (no duplicates)
    const updatedVoiceConfig =
      watchedModels?.map((model) => {
        // Check if this model already exists in the current config
        const existing = currentVoiceConfig.find(
          (v) => v.model === model?.value,
        );

        return (
          existing || {
            model: model?.value,
            voices: [], // initialize empty voices if new
          }
        );
      }) || [];

    // Only update if changed (avoids unnecessary renders)
    setValue(`promptConfig.${index}.voice`, updatedVoiceConfig, {
      shouldDirty: false,
    });
  }, [watchedModels, watchedOutputFormat, index, getValues, setValue]);

  return (
    <Accordion
      expanded={expanded}
      onChange={(e, isExpanded) => setExpanded(isExpanded)}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        padding: "16px",
        pt: 2,
        display: "flex",
        flexDirection: "column",
        gap: expanded ? 2 : 0,
      }}
    >
      <AccordionSummary
        sx={{
          flexDirection: "row",
          minHeight: "unset",
          padding: 0,
          "& .MuiAccordionSummary-content": {
            padding: 0,
            margin: 0,
          },
          "& .MuiAccordionSummary-expandIconWrapper": {
            transform: "rotate(90deg)",
            marginLeft: theme.spacing(2),
            "& > svg": {
              color: "text.primary",
            },
          },
          "& .MuiAccordionSummary-expandIconWrapper.Mui-expanded": {
            transform: "rotate(270deg)",
          },
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "row",
            alignItems: "center",
            justifyContent: "space-between",
            width: "100%",
          }}
        >
          <Stack direction={"row"} alignItems={"center"} gap={theme.spacing(1)}>
            <Box
              sx={{
                width: theme.spacing(3),
                height: theme.spacing(3),
                borderRadius: theme.spacing(0.5),
                backgroundColor: tagBackground,
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
              }}
            >
              <Typography
                typography={"s2"}
                fontWeight={"fontWeightMedium"}
                color={tagForeground}
              >
                {index + 1}
              </Typography>
            </Box>
            <Typography
              variant="s1"
              fontWeight={"fontWeightMedium"}
              color="text.primary"
            >
              Prompt Template {index + 1}
            </Typography>
          </Stack>
          <Stack direction={"row"} gap={theme.spacing(1)}>
            {expanded && (
              <ImportPromptBtn
                promptImportred={promptImported}
                onClick={(e) => {
                  e.stopPropagation();
                  if (promptImported) {
                    // reset data
                    handleResetImportedPrompt();
                  } else {
                    handleOpenImportPrompt();
                  }
                }}
              />
            )}
            {index > 0 && (
              <IconButton size="small" onClick={onRemove}>
                <SvgColor
                  sx={{
                    height: "20px",
                    width: "20px",
                    color: "text.primary",
                  }}
                  src={"/assets/icons/ic_delete.svg"}
                />
              </IconButton>
            )}
          </Stack>
        </Box>
      </AccordionSummary>
      <AccordionDetails
        sx={{
          padding: 0,
          margin: 0,
          marginTop: "16px",
        }}
      >
        <Box
          sx={{
            display: "flex",
            gap: theme.spacing(2),
            flexDirection: "column",
          }}
        >
          <FormTextFieldV2
            onClick={() => {
              if (promptImported) {
                handleOpenImportPrompt();
              }
            }}
            control={control}
            fieldName={`promptConfig.${index}.name`}
            label="Prompt Name"
            fullWidth
            placeholder={`Enter prompt name`}
            required
            size="small"
            helperText={undefined}
            defaultValue={undefined}
            onBlur={undefined}
            InputProps={{
              readOnly: promptImported,
              sx: {
                input: {
                  cursor: promptImported ? "pointer" : "text",
                },
              },
            }}
            sx={{
              "& .MuiFormHelperText-root": {
                margin: 0,
                marginTop: "4px",
              },
            }}
          />
          {promptImported && (
            <FormTextFieldV2
              onClick={() => {
                if (promptImported) {
                  handleOpenImportPrompt();
                }
              }}
              control={control}
              fieldName={`promptConfig.${index}.version`}
              label="Prompt Version"
              fullWidth
              size="small"
              helperText={undefined}
              defaultValue={undefined}
              onBlur={undefined}
              InputProps={{
                readOnly: true,
                sx: {
                  input: {
                    cursor: "pointer",
                  },
                },
              }}
            />
          )}

          <ChooseModelType
            label="Choose a model type to run your experiment "
            control={control}
            fieldName={modelTypePath}
            onChange={() => {
              setValue(`promptConfig.${index}.modelParams`, {});
              setValue(`promptConfig.${index}.voice`, []);
              setValue(`promptConfig.${index}.model`, []);
              setValue(`promptConfig.${index}.messages`, DefaultMessages);
            }}
          />
          <Stack direction={"column"} gap={theme.spacing(2)}>
            <Stack gap={0.5}>
              <CustomModelDropdownControl
                control={control}
                fieldName="model"
                modelObjectKey={`model`}
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
                hideCreateLabel={
                  watchedModelType === MODEL_TYPES.STT ||
                  watchedModelType === MODEL_TYPES.TTS
                }
                showButtons={true}
                extraParams={{ model_type: watchedModelType }}
              />
              <Typography
                typography={"s3"}
                fontWeight={"fontWeightMedium"}
                color={"text.secondary"}
              >
                Choose from a list of LLM models or create your own custom
                models for running prompt
              </Typography>
            </Stack>
          </Stack>

          {watchedModels?.map((model, modelIndex) => (
            <ModelFieldItem
              key={model?.value}
              selectedModel={model}
              modelIndex={modelIndex}
              index={index}
              control={control}
              setValue={setValue}
              useWatch={useWatch}
              voiceFieldName={`promptConfig.${index}.voice.${modelIndex}.voices`}
              modelParamsFieldName={`promptConfig.${index}.modelParams.${escapeModelKey(model?.value)}`}
              modelType={watchedModelType}
              onRemove={() => {
                handleRemoveModel(modelIndex);
                unregister(
                  `promptConfig.${index}.modelParams.${escapeModelKey(model?.value)}`,
                );
              }}
            />
          ))}

          <ShowComponent condition={watchedModelType === MODEL_TYPES.LLM}>
            <LLMPrompt
              append={append}
              handleRemoveMessage={handleRemoveMessage}
              index={index}
              watchedMessages={watchedMessages}
              allColumns={allColumns}
              jsonSchemas={jsonSchemas}
              derivedVariables={derivedVariables}
              allInvalidVariables={allInvalidVariables}
              control={control}
              onGeneratePrompt={onGeneratePrompt}
              onImprovePrompt={onImprovePrompt}
              errors={errors}
              clearErrors={clearErrors}
            />
          </ShowComponent>
          <ShowComponent
            condition={
              watchedModelType === MODEL_TYPES.TTS ||
              watchedModelType === MODEL_TYPES.IMAGE
            }
          >
            <PromptTTSInput
              allColumns={allColumns}
              jsonSchemas={jsonSchemas}
              derivedVariables={derivedVariables}
              allInvalidVariables={allInvalidVariables}
              setValue={setValue}
              fieldPrefix={`promptConfig.${index}.messages`}
              clearErrors={clearErrors}
              errors={errors}
              watch={watch}
              control={control}
            />
          </ShowComponent>
          <ShowComponent condition={watchedModelType === MODEL_TYPES.STT}>
            <PromptSTTInput
              allColumns={allColumns}
              control={control}
              getValues={getValues}
              setValue={setValue}
              messageFieldPrefix={`promptConfig.${index}.messages`}
              fieldPrefix={`promptConfig.${index}.voiceInputColumn`}
            />
          </ShowComponent>

          <Box sx={{ marginBottom: "10px" }}>
            <Box
              sx={{
                display: "flex",
                gap: 2,
                flexDirection: "column",
                position: "relative",
              }}
            >
              <InputSection
                label="Tool Choice"
                icon="/assets/icons/ic_info.svg"
                tooltipText="If your application should call a function"
              >
                <FormSearchSelectFieldControl
                  control={control}
                  fieldName={`promptConfig.${index}.configuration.toolChoice`}
                  size="small"
                  showClear={false}
                  sx={{
                    width: theme.spacing(148 / 8),
                    "& .MuiSelect-select": {
                      padding: theme.spacing(0.5, 1),
                    },
                    borderColor: "text.primary",
                  }}
                  options={[
                    ...(runPromptOptions?.tool_choices || []),
                    { value: "none", label: "None" },
                  ]}
                />
              </InputSection>
              <ConfigTool
                control={control}
                fieldName={`promptConfig.${index}.configuration.tools`}
              />
            </Box>
          </Box>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};

PromptTemplateCard.propTypes = {
  control: PropTypes.object,
  field: PropTypes.object,
  index: PropTypes.number,
  allColumns: PropTypes.array,
  jsonSchemas: PropTypes.object,
  derivedVariables: PropTypes.object,
  onRemove: PropTypes.func,
  handleOpenImportPrompt: PropTypes.func,
  promptImported: PropTypes.bool,
  handleResetImportedPrompt: PropTypes.func,
  promptVersion: PropTypes.string,
  onGeneratePrompt: PropTypes.func,
  onImprovePrompt: PropTypes.func,
  setValue: PropTypes.func,
  getValues: PropTypes.func,
  responseSchema: PropTypes.array,
  clearErrors: PropTypes.func,
  unregister: PropTypes.func,
  errors: PropTypes.object,
  watch: PropTypes.func,
};

export default PromptTemplateCard;
