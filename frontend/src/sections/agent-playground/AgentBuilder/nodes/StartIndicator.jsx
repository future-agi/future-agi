import React from "react";
import { Box, useTheme } from "@mui/material";
import SvgColor from "src/components/svg-color";
import PropTypes from "prop-types";

const StartIndicator = ({
  isWorkflowRunning,
  isRunning,
  isCompleted,
  isError,
}) => {
  const theme = useTheme();
  const isDashing = isWorkflowRunning && !isCompleted && !isRunning && !isError;

  return (
    <Box
      sx={{
        position: "absolute",
        left: -50,
        top: 5,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: 30,
        width: 30,
        border: isDashing ? "none" : "1px solid",
        borderColor:
          isCompleted || isRunning ? "status.success.solid" : "text.disabled",
        borderRadius: "50%",
        pointerEvents: "none",
      }}
    >
      {isDashing && (
        <svg
          width="30"
          height="30"
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            pointerEvents: "none",
          }}
        >
          <circle
            cx="15"
            cy="15"
            r="13.5"
            fill="none"
            stroke={theme.palette.green[500]}
            strokeWidth="1.5"
            strokeDasharray="6 3"
            strokeDashoffset="0"
            style={{
              animation: "dash-around 2s linear infinite",
            }}
          />
        </svg>
      )}
      <SvgColor
        src="/assets/icons/ic_start_agent.svg"
        sx={{ width: 20, height: 20, bgcolor: "status.info.solid" }}
      />
    </Box>
  );
};

StartIndicator.propTypes = {
  isWorkflowRunning: PropTypes.bool,
  isRunning: PropTypes.bool,
  isCompleted: PropTypes.bool,
  isError: PropTypes.bool,
};

export default StartIndicator;
