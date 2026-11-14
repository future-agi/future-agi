import React from "react";
import PropTypes from "prop-types";
import { Chip } from "@mui/material";

import { summaryText } from "./utils";

const SummaryBar = ({ aggregate }) => {
  const label = summaryText(aggregate);
  return label ? <Chip size="small" label={label} variant="outlined" /> : null;
};

SummaryBar.propTypes = {
  aggregate: PropTypes.oneOfType([PropTypes.object, PropTypes.number]),
};

export default SummaryBar;
