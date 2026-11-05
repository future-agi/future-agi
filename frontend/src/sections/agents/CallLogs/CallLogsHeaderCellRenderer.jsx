import React from "react";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import EvalTargetGlyph from "src/components/traceDetail/EvalRollupSection/EvalTargetGlyph";
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";

const CallLogsHeaderCellRenderer = ({ displayName, targetType }) => {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.5,
        overflow: "hidden",
      }}
    >
      <Iconify
        icon="material-symbols:check-circle-outline"
        color="green.500"
        width={16}
        height={16}
        style={{ flexShrink: 0 }}
      />
      <EvalTargetGlyph targetType={targetType} />
      <OverflowTooltip title={displayName}>
        <Typography
          variant="subtitle2"
          sx={{
            fontWeight: 500,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {displayName}
        </Typography>
      </OverflowTooltip>
    </Box>
  );
};

CallLogsHeaderCellRenderer.propTypes = {
  displayName: PropTypes.string,
  targetType: PropTypes.string,
};

export default CallLogsHeaderCellRenderer;
