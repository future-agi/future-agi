import { Box, Button, Typography } from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";

const NoAnnotationSection = ({ onCreateLabel }) => {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flex: 1,
        flexDirection: "column",
        gap: "12px",
      }}
    >
      <Typography variant="s1">
        Labels have been created. Add labels to start annotating.
      </Typography>
      <Button
        variant="outlined"
        color="primary"
        size="small"
        startIcon={<Iconify icon="solar:add-linear" width={16} height={16} />}
        onClick={onCreateLabel}
      >
        Add Label
      </Button>
    </Box>
  );
};

NoAnnotationSection.propTypes = {
  onCreateLabel: PropTypes.func,
};

export default NoAnnotationSection;
