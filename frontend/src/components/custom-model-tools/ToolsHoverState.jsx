import { alpha, Box, Divider, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const ToolsHoverState = ({ tools, disableHover }) => {
  if (disableHover) {
    return "Tools";
  }
  if (!tools || tools.length === 0) {
    return (
      <Box
        sx={{
          marginTop: "-10px",
          padding: "8px",
          backgroundColor: "background.paper",
          borderRadius: "8px",
          boxShadow: (theme) =>
            `4px 4px 16px 0px ${alpha(theme.palette.common.black, 0.1)}`,
        }}
      >
        <Typography
          variant="s3"
          fontWeight={"fontWeightMedium"}
          color="text.primary"
        >
          No tools
        </Typography>
      </Box>
    );
  }
  return (
    <Box
      sx={{
        marginTop: "-10px",
        padding: "8px",
        backgroundColor: "background.paper",
        borderRadius: "8px",
        display: "flex",
        flexDirection: "column",
        gap: "4px",
        boxShadow: (theme) =>
          `4px 4px 16px 0px ${alpha(theme.palette.common.black, 0.1)}`,
      }}
    >
      <Box
        sx={{
          width: "100%",
        }}
      >
        <Typography
          variant="s3"
          fontWeight={"fontWeightSemiBold"}
          color="text.primary"
        >
          Tools ({tools?.length})
        </Typography>
      </Box>
      <Divider />
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        {tools?.map((item, index) => {
          return (
            <Typography
              key={index}
              variant="s3"
              fontWeight={"fontWeightMedium"}
              color="text.primary"
            >
              {Object.prototype.hasOwnProperty.call(item, "tool")
                ? item?.tool?.label
                : item?.name}
            </Typography>
          );
        })}
      </Box>
    </Box>
  );
};

export default ToolsHoverState;

ToolsHoverState.propTypes = {
  tools: PropTypes.array,
  disableHover: PropTypes.bool,
};
