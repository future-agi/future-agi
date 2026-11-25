import React, { useEffect, useState } from "react";
import {
  Box,
  Divider,
  IconButton,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import Image from "src/components/image";
import SvgColor from "../../../components/svg-color/svg-color";
import _ from "lodash";
import { LOGO_WITH_BLACK_BACKGROUND } from "src/components/custom-model-dropdown/common";
import { useVoiceOptions } from "src/api/develop/develop-detail";
import { useWatch } from "react-hook-form";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "../../../utils/axios";
import { ShowComponent } from "../../../components/show/ShowComponent";
import VoiceSelectorPopover from "./VoiceSelectorPopover";
import ModelParamsPopover from "./ModelParamsPopover";
import { MODEL_TYPES } from "../RunPrompt/common";
import {
  // getModelParamsDefaultValue,
  getModelParamsDisplayString,
} from "./common";
import CustomAudioDialog from "../CustomAudioDialog";
import {
  getReasoningFormValues,
  transformParameterType,
} from "../RunPrompt/common";
import { isUUID } from "src/utils/utils";

const getTextResponse = (list) => {
  return (
    list?.find((item) => item?.value === "text")?.value ?? list?.[0]?.value
  );
};

function getModelParamsDefaultValue(
  modelParams,
  currentModelParams,
  _selectedModel,
) {
  const transformedModelParamsSliders = modelParams?.sliders
    ?.map((item) => {
      // Exclude any items whose label is "logoUrl" or "providers"
      if (
        _.camelCase(item?.label) === "logoUrl" ||
        _.camelCase(item?.label) === "providers"
      ) {
        return null;
      }
      if (currentModelParams?.[_.camelCase(item?.label)] !== undefined) {
        return {
          ...item,
          id: item?.label,
          value: currentModelParams[_.camelCase(item?.label)] ?? item?.default,
        };
      }
      return {
        ...item,
        id: item?.label,
        value: item?.value ?? item?.default,
      };
    })
    ?.filter(Boolean);

  const sliders = transformedModelParamsSliders;
  const responseFormat = modelParams?.responseFormat;
  const booleans = transformParameterType(
    modelParams?.booleans,
    currentModelParams,
    "booleans",
  );
  const dropdowns = transformParameterType(
    modelParams?.dropdowns,
    currentModelParams,
    "dropdowns",
  );

  const result = {};

  // Only process sliders if they exist
  if (Array.isArray(sliders)) {
    sliders.forEach((param) => {
      // convert label to camelCase
      const key = _.camelCase(param.label);

      // use value if defined, else default if defined, else 0 (preserves null)
      const value =
        param?.value !== undefined
          ? param.value
          : param?.default !== undefined
            ? param.default
            : 0;

      // Override max_tokens for GPT-5 models

      result[key] = value;
    });
  }

  const fin = {
    ...result,
    ...(Array.isArray(booleans) &&
      booleans.length > 0 && {
        booleans: Object.fromEntries(
          booleans.map((item) => [
            item.label,
            item?.value !== undefined
              ? item.value
              : item?.default !== undefined
                ? item.default
                : false,
          ]),
        ),
      }),
    ...(Array.isArray(dropdowns) &&
      dropdowns.length > 0 && {
        dropdowns: Object.fromEntries(
          dropdowns.map((item) => [
            item.label,
            item?.value ?? item.default ?? item?.options?.[0],
          ]),
        ),
      }),
    ...(currentModelParams?.responseFormat
      ? { responseFormat: currentModelParams.responseFormat }
      : responseFormat?.[0]?.value
        ? {
            responseFormat: getTextResponse(responseFormat),
          }
        : {}),
  };

  if (modelParams?.reasoning) {
    fin.reasoning = getReasoningFormValues(
      modelParams.reasoning,
      currentModelParams?.reasoning,
    );
  }

  return fin;
}

export default function ModelFieldItem({
  selectedModel = {},
  voiceFieldName = "",
  control,
  modelIndex: _modelIndex,
  modelType,
  setValue,
  modelParamsFieldName,
  onRemove = () => {},
}) {
  const [voiceAnchorEl, setVoiceAnchorEl] = useState(null);
  const [modelAnchorEl, setModelAnchorEl] = useState(null);
  const [customAudioOpen, setCustomAudioOpen] = useState(false);
  const { data: voiceOptions, isLoading: loadingVoices } = useVoiceOptions({
    model: selectedModel?.value,
    enabled: modelType === MODEL_TYPES.TTS,
  });

  const {
    data: modelParams,
    isSuccess,
    isLoading: isLoadingModelParams,
  } = useQuery({
    queryKey: ["model-params", selectedModel?.value],
    queryFn: () =>
      axios.get(endpoints.develop.modelParams, {
        params: {
          model: selectedModel?.value,
          provider: selectedModel?.providers,
          model_type: modelType,
        },
      }),
    enabled: !!(selectedModel?.value && selectedModel?.providers && modelType),
    select: (d) => d.data?.result,
  });

  const currentVoiceValue = useWatch({
    control,
    name: voiceFieldName,
  });

  const currentModelParams = useWatch({
    control,
    name: modelParamsFieldName,
  });

  const getLabelFrom = (voice) => {
    return voiceOptions?.voices?.find((v) => v?.value === voice)?.label;
  };

  const voicesString = currentVoiceValue
    ?.map((voice) => {
      if (isUUID(voice)) {
        return getLabelFrom(voice) || voice;
      } else {
        return _.startCase(voice);
      }
    })
    ?.join(", ");

  const modelParamsDisplayString = getModelParamsDisplayString(modelParams);

  useEffect(() => {
    if (modelType !== MODEL_TYPES.TTS) return;
    if (voiceOptions?.default && !currentVoiceValue?.length) {
      const timeoutId = setTimeout(() => {
        setValue(voiceFieldName, [voiceOptions.default], {
          shouldDirty: true,
          shouldValidate: true,
          shouldTouch: true,
        });
      }, 0);

      return () => clearTimeout(timeoutId);
    }
  }, [voiceOptions, currentVoiceValue, voiceFieldName, setValue, modelType]);

  useEffect(() => {
    if (!modelParams || !isSuccess) return;

    const defaultValues = getModelParamsDefaultValue(
      modelParams,
      currentModelParams,
      selectedModel?.value,
    );
    setValue(modelParamsFieldName, defaultValues, {
      shouldDirty: true,
      shouldValidate: true,
      shouldTouch: true,
    });
  }, [
    modelParams,
    modelParamsFieldName,
    setValue,
    isSuccess,
    selectedModel?.value,
  ]);

  const addNewVoice = (newVoice) => {
    const existing = currentVoiceValue || [];
    setValue(voiceFieldName, [...existing, newVoice], {
      shouldValidate: true,
      shouldDirty: true,
    });
  };
  const handleSuccessCustomAudio = (res) => {
    const customId = res?.data?.result?.id;
    if (!customId) return;
    addNewVoice(customId);
  };
  return (
    <>
      <Box
        sx={{
          borderRadius: "4px",
          padding: (theme) => theme.spacing(0.75, 1.5),
          border: "1px solid",
          borderColor: "primary.dark",
          bgcolor: "action.hover",
          display: "flex",
          flexDirection: "column",
          gap: 1,
        }}
      >
        <Stack
          direction={"row"}
          alignItems={"center"}
          justifyContent={"space-between"}
        >
          <Stack direction={"row"} gap={0.75} alignItems={"center"}>
            <Image
              src={selectedModel?.logoUrl}
              alt={selectedModel?.value}
              width="16px"
              disableThemeFilter={
                !LOGO_WITH_BLACK_BACKGROUND.includes(
                  selectedModel?.providers?.toLowerCase(),
                )
              }
              sx={{ verticalAlign: "middle" }}
              flexShrink={0}
            />
            <Typography
              typography={"s1"}
              fontWeight={"fontWeightRegular"}
              color={"text.primary"}
            >
              {selectedModel?.value}
            </Typography>
          </Stack>
          <IconButton onClick={onRemove} size="small">
            <SvgColor
              sx={{
                bgcolor: "text.secondary",
                height: "16px",
                width: "16px",
              }}
              src={"/assets/icons/ic_close.svg"}
            />
          </IconButton>
        </Stack>

        <ShowComponent condition={modelType === MODEL_TYPES.TTS}>
          <Divider
            sx={{
              borderColor: "primary.light",
            }}
          />
          <Stack
            direction={"row"}
            alignItems={"center"}
            justifyContent={"space-between"}
          >
            <Stack
              direction={"row"}
              gap={0.75}
              alignItems={"center"}
              sx={{
                flex: 1,
              }}
            >
              <SvgColor
                sx={{
                  height: "12px",
                  width: "12px",
                  bgcolor: "text.primary",
                }}
                src="/assets/icons/ic_voice.svg"
              />
              {loadingVoices ? (
                <Skeleton
                  sx={{
                    bgcolor: "background.neutral",
                    borderRadius: "4px",
                  }}
                  variant="rounded"
                  width="100%"
                  height={16}
                />
              ) : (
                <Typography
                  typography={"s2"}
                  fontWeight={"fontWeightMedium"}
                  color={"text.primary"}
                >
                  {voicesString}
                </Typography>
              )}
            </Stack>
            <IconButton
              size="small"
              onClick={(e) => setVoiceAnchorEl(e.currentTarget)}
              sx={{
                color: "text.primary",
              }}
              disabled={loadingVoices}
            >
              <SvgColor
                sx={{
                  height: "16px",
                  width: "16px",
                }}
                src={"/assets/icons/custom/lucide--chevron-down.svg"}
              />
            </IconButton>
          </Stack>
        </ShowComponent>

        <ShowComponent
          condition={isLoadingModelParams || !!modelParamsDisplayString}
        >
          <Divider
            sx={{
              borderColor: "primary.light",
            }}
          />
          <Stack
            direction={"row"}
            alignItems={"center"}
            sx={{
              minWidth: 0,
            }}
          >
            <Stack
              sx={{
                maxWidth: "95%",
                minWidth: 0,
                flex: 1,
              }}
              direction={"row"}
              gap={0.75}
              alignItems={"center"}
            >
              <SvgColor
                sx={{
                  height: "12px",
                  width: "12px",
                  bgcolor: "text.primary",
                }}
                src="/assets/icons/ic_model_settings.svg"
              />
              {isLoadingModelParams ? (
                <Skeleton
                  sx={{
                    bgcolor: "background.neutral",
                    borderRadius: "4px",
                  }}
                  variant="rectangular"
                  width="100%"
                  height={16}
                />
              ) : (
                <Typography
                  typography={"s2"}
                  fontWeight={"fontWeightMedium"}
                  color={"text.primary"}
                  sx={{
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    maxWidth: "100%",
                    display: "block",
                  }}
                >
                  {modelParamsDisplayString}
                </Typography>
              )}
            </Stack>
            <IconButton
              disabled={isLoadingModelParams}
              size="small"
              onClick={(e) => setModelAnchorEl(e.currentTarget)}
              sx={{
                ml: "auto",
                color: "text.primary",
              }}
            >
              <SvgColor
                sx={{
                  height: "16px",
                  width: "16px",
                }}
                src={"/assets/icons/custom/lucide--chevron-down.svg"}
              />
            </IconButton>
          </Stack>
        </ShowComponent>
      </Box>

      {/* Custom Audio Dialog */}
      <CustomAudioDialog
        open={customAudioOpen}
        onClose={() => {
          setCustomAudioOpen(false);
        }}
        selectedModel={{
          value: selectedModel?.value,
          providers: selectedModel?.providers,
        }}
        onSuccess={handleSuccessCustomAudio}
      />

      {/* Voice Selector Popover */}
      {Boolean(voiceAnchorEl) && (
        <VoiceSelectorPopover
          anchorEl={voiceAnchorEl}
          setAnchorEl={setVoiceAnchorEl}
          control={control}
          isCustomAudio={voiceOptions?.isCustomAudio}
          fieldName={voiceFieldName}
          openModal={() => {
            if (customAudioOpen) {
              return;
            }
            setVoiceAnchorEl(null);
            setCustomAudioOpen(true);
          }}
          options={voiceOptions?.voices}
          setValue={setValue}
          currentValue={currentVoiceValue}
        />
      )}

      {/* Model Params Popover */}
      {Boolean(modelAnchorEl) && (
        <ModelParamsPopover
          anchorEl={modelAnchorEl}
          setAnchorEl={setModelAnchorEl}
          control={control}
          modelParams={modelParams}
          fieldName={modelParamsFieldName}
        />
      )}
    </>
  );
}

ModelFieldItem.propTypes = {
  selectedModel: PropTypes.object,
  voiceFieldName: PropTypes.string,
  control: PropTypes.object,
  modelIndex: PropTypes.number,
  modelType: PropTypes.string,
  setValue: PropTypes.func,
  modelParamsFieldName: PropTypes.string,
  onRemove: PropTypes.func,
};
