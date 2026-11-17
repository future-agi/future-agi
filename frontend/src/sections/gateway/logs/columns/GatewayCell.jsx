import React from "react";
import PropTypes from "prop-types";
import { TableCell, Typography } from "@mui/material";
import { EMPTY } from "../../utils/formatters";
import RunLink from "../RunLink";
import { BILLING_MODES, CREDENTIAL_TYPES, labelOf } from "../explorerLabels";

// Cells for the Agent & billing columns (gatewayColumns.js). A log from before
// log entry v2 has none of these fields and shows a dash in each. A request's
// cost is the built-in Cost column alone.

const present = (value) => value != null && value !== "";

const READERS = {
  billingMode: (log) =>
    present(log.billing_mode) ? labelOf(BILLING_MODES, log.billing_mode) : null,
  credentialType: (log) =>
    present(log.credential_type)
      ? labelOf(CREDENTIAL_TYPES, log.credential_type)
      : null,
  // A pre-v2 row keeps the column's default of 1, which counts nothing; only
  // a v2 row, which always carries a generation id, has a real count.
  attemptCount: (log) =>
    present(log.generation_id) && present(log.attempt_count)
      ? String(log.attempt_count)
      : null,
  agentId: (log) => (present(log.agent_id) ? log.agent_id : null),
  agentStep: (log) => (present(log.agent_step) ? log.agent_step : null),
};

/** An Agent & billing value, bare (the logs grid) or in GatewayCell. */
export const GatewayValue = React.memo(function GatewayValue({ column, log }) {
  if (column === "agentRunId") {
    return present(log.agent_run_id) ? (
      <RunLink
        runId={String(log.agent_run_id)}
        variant="body2"
        noWrap
        title={String(log.agent_run_id)}
        sx={{ display: "block", maxWidth: 140 }}
      />
    ) : (
      <Typography variant="body2">{EMPTY}</Typography>
    );
  }
  const value = READERS[column]?.(log) ?? null;
  return (
    <Typography variant="body2" noWrap>
      {value ?? EMPTY}
    </Typography>
  );
});

GatewayValue.propTypes = {
  column: PropTypes.string.isRequired,
  log: PropTypes.object.isRequired,
};

const GatewayCell = React.memo(function GatewayCell({ column, log }) {
  return (
    <TableCell>
      <GatewayValue column={column} log={log} />
    </TableCell>
  );
});

GatewayCell.propTypes = {
  column: PropTypes.string.isRequired,
  log: PropTypes.object.isRequired,
};

export default GatewayCell;
