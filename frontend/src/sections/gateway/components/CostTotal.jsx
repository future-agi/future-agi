import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

import { costTotal } from "../utils/formatters";

// A cost total that never reads as free when its requests had no price
//: an em dash titled "No price found" when none had one,
// the priced part as a floor ("≥ $0.035") when some had none, titled with
// how many. See costTotal.
export default function CostTotal({
  value,
  unpriced,
  requests,
  cell = false,
  format,
  of,
}) {
  const { text, title } = costTotal(value, {
    unpriced,
    requests,
    cell,
    format,
    of,
  });
  return (
    <Box component="span" title={title ?? undefined}>
      {text}
    </Box>
  );
}

CostTotal.propTypes = {
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  unpriced: PropTypes.number,
  requests: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  cell: PropTypes.bool,
  format: PropTypes.func,
  of: PropTypes.string,
};
