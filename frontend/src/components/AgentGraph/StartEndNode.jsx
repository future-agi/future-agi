import React, { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";

const StartEndNode = ({ data }) => {
  const isStart = data.variant === "start";

  return (
    <Box sx={{ position: "relative" }}>
      {/* Input handle for End node */}
      {!isStart && (
        <Handle
          type="target"
          position={Position.Top}
          style={{ visibility: "hidden" }}
          isConnectable={false}
        />
      )}

      <Box
        sx={{
          px: 2,
          py: 0.5,
          borderRadius: 99,
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "background.paper",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 0.5,
          minWidth: 70,
        }}
      >
        {isStart && (
          <SvgColor
            src="/assets/icons/ic_start_agent.svg"
            sx={{ width: 16, height: 16, bgcolor: "status.info.solid" }}
          />
        )}
        <Typography
          typography="s2"
          color="text.secondary"
          fontWeight="fontWeightMedium"
        >
          {isStart ? "Start" : "End"}
        </Typography>
      </Box>

      {/* Output handle for Start node */}
      {isStart && (
        <Handle
          type="source"
          position={Position.Bottom}
          style={{ visibility: "hidden" }}
          isConnectable={false}
        />
      )}
    </Box>
  );
};

StartEndNode.propTypes = {
  data: PropTypes.shape({
    variant: PropTypes.oneOf(["start", "end"]).isRequired,
  }).isRequired,
};

export default memo(StartEndNode);
