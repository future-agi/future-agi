/* eslint-disable react/prop-types */
import {
  Box,
  Divider,
  Typography,
  Checkbox,
  FormControlLabel,
  Stack,
} from "@mui/material";
import React, {
  useState,
  useEffect,
  forwardRef,
  useImperativeHandle,
} from "react";
import { useVoiceOptions } from "src/api/develop/develop-detail";
import { MODEL_TYPES } from "src/sections/develop-detail/RunPrompt/common";
import Image from "../image";
import _ from "lodash";
import CustomAudioDialog from "src/sections/develop-detail/CustomAudioDialog";
import PropTypes from "prop-types";
import { LabelButton } from "../FormSelectField/FormSelectFieldStyle";
import LoaderForCustomVoices from "./LoaderForCustomVoices";
import CustomTooltip from "../tooltip";

const EachCustomVoiceModel = forwardRef((props, ref) => {
  const { modelType = "tts", selectedModel, onCustomAudioDialogOpen } = props;

  const { data: voiceOptions, isLoading: loadingVoices } = useVoiceOptions({
    model: selectedModel?.value,
    enabled: !!selectedModel?.value && modelType === MODEL_TYPES.TTS,
  });

  const toVoiceString = (v) => (typeof v === "string" ? v : v?.voice);
  const [selectedVoices, setSelectedVoices] = useState(
    (selectedModel?.voices || []).map(toVoiceString),
  );
  const [customAudioOpen, setCustomAudioOpen] = useState(false);
  const [customVoices, setCustomVoices] = useState([]);

  useImperativeHandle(
    ref,
    () => ({
      getSelectedVoices: () => {
        // Build a map of voice string → promptConfigId from the original voices prop
        const existingMap = new Map(
          (selectedModel?.voices || []).map((v) =>
            typeof v === "string" ? [v, null] : [v.voice, v.promptConfigId],
          ),
        );
        return selectedVoices.map((voice) => ({
          voice,
          promptConfigId: existingMap.get(voice) ?? null,
        }));
      },
    }),
    [selectedVoices, selectedModel?.voices],
  );

  useEffect(() => {
    onCustomAudioDialogOpen?.(customAudioOpen);
  }, [customAudioOpen, onCustomAudioDialogOpen]);

  const handleCheckboxChange = (voiceValue) => {
    setSelectedVoices((prev) => {
      if (prev.length === 1 && prev.includes(voiceValue)) {
        return prev;
      }
      if (prev.includes(voiceValue)) {
        return prev.filter((v) => v !== voiceValue);
      } else {
        return [...prev, voiceValue];
      }
    });
  };

  const addNewVoice = (customId) => {
    if (!customId) return;
    setCustomVoices((prev) => {
      if (!prev.includes(customId)) {
        return [...prev, customId];
      }
      return prev;
    });
    setSelectedVoices((prev) => {
      if (!prev.includes(customId)) {
        return [...prev, customId];
      }
      return prev;
    });
  };

  const handleSuccessCustomAudio = (res) => {
    const customId = res?.data?.result?.id;
    if (!customId) return;
    addNewVoice(customId);
  };

  useEffect(() => {
    if (selectedVoices?.length === 0 && voiceOptions?.voices?.length > 0) {
      setSelectedVoices([voiceOptions.voices[0].value]);
    }
  }, [selectedVoices, voiceOptions]);

  const apiVoiceValues = new Set(
    (voiceOptions?.voices || []).map((v) => v.value),
  );
  const allVoices = [
    ...(voiceOptions?.voices || []),
    ...customVoices
      .filter((id) => !apiVoiceValues.has(id))
      .map((id) => ({ value: id, label: `Custom Voice (${id})` })),
  ];

  return (
    <Box
      sx={{
        width: "100%",
        height: "185px",
        overflowY: "auto",
        border: "1px solid",
        borderColor: "divider",
        p: 1,
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
          src={selectedModel?.logoUrl}
          alt={selectedModel?.model_name}
          width={20}
        />
        <Typography typography="s1" fontWeight="fontWeightMedium">
          {selectedModel?.value}
        </Typography>
      </Box>

      <Divider sx={{ my: 1 }} />
      {voiceOptions?.isCustomAudio && (
        <LabelButton
          onClick={() => setCustomAudioOpen(true)}
          sx={{
            typography: "s2_1",
            borderTop: "0",
            marginTop: "5px",
            borderRadius: "6px",
            height: "34px",
            backgroundColor: "background.neutral",
            paddingLeft: "8px",
            "&:hover": {
              backgroundColor: "action.hover",
            },
          }}
        >
          <Typography component="span" sx={{ fontSize: "20px" }}>
            +
          </Typography>{" "}
          &nbsp; Add Custom Voice
        </LabelButton>
      )}
      <Stack spacing={1}>
        {loadingVoices ? (
          <LoaderForCustomVoices />
        ) : allVoices.length > 0 ? (
          allVoices.map((voice) => (
            <CustomTooltip
              key={voice.value}
              title={
                selectedVoices.length === 1 &&
                selectedVoices.includes(voice.value)
                  ? "At least one voice must remain selected"
                  : ""
              }
              arrow
            >
              <span>
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={selectedVoices.includes(voice.value)}
                      onChange={() => handleCheckboxChange(voice.value)}
                      size="small"
                      sx={{ py: 0, ml: 0.5 }}
                      disabled={
                        selectedVoices.length === 1 &&
                        selectedVoices.includes(voice.value)
                      }
                    />
                  }
                  label={_.startCase(voice?.label) || _.startCase(voice.value)}
                  sx={{ "& .MuiFormControlLabel-label": { typography: "s1" } }}
                />
              </span>
            </CustomTooltip>
          ))
        ) : (
          <Typography variant="body2" color="text.secondary">
            No voices available
          </Typography>
        )}
      </Stack>
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
    </Box>
  );
});

EachCustomVoiceModel.displayName = "EachCustomVoiceModel";
export default EachCustomVoiceModel;

EachCustomVoiceModel.propTypes = {
  modelType: PropTypes.string,
  selectedModel: PropTypes.shape({
    value: PropTypes.string,
    logoUrl: PropTypes.string,
    modelName: PropTypes.string,
    voices: PropTypes.arrayOf(PropTypes.string),
    providers: PropTypes.arrayOf(PropTypes.string),
  }),
  onCustomAudioDialogOpen: PropTypes.func,
};
