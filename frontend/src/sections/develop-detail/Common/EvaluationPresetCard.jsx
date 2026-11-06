import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const EvaluationPresetCard = React.forwardRef(
  ({ title, subTitle, onClick, sx = {}, ...rest }, ref) => {
    return (
      <Box
        ref={ref}
        {...rest}
        sx={{
          paddingY: 2,
          paddingX: 3,
          display: "flex",
          flexDirection: "column",
          gap: 1,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          cursor: "pointer",
          ...sx,
        }}
        onClick={onClick}
      >
        <Typography
          variant="body2"
          fontSize={13}
          color={"text.primary"}
          fontWeight={500}
        >
          {title}
        </Typography>
        <Typography
          variant="caption"
          fontSize={13}
          fontWeight={400}
          color="text.secondary"
        >
          {subTitle}
        </Typography>
      </Box>
    );
  },
);

EvaluationPresetCard.displayName = "EvaluationPresetCard";

EvaluationPresetCard.propTypes = {
  title: PropTypes.string.isRequired,
  subTitle: PropTypes.string.isRequired,
  onClick: PropTypes.func,
  sx: PropTypes.object,
};

export default EvaluationPresetCard;
