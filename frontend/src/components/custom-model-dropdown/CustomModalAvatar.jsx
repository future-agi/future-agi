import { Box, Typography, useTheme } from "@mui/material";
import React from "react";
import { getInitials } from "./KeysHelper";
import PropTypes from "prop-types";

const CustomModalAvatar = ({
  text,
  width = 25,
  height = 25,
  fontSize = 12,
}) => {
  const theme = useTheme();
  return (
    <Box
      sx={{
        height,
        width,
        borderRadius: "50%",
        backgroundColor: "primary.lighter",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0, // prevent shrinking on small screens
      }}
    >
      <Typography
        variant="caption"
        fontWeight="600"
        sx={{
          fontSize,
          lineHeight: 1,
          background: `linear-gradient(to right, ${theme.palette.primary.main}, ${theme.palette.pink[500]})`,
          WebkitBackgroundClip: "text",
          WebkitTextFillColor: "transparent",
          display: "inline-block",
        }}
      >
        {getInitials(text)}
      </Typography>
    </Box>
  );
};

CustomModalAvatar.propTypes = {
  text: PropTypes.string,
  width: PropTypes.number,
  height: PropTypes.number,
  fontSize: PropTypes.number,
};

export default CustomModalAvatar;
