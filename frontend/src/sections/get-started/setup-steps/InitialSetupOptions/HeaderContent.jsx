import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const HeaderContent = ({ title, description }) => {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
      <Typography
        variant="s1"
        fontWeight={"fontWeightSemiBold"}
        color="text.primary"
      >
        {title}
      </Typography>
      <Typography
        variant="s2"
        fontWeight={"fontWeightRegular"}
        color="text.secondary"
      >
        {description}
      </Typography>
    </Box>
  );
};

export default HeaderContent;

HeaderContent.propTypes = {
  title: PropTypes.string,
  description: PropTypes.string,
};
