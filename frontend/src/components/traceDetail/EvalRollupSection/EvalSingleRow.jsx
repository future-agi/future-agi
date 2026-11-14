import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

import EvalTargetGlyph from "./EvalTargetGlyph";
import SummaryBar from "./SummaryBar";
import BreakdownRow from "./BreakdownRow";

const EvalSingleRow = ({ evalRow, onSelectSpan }) => (
  <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1, p: 1 }}>
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, pb: 0.5 }}>
      <EvalTargetGlyph targetType={evalRow.target_type} />
      <Typography variant="subtitle2" sx={{ flex: 1 }}>
        {evalRow.eval_name}
      </Typography>
      <SummaryBar aggregate={evalRow.aggregate} />
    </Box>
    {(evalRow.spans || []).map((row, index) => (
      <BreakdownRow
        key={`${row.span_id || "span"}-${index}`}
        row={row}
        onSelectSpan={onSelectSpan}
      />
    ))}
  </Box>
);

EvalSingleRow.propTypes = { evalRow: PropTypes.object.isRequired, onSelectSpan: PropTypes.func };

export default EvalSingleRow;
