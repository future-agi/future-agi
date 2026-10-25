import React from "react";
import PropTypes from "prop-types";
import { TableCell, Typography } from "@mui/material";
import { formatCost } from "../../utils/formatters";
import {
  BILLING_MODES_SHORT,
  CREDENTIAL_TYPES,
  labelOf,
} from "../explorerLabels";

// Cells for the Agent & billing columns (gatewayColumns.js). A log from before
// log entry v2 has none of these fields and shows a dash in each.

const present = (value) => value != null && value !== "";

const cost = (value) => (present(value) ? formatCost(value) : null);

const READERS = {
  billingMode: (log) =>
    present(log.billing_mode)
      ? labelOf(BILLING_MODES_SHORT, log.billing_mode)
      : null,
  credentialType: (log) =>
    present(log.credential_type)
      ? labelOf(CREDENTIAL_TYPES, log.credential_type)
      : null,
  costUpstream: (log) => cost(log.cost_upstream),
  costMarkup: (log) => cost(log.cost_markup),
  // A pre-v2 row keeps the column's default of 1, which counts nothing; only
  // a v2 row, which always carries a generation id, has a real count.
  attemptCount: (log) =>
    present(log.generation_id) && present(log.attempt_count)
      ? String(log.attempt_count)
      : null,
  agentId: (log) => (present(log.agent_id) ? log.agent_id : null),
  agentStep: (log) => (present(log.agent_step) ? log.agent_step : null),
};

const GatewayCell = React.memo(function GatewayCell({ column, log }) {
  if (column === "agentRunId") {
    return (
      <TableCell>
        {present(log.agent_run_id) ? (
          // Text, not a link.
          <Typography
            variant="body2"
            noWrap
            title={String(log.agent_run_id)}
            sx={{ display: "block", maxWidth: 140, fontFamily: "monospace" }}
          >
            {String(log.agent_run_id)}
          </Typography>
        ) : (
          <Typography variant="body2">-</Typography>
        )}
      </TableCell>
    );
  }
  const value = READERS[column]?.(log) ?? null;
  return (
    <TableCell>
      <Typography variant="body2" noWrap>
        {value ?? "-"}
      </Typography>
    </TableCell>
  );
});

GatewayCell.propTypes = {
  column: PropTypes.string.isRequired,
  log: PropTypes.object.isRequired,
};

export default GatewayCell;
