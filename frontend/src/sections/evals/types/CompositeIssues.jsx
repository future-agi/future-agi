// W7 A1 (W7-5): why a composite cannot be saved or tested yet (a seat with
// no judge, a weight of 0, a majority of one), from the pure
// compositeModel.validateComposite the Save buttons also read.
import React from "react";
import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import { validateComposite } from "src/sections/common/EvalTypes/compositeModel";

export default function CompositeIssues({ aggregation, rows, weights }) {
  const errors = validateComposite({ aggregation, children: rows, weights });
  if (!errors.length) return null;
  return (
    <Stack gap={0.25} data-testid="composite-issues">
      {errors.map((e) => (
        <Typography key={e} variant="caption" color="error.main">
          {e}
        </Typography>
      ))}
    </Stack>
  );
}

CompositeIssues.propTypes = {
  aggregation: PropTypes.string,
  rows: PropTypes.array.isRequired,
  weights: PropTypes.object,
};
