import React from "react";
import { Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";

const ScenarioNameCellRenderer = ({ data }) => {
  const name = data?.name;
  const description = data?.description || data?.source || "";

  return (
    <Stack
      display={"flex"}
      direction={"column"}
      justifyContent={"center"}
      height="100%"
      spacing={0.25}
      width="100%"
    >
      <Typography
        variant="body2"
        fontWeight={600}
        sx={{ fontSize: "13px", color: "text.primary" }}
      >
        {name}
      </Typography>

      <Typography
        variant="caption"
        color="text.secondary"
        sx={{
          fontSize: "12px",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          width: "100%",
          display: "block",
        }}
      >
        {description}
      </Typography>
    </Stack>
  );
};

ScenarioNameCellRenderer.propTypes = {
  data: PropTypes.object,
};

export default ScenarioNameCellRenderer;
