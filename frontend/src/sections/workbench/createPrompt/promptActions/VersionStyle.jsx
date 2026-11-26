import { Box, Typography } from "@mui/material";
import _ from "lodash";
import PropTypes from "prop-types";
import React from "react";
import { getUniqueColorPalette } from "src/utils/utils";

const VersionStyle = ({ text, index }) => {
  let tagForeground;

  if (index !== undefined && index !== null) {
    const { tagForeground: color } = getUniqueColorPalette(index);
    tagForeground = color;
  }
  return (
    <Typography
      variant="s3"
      fontWeight={"fontWeightMedium"}
      sx={{
        backgroundColor: "action.hover",
        borderRadius: "4px",
        color: "text.primary",
        paddingX: (theme) => theme.spacing(0.75),
        paddingTop: (theme) => theme.spacing(0.5),
        paddingBottom: (theme) => theme.spacing(0.375),
        textTransform: "none",
      }}
    >
      {tagForeground && (
        <Box
          component={"span"}
          sx={{
            height: "9px",
            width: "9px",
            borderRadius: "50%",
            display: "inline-block",
            bgcolor: tagForeground ?? "text.primary",
            mr: 1.25,
            mt: 0.25,
          }}
        />
      )}
      {_.capitalize(text)}
    </Typography>
  );
};

export default VersionStyle;
VersionStyle.propTypes = {
  text: PropTypes.string,
  index: PropTypes.number,
};
