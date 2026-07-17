import React, { useMemo } from "react";
import { alpha, Box, Divider, Typography } from "@mui/material";
import PropTypes from "prop-types";

import { buildParameterRows } from "./toolHoverState.utils";

const CellStyle = ({ heading, value }) => {
  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-start",
        gap: 2,
      }}
    >
      <Typography
        variant="s3"
        fontWeight={"fontWeightMedium"}
        color="text.primary"
        sx={{ flexShrink: 0 }}
      >
        {heading}
      </Typography>
      <Typography
        variant="s3"
        fontWeight={"fontWeightRegular"}
        color="text.primary"
        sx={{ textAlign: "right", wordBreak: "break-word", minWidth: 0 }}
      >
        {value}
      </Typography>
    </Box>
  );
};

CellStyle.propTypes = {
  heading: PropTypes.string,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};

const ToolHoverState = ({ config, disabledHover }) => {
  const rows = useMemo(() => buildParameterRows(config), [config]);

  if (disabledHover) {
    return "Parameters";
  }

  return (
    <Box
      sx={{
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
      <Box sx={{ width: "100%" }}>
        <Typography
          variant="s3"
          fontWeight={"fontWeightSemiBold"}
          color="text.primary"
        >
          Parameters
        </Typography>
      </Box>
      <Divider />
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 1,
          maxHeight: 260,
          overflowY: "auto",
        }}
      >
        {rows.map(({ heading, value }, index) => (
          <CellStyle
            key={`${heading}-${index}`}
            heading={`${heading}:`}
            value={value}
          />
        ))}
      </Box>
    </Box>
  );
};

export default ToolHoverState;

ToolHoverState.propTypes = {
  config: PropTypes.object,
  disabledHover: PropTypes.bool,
};
