import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const HelperText = ({ id, text, sx = {} }) => {
  return (
    <Box sx={{ display: "flex" }}>
      <Typography
        id={id}
        variant="s1"
        color="text.primary"
        fontWeight={"fontWeightRegular"}
        sx={{
          ...sx,
        }}
      >
        {text}
      </Typography>
    </Box>
  );
};

HelperText.propTypes = {
  id: PropTypes.string,
  text: PropTypes.string,
  sx: PropTypes.object,
};

export default HelperText;
