import {
  Box,
  IconButton,
  Typography,
  Stack,
  Divider,
  Skeleton,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import Image from "src/components/image";
import SvgColor from "src/components/svg-color";
import axios, { endpoints } from "src/utils/axios";
import { MODEL_TYPES } from "../../RunPrompt/common";
import _ from "lodash";
import ModelParamsPopover from "../ModelParamsPopover";
import { ShowComponent } from "src/components/show";
import { useVoiceOptions } from "src/api/develop/develop-detail";
import { getLabelFrom, getModelParamsDefaultValue } from "../common";
import CustomAudioDialog from "../../CustomAudioDialog";
import VoiceSelectorPopover from "../VoiceSelectorPopover";
import CustomModelDropdown from "src/components/custom-model-dropdown/CustomModelDropdown";
import Iconify from "src/components/iconify";
import { getRandomId } from "src/utils/utils";
import { isUUID } from "src/utils/utils";
const NewModelRenderWithParamsTool = ({
  selectedModel,
  modelIndex: _modelIndex,
  index,
  control,
  setValue,
  useWatch,
  voiceFieldName = null,
  modelParamsFieldName,
  modelType,
  onRemove,
  showTool: _showTool = false,
  getValues: _getValues,
  deleteIcon = null,
  customBgColor = "background.paper",
}) => {
  const [modelAnchorEl, setModelAnchorEl] = useState(null);
  const [voiceAnchorEl, setVoiceAnchorEl] = useState(null);
  const [customAudioOpen, setCustomAudioOpen] = useState(false);
  const { data: voiceOptions, isLoading: loadingVoices } = useVoiceOptions({
    model: selectedModel?.value,
    enabled: modelType === MODEL_TYPES.TTS,
  });
  const [open, setOpen] = useState(false);
  const modelContainerRef = useRef(null);
  const {
    data: modelParams,
    isSuccess,
    isLoading: _isLoadingModelParams,
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
  const currentModelParams = useWatch({
    control,
    name: modelParamsFieldName,
  });

  useEffect(() => {
    if (!modelParams || !isSuccess) return;

    if (currentModelParams && Object.keys(currentModelParams).length > 0) {
      return;
    }

    const defaultValues = getModelParamsDefaultValue(
      modelParams,
      currentModelParams,
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
    currentModelParams,
  ]);
  const addNewVoice = (newVoice) => {
    const existing = selectedModel?.voices || [];
    setValue(
      voiceFieldName,
      [...existing, { voice: newVoice, promptConfigId: null }],
      { shouldValidate: true, shouldDirty: true },
    );
  };
  const handleSuccessCustomAudio = (res) => {
    const customId = res?.data?.result?.id;
    if (!customId) return;
    addNewVoice(customId);
  };
  const voicesString = selectedModel?.voices
    ?.map((voiceObj) => {
      const voice = typeof voiceObj === "string" ? voiceObj : voiceObj.voice;
      if (isUUID(voice)) {
        return getLabelFrom(voiceOptions, voice) || voice;
      } else {
        return _.startCase(voice);
      }
    })
    ?.join(", ");
  const handleModelChange = (e) => {
    const selectedModels = e.target.value;

    // Handle both single and multiple selections
    const modelsArray = Array.isArray(selectedModels)
      ? selectedModels
      : [selectedModels];

    // Map each selected model to form structure
    const formattedModels = modelsArray.map((model) => ({
      id: getRandomId(),
      value: model?.model_name || model?.value,
      ...model,
    }));

    setValue(`promptConfig.${index}.model`, formattedModels);
  };
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1,
        border: "1px solid",
        backgroundColor: customBgColor,
        borderColor: "divider",
        padding: 1.5,
        py: 1,
        borderRadius: 0.5,
      }}
      ref={modelContainerRef}
    >
      {!selectedModel?.value ? (
        <CustomModelDropdown
          isModalContainer={true}
          openSelectModel={open}
          setOpenSelectModel={setOpen}
          hoverPlacement="bottom-start"
          buttonTitle="Select Model"
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
          multiple={true}
          showButtons={true}
          value={selectedModel?.value || null}
          modelDetail={selectedModel?.modelDetail}
          onChange={handleModelChange}
          modelContainerRef={modelContainerRef}
          extraParams={{ model_type: "llm" }}
        />
      ) : (
        <Box
          sx={{
            display: "flex",
            flexDirection: "row",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Box
            sx={{
              display: "flex",
              flexDirection: "row",
              alignItems: "center",
              gap: 1,
            }}
          >
            <Image
              ratio="1/1"
              src={selectedModel?.logoUrl ?? selectedModel?.logo_url}
              alt={selectedModel?.model_name}
              flexShrink={0}
              style={{
                width: "16px",
                height: "16px",
              }}
            />
            <Typography typography="s1" fontWeight="fontWeightMedium">
              {selectedModel?.value}
            </Typography>
          </Box>
          <Box
            sx={{
              display: "flex",
              flexDirection: "row",
              alignItems: "center",
              gap: 1,
            }}
          >
            {currentModelParams && (
              <IconButton
                size="small"
                onClick={(e) => setModelAnchorEl(e.currentTarget)}
                sx={{
                  width: 22,
                  height: 22,
                  padding: "3px",
                  borderRadius: "2px",
                  backgroundColor: "background.default",
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <SvgColor
                  sx={{
                    height: "20px",
                    width: "20px",
                    color: "text.primary",
                  }}
                  src="/assets/prompt/slider-options.svg"
                />
              </IconButton>
            )}

            {onRemove && (
              <IconButton
                size="small"
                onClick={onRemove}
                sx={{
                  width: 22,
                  height: 22,
                  padding: "3px",
                  borderRadius: "2px",
                  backgroundColor: "background.default",
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <SvgColor
                  sx={{
                    height: "20px",
                    width: "20px",
                    color: deleteIcon ? "text.primary" : "error.main",
                  }}
                  src={deleteIcon ?? "/assets/icons/ic_delete.svg"}
                />
              </IconButton>
            )}
          </Box>
        </Box>
      )}

      <ShowComponent condition={modelType === MODEL_TYPES.TTS}>
        <Divider />
        <Typography typography={"s2"} fontWeight={"fontWeightMedium"}>
          Selected voice
        </Typography>

        <Stack direction={"row"} gap={0.75} alignItems={"center"}>
          {loadingVoices ? (
            <Skeleton
              sx={{
                bgcolor: "background.paper",
                borderRadius: 0.5,
              }}
              variant="rounded"
              width="100%"
              height={16}
            />
          ) : (
            <Box
              sx={{
                border: "1px solid",
                display: "flex",
                flexDirection: "row",
                alignItems: "center",
                justifyContent: "space-between",
                borderColor: "divider",
                backgroundColor: "background.paper",
                padding: "4px 6px",
                borderRadius: 0.5,
                width: "100%",
              }}
            >
              <Typography
                typography={"s2_1"}
                fontWeight={"fontWeightRegular"}
                sx={{
                  flex: 1,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {voicesString}
              </Typography>

              <ShowComponent condition={!loadingVoices}>
                <IconButton
                  size="small"
                  onClick={(e) => setVoiceAnchorEl(e.currentTarget)}
                  sx={{
                    color: "text.primary",
                    alignItems: "flex-end",
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
              </ShowComponent>
            </Box>
          )}
        </Stack>
      </ShowComponent>

      {Boolean(modelAnchorEl) && (
        <ModelParamsPopover
          anchorEl={modelAnchorEl}
          setAnchorEl={setModelAnchorEl}
          control={control}
          modelParams={modelParams}
          fieldName={modelParamsFieldName}
        />
      )}
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
          currentValue={selectedModel?.voices || []}
        />
      )}
    </Box>
  );
};

NewModelRenderWithParamsTool.propTypes = {
  selectedModel: PropTypes.object,
  modelIndex: PropTypes.number,
  index: PropTypes.number,
  control: PropTypes.object,
  setValue: PropTypes.func,
  useWatch: PropTypes.func,
  voiceFieldName: PropTypes.string,
  modelParamsFieldName: PropTypes.string,
  modelType: PropTypes.string,
  onRemove: PropTypes.func,
  showTool: PropTypes.bool,
  deleteIcon: PropTypes.string,
  getValues: PropTypes.func,
  customBgColor: PropTypes.string,
};

export default NewModelRenderWithParamsTool;
