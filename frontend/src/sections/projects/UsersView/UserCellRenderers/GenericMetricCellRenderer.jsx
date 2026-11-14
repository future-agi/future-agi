import React from "react";
import { Box, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify"; // Adjust based on actual path
import PropTypes from "prop-types";
import { formatNumberWithCommas } from "../common";

const iconMap = {
  tokens: (
    <SvgColor
      src="/assets/icons/components/ic_newcoin.svg"
      sx={{ width: 15, height: 15, color: "text.disabled" }}
    />
  ),
  cost: <Typography variant="body2">$</Typography>,
  latency: (
    <Iconify
      icon="solar:clock-circle-linear"
      color="text.disabled"
      width={16}
    />
  ),
};

export const GeneralStatCellRenderer = (props) => {
  const { value, colDef } = props;
  const type = colDef?.cellRendererParams?.dataType;

  const icon = iconMap[type];

  const displayValue =
    typeof value === "number" ? formatNumberWithCommas(value) : value;

  return (
    <Box display="flex" alignItems="center" gap={1} height="100%" width="100%">
      {icon}
      <Typography variant="body2">{displayValue}</Typography>
    </Box>
  );
};

GeneralStatCellRenderer.propTypes = {
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  colDef: PropTypes.shape({
    cellRendererParams: PropTypes.shape({
      dataType: PropTypes.string,
    }),
  }),
};
