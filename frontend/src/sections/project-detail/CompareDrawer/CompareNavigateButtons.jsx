import { Box, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

const CompareNavigateButtons = ({
  totalCount,
  currentCount,
  onNext,
  onPrevious,
}) => {
  return (
    <Box
      sx={{ display: "flex", my: 1, ml: 0.5, alignItems: "center", gap: "6px" }}
    >
      <IconButton
        size="small"
        disabled={currentCount === 1}
        onClick={onPrevious}
      >
        <Iconify
          icon="solar:alt-arrow-left-linear"
          color="text.primary"
          width={14}
        />
      </IconButton>
      <Typography fontSize="13px" color="text.primary">
        <Typography component="span" fontWeight={600} fontSize="13px">
          {currentCount}
        </Typography>{" "}
        {" of "}
        <Typography component="span" fontWeight={600} fontSize="13px">
          {totalCount}
        </Typography>{" "}
        {" Row"}
        {totalCount > 1 ? "s" : ""}
      </Typography>
      <IconButton
        size="small"
        disabled={currentCount === totalCount}
        onClick={onNext}
      >
        <Iconify
          icon="solar:alt-arrow-right-linear"
          color="text.primary"
          width={14}
        />
      </IconButton>
    </Box>
  );
};

CompareNavigateButtons.propTypes = {
  totalCount: PropTypes.number,
  currentCount: PropTypes.number,
  onNext: PropTypes.func,
  onPrevious: PropTypes.func,
};

export default CompareNavigateButtons;
