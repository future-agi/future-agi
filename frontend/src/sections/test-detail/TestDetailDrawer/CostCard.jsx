import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";

const CostCard = ({
  icon,
  iconColor,
  iconBgColor,
  title,
  value,
  additionalInfo,
}) => {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        padding: 1,
        position: "relative",
        gap: 0.5,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Box
        sx={{
          position: "absolute",
          top: 8,
          right: 8,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          backgroundColor: iconBgColor,
          padding: 0.5,
          borderRadius: "4px",
        }}
      >
        <SvgColor
          src={icon}
          sx={{ width: "21px", height: "21px", color: iconColor }}
        />
      </Box>
      <Typography typography="s1">{title}</Typography>
      <Typography typography="m1" fontWeight="fontWeightSemiBold">
        ${value}
      </Typography>
      <ShowComponent condition={additionalInfo?.length === 0}>
        <Typography typography="s2">No additional details available</Typography>
      </ShowComponent>
      <ShowComponent condition={additionalInfo?.length > 0}>
        {additionalInfo?.map((item) => (
          <Typography typography="s2" key={item.label}>
            {item.label}: {item.value}
          </Typography>
        ))}
      </ShowComponent>
    </Box>
  );
};

CostCard.propTypes = {
  title: PropTypes.string,
  icon: PropTypes.string,
  value: PropTypes.number,
  additionalInfo: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string,
      value: PropTypes.number,
    }),
  ),
  iconColor: PropTypes.string,
  iconBgColor: PropTypes.string,
};

export default CostCard;
