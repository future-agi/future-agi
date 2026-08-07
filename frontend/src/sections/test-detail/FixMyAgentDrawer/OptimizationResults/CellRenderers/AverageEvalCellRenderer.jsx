import { Box, Typography } from "@mui/material";
import React from "react";
import Growth from "../Growth";
import PropTypes from "prop-types";
import { ShowComponent } from "../../../../../components/show";

const formatScore = (value) => {
  if (typeof value?.score !== "number") return "-";
  const pct = value.score <= 1 ? value.score * 100 : value.score;
  return `${pct % 1 === 0 ? pct : pct.toFixed(2)}%`;
};

const AverageEvalCellRenderer = ({ value }) => {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        height: "100%",
        lineHeight: "22px",
      }}
    >
      <Typography typography="s1">{formatScore(value)}</Typography>
      <ShowComponent
        condition={
          value?.percentage_change !== null &&
          value?.percentage_change !== undefined
        }
      >
        <Growth value={value?.percentage_change} />
      </ShowComponent>
    </Box>
  );
};

AverageEvalCellRenderer.propTypes = {
  value: PropTypes.object,
};

export default AverageEvalCellRenderer;
