import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

const EvalColumnChip = ({ text }) => {
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
      <Iconify
        icon="solar:check-circle-linear"
        sx={{ color: "text.secondary" }}
        width={20}
      />
      <Typography color="text.secondary" fontWeight={600} fontSize="13px">
        {text}
      </Typography>
    </Box>
  );
};

EvalColumnChip.propTypes = {
  text: PropTypes.string,
};

export default EvalColumnChip;
