import React, { useEffect, useRef, useState } from "react";
import { Box, Divider, IconButton, Typography, useTheme } from "@mui/material";
import CustomModelDropdown from "src/components/custom-model-dropdown/CustomModelDropdown";
import Iconify from "src/components/iconify";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import PropTypes from "prop-types";
import CustomModelTools from "src/components/custom-model-tools";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useParams } from "react-router";
import {
  modelConfigDefault,
  usePromptWorkbenchContext,
} from "../WorkbenchContext";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { usePromptStoreShallow } from "src/sections/workbench-v2/store/usePromptStore";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";
import { getModelParamValues, transformModelParams } from "./common";
import ModelParamsContainer from "./ModelParamsContainer";
import { MODEL_TYPES } from "src/sections/develop-detail/RunPrompt/common";
import logger from "src/utils/logger";
import { useModelParams } from "src/api/develop/prompt";
import { useVoiceOptions } from "src/api/develop/develop-detail";
import ResponseFormatSelector from "./ResponseFormatSelector";
import TemplateFormatSelector from "./TemplateFormatSelector";
import { ShowComponent } from "../../../../components/show";

const modelTypeByValueType = {
  chat: MODEL_TYPES.LLM,
  tts: MODEL_TYPES.TTS,
  stt: MODEL_TYPES.STT,
  image_generation: MODEL_TYPES.IMAGE,
};

const getOutputFormatFromModelType = (responseFormat) => {
  if (responseFormat === "tts") return "audio";
  if (responseFormat === "image_generation") return "image";
  return "string";
};

