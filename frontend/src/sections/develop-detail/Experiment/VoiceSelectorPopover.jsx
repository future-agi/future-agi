import {
  Button,
  Checkbox,
  FormControlLabel,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import _ from "lodash";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { LabelButton } from "src/components/FormSelectField/FormSelectFieldStyle";

export default function VoiceSelectorPopover({
  anchorEl,
  setAnchorEl,
  fieldName,
  options,
  setValue,
  currentValue = [],
  isCustomAudio = false,
  openModal,
}) {
  // Work with voice strings internally for selection UI
  const toVoiceString = (v) => (typeof v === "string" ? v : v.voice);
  const [selectedValues, setSelectedValues] = useState(
    currentValue.map(toVoiceString),
  );

  const handleCheckboxChange = (value) => {
    setSelectedValues((prev) =>
      prev.includes(value)
        ? prev.filter((item) => item !== value)
        : [...prev, value],
    );
  };

  const handleAdd = () => {
    // Preserve existing promptConfigId; new voices get null
    const existingMap = new Map(
      currentValue.map((v) => [
        toVoiceString(v),
        typeof v === "string" ? null : v.promptConfigId,
      ]),
    );
    const voiceObjects = selectedValues.map((voice) => ({
      voice,
      promptConfigId: existingMap.get(voice) ?? null,
    }));
    setValue(fieldName, voiceObjects);
    setAnchorEl(null);
  };

  return (
    <Popover
      open={Boolean(anchorEl)}
      anchorEl={anchorEl}
      onClose={() => setAnchorEl(null)}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      PaperProps={{
        sx: {
          p: 1,
          width: 200,
          borderRadius: "4px",
        },
      }}
    >
      <Typography
        typography="s2_1"
        fontWeight={"fontWeightMedium"}
        color={"text.primary"}
      >
        Select voice
      </Typography>
      <Stack
        gap={0.25}
        sx={{
          maxHeight: "200px",
          overflow: "scroll",
        }}
      >
        {isCustomAudio && (
          <LabelButton
            onClick={openModal}
            sx={{
              typography: "s2_1",
              borderTop: "0",
              marginTop: "5px",
              borderRadius: "6px",
              height: "34px",
              backgroundColor: "background.paper",
              paddingLeft: "8px",
              "&:hover": {
                backgroundColor: "action.hover",
              },
            }}
          >
            <Typography type="span" sx={{ fontSize: "20px" }}>
              +
            </Typography>{" "}
            &nbsp; Add Custom Voice
          </LabelButton>
        )}
        {options?.map((option) => (
          <FormControlLabel
            key={option?.value}
            control={
              <Checkbox
                checked={selectedValues.includes(option?.value)}
                onChange={() => handleCheckboxChange(option?.value)}
                disabled={
                  selectedValues.length === 1 &&
                  selectedValues.includes(option?.value)
                }
                size="small"
                sx={{
                  py: 0,
                  ml: 0.5,
                  "& .MuiSvgIcon-root": {
                    width: 16,
                    height: 16,
                  },
                }}
              />
            }
            label={_.startCase(option?.label) || _.startCase(option?.value)}
            sx={{
              "& .MuiFormControlLabel-label": {
                typography: "s2_1",
                fontWeight: "fontWeightRegular",
                color: "text.primary",
              },
            }}
          />
        ))}
      </Stack>

      <Button
        size="small"
        color="primary"
        variant="contained"
        fullWidth
        sx={{ mt: 2 }}
        onClick={handleAdd}
        disabled={!selectedValues?.length}
      >
        Add
      </Button>
    </Popover>
  );
}

VoiceSelectorPopover.propTypes = {
  anchorEl: PropTypes.any,
  setAnchorEl: PropTypes.func,
  fieldName: PropTypes.string,
  options: PropTypes.array,
  setValue: PropTypes.func,
  currentValue: PropTypes.array,
  isCustomAudio: PropTypes.bool,
  openModal: PropTypes.func,
};
