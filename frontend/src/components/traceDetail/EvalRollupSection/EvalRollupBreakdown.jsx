import React, { useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, Collapse } from "@mui/material";

import EvalRollupSection from "./index";
import { evalRollupShape } from "./shapes";

// The per-span rollup sits above the existing eval list, collapsed, so the
// list keeps its search, labels, explanations and Fix with Falcon.
const EvalRollupBreakdown = ({ rollup, onSelectSpan }) => {
  const [open, setOpen] = useState(false);
  if (rollup?.error) {
    return (
      <Alert severity="error" sx={{ mb: 1 }}>
        Evaluations are temporarily unavailable.
      </Alert>
    );
  }
  const count = rollup?.evals?.length || 0;
  if (!count) return null;
  return (
    <Box sx={{ mb: 1 }}>
      <Button
        size="small"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
      >
        {open
          ? "Hide per-span breakdown"
          : `Show per-span breakdown (${count})`}
      </Button>
      <Collapse in={open} unmountOnExit>
        <EvalRollupSection rollup={rollup} onSelectSpan={onSelectSpan} />
      </Collapse>
    </Box>
  );
};

EvalRollupBreakdown.propTypes = {
  rollup: evalRollupShape,
  onSelectSpan: PropTypes.func,
};

export default EvalRollupBreakdown;
