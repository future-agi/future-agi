import { Box, Typography } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import SvgColor from "../../../../components/svg-color";
import { ShowComponent } from "../../../../components/show";
import CustomTooltip from "src/components/tooltip";

const getColorsBasedOnValue = (value) => {
  if (value > 0) {
    return {
      backgroundColor: "status.success.bg",
      color: "status.success.fg",
    };
  } else if (value < 0) {
    return {
      backgroundColor: "status.danger.bg",
      color: "status.danger.fg",
    };
  } else {
    return {
      backgroundColor: "background.neutral",
      color: "text.disabled",
    };
  }
};

const Growth = ({ value, getText }) => {
  const isGrowth = value >= 0;

  const { backgroundColor, color } = getColorsBasedOnValue(value);

  const getDisplayValue = () => {
    if (getText) {
      return getText(value);
    } else {
      return `${value > 0 ? "+" : ""}${value}%`;
    }
  };

  return (
    <CustomTooltip
      show={value > 0}
      type="black"
      size={"small"}
      arrow={true}
      title={"% improved from baseline prompt"}
    >
      <Box
        sx={{
          paddingX: 1,
          paddingY: "2px",
          backgroundColor,
          borderRadius: "4px",
          gap: 1,
          display: "flex",
          alignItems: "center",
        }}
      >
        <Typography typography="s2" fontWeight="fontWeightMedium" color={color}>
          {getDisplayValue()}
        </Typography>
        <ShowComponent condition={isGrowth}>
          <SvgColor
            src="/assets/icons/ic_grow.svg"
            sx={{ width: "14px", height: "14px" }}
            color={color}
          />
        </ShowComponent>
        <ShowComponent condition={!isGrowth}>
          <SvgColor
            src="/assets/icons/ic_decline.svg"
            sx={{ width: "14px", height: "14px" }}
            color={color}
          />
        </ShowComponent>
      </Box>
    </CustomTooltip>
  );
};

Growth.propTypes = {
  value: PropTypes.number.isRequired,
  getText: PropTypes.func,
};

export default Growth;