const ModelContainer = ({
  modelConfig,
  setModelConfig,
  open,
  setOpen,
  promptIndex,
}) => {
  const theme = useTheme();
  const { id } = useParams();
  const { selectedVersions, templateFormat, setTemplateFormat } =
    usePromptWorkbenchContext();
  const { role: userRole } = useAuthContext();
  const modelContainerRef = useRef(null);
  const { setSelectTemplateDrawerOpen, setSelectedPromptIndex } =
    usePromptStoreShallow((state) => ({
      setSelectTemplateDrawerOpen: state.setSelectTemplateDrawerOpen,
      setSelectedPromptIndex: state.setSelectedPromptIndex,
    }));

  const queryClient = useQueryClient();
  const [transformedModelParamsValue, setTransformedModelParamsValue] =
    useState(null);

  const handleUseTemplate = () => {
    trackEvent(Events.promptUseTemplateClicked, {
      [PropertyName.click]: true,
    });
    setSelectTemplateDrawerOpen(true);
    setSelectedPromptIndex(promptIndex);
  };

  const handleModelOnChange = async (e) => {
    const value = e.target.value;
    const currentModelType = modelConfig?.model_type ?? "all";
    const apiModelType = modelTypeByValueType[value?.type] ?? MODEL_TYPES.LLM;
    // Normalize camelCase/snake_case — dropdown emits either shape.
    const valueModelName = value?.modelName ?? value?.model_name;
    const valueProviders = value?.providers;

    try {
      const { data } = await queryClient.fetchQuery({
        queryKey: [
          "model-params",
          valueModelName,
          valueProviders,
          apiModelType,
        ],
        queryFn: () =>
          axios.get(endpoints.develop.modelParams, {
            params: {
              model: valueModelName,
              provider: valueProviders,
              model_type: apiModelType,
            },
          }),
      });
      const transformed = transformModelParams(data?.result);
      const transformedValue = getModelParamValues(
        transformed,
        modelConfig,
        valueModelName !== modelConfig?.model,
        value, // new model config
      );

      // Set appropriate default responseFormat based on model type
      let defaultResponseFormat = transformedValue.responseFormat;
      if (value?.type === "tts") {
        defaultResponseFormat = "text";
      } else if (value?.type === "image_generation") {
        defaultResponseFormat = "text";
      } else if (value?.type === "stt") {
        defaultResponseFormat = "text";
      }

      const outputFormat = getOutputFormatFromModelType(value?.type);

      setTransformedModelParamsValue({
        ...transformedValue,
        responseFormat: defaultResponseFormat,
      });
      setModelConfig(() => ({
        ...modelConfigDefault,
        model: valueModelName,
        model_detail: value,
        model_type: currentModelType,
        ...transformedValue,
        responseFormat: defaultResponseFormat,
        output_format: outputFormat,
      }));
    } catch (error) {
      logger.error("Error changing model", error);
      setModelConfig(() => ({
        ...modelConfigDefault,
        model: valueModelName,
        model_detail: value,
        model_type: currentModelType,
      }));
    }
  };

  const handleOnModelTypeChange = (newModelType) => {
    setModelConfig((pre) => {
      // If changing to "all", keep the current model
      if (newModelType === "all") {
        return { ...pre, model_type: newModelType };
      }
      // For any other change (from "all" or between types), reset to default
      return { ...modelConfigDefault, model_type: newModelType };
    });
  };

  const modelOptionChange = (values) => {
    setModelConfig((pre) => ({ ...pre, ...values }));
  };

  const { data: responseSchema } = useQuery({
    queryKey: ["response-schema"],
    queryFn: () => axios.get(endpoints.develop.runPrompt.responseSchema),
    select: (d) => d.data?.results,
    staleTime: 1 * 60 * 1000, // 1 min stale time
  });

  const handleApplyTool = (data) => {
    setModelConfig((pre) => ({
      ...pre,
      tools: data,
    }));
  };

  const handleResponseFormatChange = (value, modelType) => {
    if (
      modelType === "image_generation" ||
      modelType === "tts" ||
      modelType === "stt"
    ) {
      setModelConfig((pre) => ({
        ...pre,
        output_format: value === "text" ? "string" : value,
      }));
      return;
    }
    // Check if it's a custom schema (from responseSchema)
    const customSchema = responseSchema?.find((item) => item.id === value);
    if (customSchema) {
      setModelConfig((pre) => ({
        ...pre,
        responseFormat: customSchema,
      }));
    } else {
      setModelConfig((pre) => ({
        ...pre,
        responseFormat: value,
      }));
    }
  };

  // model_detail comes from the model dropdown in both camelCase
  // (modelName/providers) and snake_case (model_name) shapes depending on
  // the source — read both and normalize.
  const detail = modelConfig?.model_detail || {};
  const modelName = detail.modelName ?? detail.model_name;
  const { providers, type } = detail;

  const isTTSModel = type === "tts";
  const apiModelType = modelTypeByValueType[type] ?? MODEL_TYPES.LLM;
  const modelType = modelConfig?.model_type ?? "all";
  const { data: modelParams } = useModelParams(
    modelName,
    providers,
    apiModelType,
  );
  const transformedModelParams = transformModelParams(modelParams);

  const { data: voiceOptions } = useVoiceOptions({
    model: modelName,
    enabled: !!modelName && isTTSModel,
  });

  useEffect(() => {
    if (isTTSModel && voiceOptions?.default) {
      setModelConfig(
        (pre) => {
          if (pre?.voiceId) return pre;
          return { ...pre, voiceId: voiceOptions.default };
        },
        { skipSave: true },
      );
    }
    if (!isTTSModel) {
      setModelConfig(
        (pre) => {
          if (!pre?.voiceId) return pre;
          const { voiceId, ...rest } = pre;
          return rest;
        },
        { skipSave: true },
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isTTSModel, voiceOptions?.default]);

  return (
    <React.Fragment>
      <Box
        display="flex"
        gap={theme.spacing(1)}
        sx={{
          marginTop: "4px",
          alignItems: "center",
          flexWrap: "wrap",
          rowGap: theme.spacing(0.75),
        }}
      >
        <Box
          display={"flex"}
          flexDirection={"row"}
          borderRadius={0.5}
          gap={1}
          ref={modelContainerRef}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            height: "32px",
            display: "flex",
            alignItems: "center",
            minWidth: 0,
            maxWidth: "100%",
            flexShrink: 1,
            borderRadius: (theme) =>
              open ? theme.spacing(0.5, 0.5, 0, 0) : theme.spacing(0.5),
          }}
        >
          <CustomModelDropdown
            isModalContainer={true}
            openSelectModel={open}
            setOpenSelectModel={setOpen}
            hoverPlacement="bottom-start"
            buttonTitle="Select Model"
            disabledClick={
              !RolePermission.PROMPTS[PERMISSIONS.DELETE][userRole]
            }
            buttonIcon={
              <Iconify
                icon="radix-icons:box-model"
                width="16px"
                height="16px"
                sx={{
                  cursor: "pointer",
                  color: "text.primary",
                }}
              />
            }
            value={modelConfig.model}
            modelDetail={modelConfig.model_detail}
            onChange={handleModelOnChange}
            onModelTypeChange={handleOnModelTypeChange}
            onClick={() => {
              trackEvent(Events.promptSelectModelClicked, {
                [PropertyName.promptId]: id,
                [PropertyName.type]: "user",
                [PropertyName.version]:
                  selectedVersions?.[promptIndex]?.version,
              });
            }}
            modelContainerRef={modelContainerRef}
            extraParams={{ model_type: modelType }}
            modelType={modelType}
          />
          <Divider
            orientation="vertical"
            flexItem
            sx={{
              height: "20px",
              borderColor: "divider",
              marginBottom: 1,
              marginTop: "4px", // optional, centers it nicely in flex containers
            }}
          />
          <ModelParamsContainer
            initialModelParams={transformedModelParamsValue}
            transformedModelParams={transformedModelParams}
            modelConfig={modelConfig}
            responseSchema={responseSchema}
            modelOptionChange={modelOptionChange}
            promptIndex={promptIndex}
            selectedVersions={selectedVersions}
            userRole={userRole}
            voiceOptions={voiceOptions}
            disabled={!modelConfig?.model}
          />
        </Box>

        <Box sx={{ flexShrink: 0 }}>
          <CustomModelTools
            isModalContainer={true}
            handleApply={handleApplyTool}
            tools={modelConfig?.tools || []}
            disableClick={!RolePermission.PROMPTS[PERMISSIONS.DELETE][userRole]}
            disableHover={
              !modelConfig?.tools || modelConfig?.tools.length === 0
            }
            onClick={() => {
              trackEvent(Events.promptSelectToolsClicked, {
                [PropertyName.promptId]: id,
                [PropertyName.version]:
                  selectedVersions?.[promptIndex]?.version,
              });
            }}
          />
        </Box>
        <CustomTooltip
          show
          arrow
          size="small"
          title={
            <Typography typography={"s2"} fontWeight={"fontWeightRegular"}>
              Import template
            </Typography>
          }
        >
          <IconButton
            size="small"
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
              padding: "5px 10px",
              flexShrink: 0,
            }}
            onClick={handleUseTemplate}
          >
            <SvgColor
              sx={{
                height: 20,
                width: 20,
                color: "text.primary",
              }}
              src={"/assets/icons/ic_import.svg"}
            />
          </IconButton>
        </CustomTooltip>
        <ShowComponent condition={!!modelConfig?.model}>
          <ResponseFormatSelector
            modelType={type ?? "chat"}
            responseFormatOptions={transformedModelParams?.responseFormat}
            responseSchema={responseSchema}
            selectedValue={(() => {
              if (
                type === "image_generation" ||
                type === "tts" ||
                type === "stt"
              ) {
                return modelConfig?.output_format;
              }
              return typeof modelConfig?.responseFormat === "object"
                ? modelConfig?.responseFormat?.id
                : modelConfig?.responseFormat;
            })()}
            onChange={(value) => handleResponseFormatChange(value, type)}
            disabled={
              !modelConfig?.model ||
              !RolePermission.PROMPTS[PERMISSIONS.DELETE][userRole]
            }
          />
        </ShowComponent>
        <TemplateFormatSelector
          value={templateFormat}
          onChange={setTemplateFormat}
          disabled={!RolePermission.PROMPTS[PERMISSIONS.DELETE][userRole]}
        />
      </Box>
    </React.Fragment>
  );
};

ModelContainer.propTypes = {
  modelConfig: PropTypes.object,
  setModelConfig: PropTypes.func,
  open: PropTypes.bool,
  setOpen: PropTypes.func,
  promptIndex: PropTypes.number,
};

export default ModelContainer;
