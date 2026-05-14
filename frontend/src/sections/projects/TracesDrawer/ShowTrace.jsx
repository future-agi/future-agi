import React from "react";
import PropTypes from "prop-types";
import { Grid, Box } from "@mui/material";
import Conversation from "../SessionsView/Conversation";
import TraceCardRightSection from "../SessionsView/TraceCardRightSection";

const ShowTrace = ({
  traceId,
  human,
  ai,
  systemMetrics,
  evaluationMetrics,
  activeSessionId,
  onTraceClick,
}) => {
  return (
    <Grid
      container
      spacing={0}
      onClick={() => onTraceClick?.(traceId)}
      sx={{
        marginTop: "2px",
        border: "1px solid",
        borderRadius: "10px",
        borderColor: "divider",
        cursor: onTraceClick ? "pointer" : "default",
        minHeight: "440px",
        height: "440px",
        width: "100%",
        minWidth: 0,
        overflow: "hidden",
        ...(onTraceClick && {
          transition: "box-shadow 0.2s",
          "&:hover": { boxShadow: 3 },
        }),
      }}
    >
      {/* Left Section */}
      <Grid
        item
        xs={8}
        sx={{
          height: "440px",
          maxHeight: "440px",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          minWidth: 0,
          overflow: "hidden",
          borderTopLeftRadius: "10px",
          borderBottomLeftRadius: "10px",
          position: "relative",
          backgroundColor: "background.paper",
          // paddingY: 4,
          borderRight: "1px solid",
          borderColor: "divider",
        }}
      >
        <Conversation activeSessionId={activeSessionId} human={human} ai={ai} />
      </Grid>

      {/* Right Section */}
      <Grid
        item
        xs={4}
        sx={{
          backgroundColor: "background.paper",
          minWidth: 0,
          overflow: "hidden",
        }}
      >
        <Box>
          <TraceCardRightSection
            traceId={traceId}
            systemMetrics={systemMetrics}
            evaluationMetrics={evaluationMetrics}
          />
        </Box>
      </Grid>
    </Grid>
  );
};

ShowTrace.propTypes = {
  traceId: PropTypes.string.isRequired,
  human: PropTypes.string.isRequired,
  ai: PropTypes.string.isRequired,
  systemMetrics: PropTypes.object,
  evaluationMetrics: PropTypes.object,
  activeSessionId: PropTypes.string,
  onTraceClick: PropTypes.func,
};

export default ShowTrace;
