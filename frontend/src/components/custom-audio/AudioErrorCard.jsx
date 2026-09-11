import { Box, Typography, Divider } from "@mui/material";
import React, { useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import PropTypes from "prop-types";
import { transformAudioData } from "./audioHelper";
import TestAudioPlayer from "./TestAudioPlayer";

const AudioErrorCard = ({ valueInfos, column }) => {
  const audioPlayerData = transformAudioData(valueInfos);
  const wordLimit = 240;

  const [expanded, setExpanded] = useState(
    Array(audioPlayerData.length).fill(false),
  );

  const toggleShowMore = (index) => {
    setExpanded((prev) => {
      const newExpanded = [...prev];
      newExpanded[index] = !newExpanded[index];
      return newExpanded;
    });
  };

  return (
    <Accordion defaultExpanded disableGutters>
      <AccordionSummary aria-label={`Expand audio for ${column}`}>
        {column}
      </AccordionSummary>
      <AccordionDetails>
        <Divider sx={{ mb: 1 }} />
        <Box>
          {audioPlayerData.map((item, index) => (
            <Box key={item.id || `audio-${index}`}>
              <TestAudioPlayer
                key={item?.audioData?.url}
                {...item}
                splitTimeView={true}
              />
              <Typography
                sx={{ mt: 1, ml: 1, fontSize: "12px", color: "text.disabled" }}
              >
                {expanded[index] || (item.description || "").length <= wordLimit
                  ? (item.description || "")
                  : `${(item.description || "").substring(0, wordLimit)}...`}
              </Typography>

              {(item.description || "").length > wordLimit && (
                <Box
                  sx={{ display: "flex", justifyContent: "flex-end", mt: 1 }}
                >
                  <Typography
                    component="button"
                    variant="body2"
                    onClick={() => toggleShowMore(index)}
                    sx={{
                      color: "text.primary",
                      textDecoration: "underline",
                      background: "none",
                      border: "none",
                      fontWeight: "500",
                      cursor: "pointer",
                      padding: 0,
                    }}
                  >
                    {expanded[index] ? "Show Less" : "Show More"}
                  </Typography>
                </Box>
              )}

              {index < audioPlayerData.length - 1 && <Divider sx={{ my: 2 }} />}
            </Box>
          ))}
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};

AudioErrorCard.propTypes = {
  valueInfos: PropTypes.object,
  column: PropTypes.object,
};

export default AudioErrorCard;
