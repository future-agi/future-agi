import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";

export const FieldWrapper = ({ children, helperText }) => {
  return (
    <Box sx={{ display: "flex", gap: 0.5, flexDirection: "column" }}>
      {children}
      <Typography
        typography="s3"
        fontWeight="fontWeightMedium"
        color="text.secondary"
      >
        {helperText}
      </Typography>
    </Box>
  );
};

FieldWrapper.propTypes = {
  children: PropTypes.node.isRequired,
  helperText: PropTypes.string.isRequired,
};
