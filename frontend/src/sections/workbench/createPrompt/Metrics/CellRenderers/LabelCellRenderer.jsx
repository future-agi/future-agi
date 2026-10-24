import React from "react";
import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import _ from "lodash";
import { getBgColor, getTextColor } from "../common";

const getLabelStyles = (label) => ({
  display: "flex",
  height: "100%",
  alignItems: "center",
  "& .label-text": {
    backgroundColor: getBgColor(label),
    borderRadius: "4px",
    color: getTextColor(label),
    px: 0.75,
    py: 0.375,
    textTransform: "none",
    lineHeight: 1.5,
  },
});

const LabelCellRenderer = (props) => {
  let values = props.value || props.data?.labels;
  if (!values) return null;

  // Ensure values is always an array
  if (!Array.isArray(values)) {
    values = [values];
  }

  return (
    <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
      {values.map((val, index) => {
        const sx = getLabelStyles(val);
        return (
          <Box key={index} sx={sx}>
            <Typography
              variant="s3"
              fontWeight="fontWeightMedium"
              className="label-text"
            >
              {_.capitalize(val)}
            </Typography>
          </Box>
        );
      })}
    </Box>
  );
};

export default LabelCellRenderer;

LabelCellRenderer.propTypes = {
  value: PropTypes.string,
  data: PropTypes.shape({
    labels: PropTypes.string,
  }),
};
