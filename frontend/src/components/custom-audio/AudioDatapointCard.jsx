import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import logger from "src/utils/logger";
import SvgColor from "src/components/svg-color";
import TestAudioPlayer from "./TestAudioPlayer";

const AudioDatapointCard = ({ value, column }) => {
  const cellValue = value?.cell_value;
  const memoizedAudioData = useMemo(() => {
    try {
      if (cellValue) {
        return {
          url: cellValue,
          fileName: "",
          fileType: "",
          size: "",
        };
      }
      return null;
    } catch (error) {
      logger.error("Failed to memoize audio data:", error);
      return null;
    }
  }, [cellValue]);

  return (
    <Accordion defaultExpanded disableGutters>
      <AccordionSummary
        aria-label={`Expand ${column?.headerName}`}
        sx={{
          flexDirection: "row",
          paddingLeft: 1,
          paddingRight: 2,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <SvgColor
            src={`/assets/icons/action_buttons/ic_audio.svg`}
            sx={{
              width: 20,
              height: 20,
              color: "text.secondary",
              flexShrink: 0,
            }}
          />
          <Typography
            variant="s1"
            color="text.disabled"
            fontWeight="fontWeightMedium"
          >
            {column?.headerName ?? column?.name}
          </Typography>
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ px: 2, pb: 1 }}>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            mb: 1,
          }}
        >
          <Box
            sx={{
              px: 2,
              py: 1.5,
              backgroundColor: "background.default",
              borderRadius: "6px",
            }}
          >
            {value?.status === "error" ? (
              <Typography
                sx={{
                  typography: "s2_1",
                  color: "red.500",
                  fontWeight: "fontWeightMedium",
                }}
              >
                {value?.value_infos?.reason ||
                  "An unexpected error occurred. Please try again."}
              </Typography>
            ) : (
              <TestAudioPlayer
                key={memoizedAudioData?.url}
                audioData={memoizedAudioData}
              />
            )}
          </Box>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};

AudioDatapointCard.propTypes = {
  value: PropTypes.object,
  column: PropTypes.object,
};

export default AudioDatapointCard;
