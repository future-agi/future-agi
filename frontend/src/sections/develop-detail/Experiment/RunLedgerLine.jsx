// W1-6: the one ledger line of a run on a surface that has no R1 object of
// its own (the waiting panel): it reads the experiment detail the tray
// already polls (same query key, so no extra request) and prints
// runLedger.ledgerLine. Nothing while the run has no rows yet.
import React from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import { useExperimentDetail } from "src/api/develop/runs";
import { ledgerLine } from "src/sections/common/Runs/runLedger";

export default function RunLedgerLine({ experimentId, runId, noun = "row" }) {
  const { data } = useExperimentDetail(experimentId, {
    enabled: Boolean(experimentId && runId),
    refetchInterval: 4000,
    meta: { errorHandled: true },
  });
  const run = (data?.runs ?? []).find((r) => r.run_id === runId);
  const line = ledgerLine(run?.ledger, { noun });
  if (!line) return null;
  return (
    <Typography
      typography="s2"
      color="text.secondary"
      data-testid="waiting-ledger"
      sx={{ minWidth: 0 }}
    >
      {line}
    </Typography>
  );
}

RunLedgerLine.propTypes = {
  experimentId: PropTypes.string,
  runId: PropTypes.string,
  noun: PropTypes.string,
};
