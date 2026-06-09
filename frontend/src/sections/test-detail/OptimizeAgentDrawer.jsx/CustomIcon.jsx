import { AgentPromptOptimizerStatus } from "../FixMyAgentDrawer/common";
import { getStatusColor } from "./common";
import SvgColor from "src/components/svg-color/svg-color";
import PropTypes from "prop-types";
import { Box, CircularProgress, useTheme } from "@mui/material";

const CustomStepIcon = ({ step, isFailedStep }) => {
  const theme = useTheme();
  const effectiveStatus = isFailedStep
    ? AgentPromptOptimizerStatus.FAILED
    : step?.status;
  const colors = getStatusColor(effectiveStatus, theme);
  const iconColor = colors?.icon;
  const isRunning = effectiveStatus === AgentPromptOptimizerStatus.RUNNING;
  return (
    <Box
      sx={{
        width: 30,
        height: 30,
        borderRadius: "50%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        bgcolor: colors?.bg,
        flexShrink: 0,
      }}
    >
      {isRunning ? (
        <CircularProgress
          size={14}
          thickness={5}
          sx={{ color: iconColor }}
          aria-label="Step in progress"
        />
      ) : (
        <SvgColor
          sx={{ color: iconColor, width: 14 }}
          src={
            isFailedStep
              ? "/assets/icons/ic_failed.svg"
              : "/assets/icons/ic_check_with_circle_tick.svg"
          }
        />
      )}
    </Box>
  );
};

CustomStepIcon.propTypes = {
  step: PropTypes.shape({
    status: PropTypes.string.isRequired, // Ensure `status` is validated as a required string
    name: PropTypes.string,
    description: PropTypes.string,
    updatedAt: PropTypes.string,
  }).isRequired,
  isFailedStep: PropTypes.bool,
};
export default CustomStepIcon;
