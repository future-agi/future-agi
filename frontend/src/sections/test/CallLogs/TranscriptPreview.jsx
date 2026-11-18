import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import _ from "lodash";
import { ShowComponent } from "../../../components/show";
import { formatRole } from "../common";
import { getContentMessage } from "./common";

const TranscriptPreview = ({
  transcript,
  agentName,
  simulatorName,
  callType,
  simulationCallType,
}) => {
  const first = transcript?.[0] ?? null;
  const previewRole = first?.speaker_role;
  const previewContent = getContentMessage(first, simulationCallType);

  return (
    <Box
      sx={{
        borderRadius: 1,
        backgroundColor: "background.default",
        padding: 1,
        gap: 1,
        flexDirection: "column",
        display: "flex",
      }}
    >
      <Typography typography="s2" fontWeight="fontWeightMedium">
        Transcript Preview:
      </Typography>
      <ShowComponent condition={!transcript?.length}>
        <Typography typography="s2" color="text.disabled">
          No transcript available
        </Typography>
      </ShowComponent>
      <ShowComponent condition={transcript?.length > 0}>
        <Box>
          <Typography typography="s2" color="text.disabled">
            {formatRole(
              previewRole,
              agentName,
              simulatorName,
              callType,
              simulationCallType,
            )}{" "}
            : {previewContent}
          </Typography>
        </Box>
      </ShowComponent>
    </Box>
  );
};

TranscriptPreview.propTypes = {
  transcript: PropTypes.array,
  agentName: PropTypes.string,
  simulatorName: PropTypes.string,
  callType: PropTypes.string,
  simulationCallType: PropTypes.string,
};

export default TranscriptPreview;
