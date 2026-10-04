import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Collapse, Typography } from "@mui/material";

const printableValue = (value) => {
  if (Array.isArray(value)) return value.join(", ");
  return value == null ? "—" : String(value);
};

const BreakdownRow = ({ row, onSelectSpan }) => {
  const [showExplanation, setShowExplanation] = useState(false);
  return (
    <Box sx={{ py: 0.75, borderTop: "1px solid", borderColor: "divider" }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <Button size="small" onClick={() => onSelectSpan?.(row.span_id)}>
          View span
        </Button>
        <Typography variant="body2" sx={{ flex: 1 }}>
          {row.span_name || row.span_id}
        </Typography>
        <Typography color={row.error ? "error.main" : "text.secondary"} variant="body2">
          {row.error ? "Error" : printableValue(row.value)}
        </Typography>
      </Box>
      {row.explanation && (
        <>
          <Button
            size="small"
            onClick={() => setShowExplanation((current) => !current)}
            aria-expanded={showExplanation}
          >
            {showExplanation ? "Hide explanation" : "Show explanation"}
          </Button>
          <Collapse in={showExplanation}>
            <Typography variant="body2" color="text.secondary" sx={{ px: 1 }}>
              {row.explanation}
            </Typography>
          </Collapse>
        </>
      )}
    </Box>
  );
};

BreakdownRow.propTypes = { row: PropTypes.object.isRequired, onSelectSpan: PropTypes.func };

export default BreakdownRow;
