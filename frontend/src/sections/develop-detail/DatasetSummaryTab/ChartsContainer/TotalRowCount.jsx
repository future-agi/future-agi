import { Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const TotalRowCount = ({ value, ...rest }) => {
  return value || value === 0 ? (
    <Typography
      fontSize={"12px"}
      lineHeight={"20px"}
      fontWeight={"fontWeightMedium"}
      {...rest}
    >
      &nbsp;(#{value})
    </Typography>
  ) : (
    <></>
  );
};

export default TotalRowCount;

TotalRowCount.propTypes = {
  value: PropTypes.number,
};
