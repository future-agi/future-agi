import { Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { getUniqueColorPalette } from "src/utils/utils";

const CompareDatasetSummaryIcon = ({ index, style = {} }) => {
  if ((index == undefined || index === null) && typeof index !== "number")
    return <></>;
  const { tagBackground, tagForeground } = getUniqueColorPalette(index);

  return (
    <Typography
      sx={{
        color: tagForeground,
        backgroundColor: tagBackground,
        borderRadius: "6px",
        height: "24px",
        width: "24px",
        fontSize: "12px",
        fontWeight: 500,
        paddingX: "4px",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        ...style,
      }}
    >
      {String.fromCharCode(65 + index)}
    </Typography>
  );
};

export default CompareDatasetSummaryIcon;

CompareDatasetSummaryIcon.propTypes = {
  index: PropTypes.number,
  style: PropTypes.object,
};
