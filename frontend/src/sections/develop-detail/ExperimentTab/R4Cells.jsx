// R4 leaderboard cells (lane Rb MUST 7): Updated (RelativeTime, or the
// live status while a run is going), pass^k and Where it ran. Numbers come
// from the leaderboard's one numbers object (runNumbers.js), never a
// second computation.
import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import RelativeTime from "src/sections/common/ux/RelativeTime";
import { EMPTY, NUM_SX, fmtPct } from "src/sections/common/format/fmt";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import { provenanceOf } from "src/sections/common/Runs/provenanceModel";
import { useCell } from "./cellContext";
import RunStatusCell from "./RunStatusCell";
import { updatedOf } from "./leaderboardVersions";
import {
  reducedText,
  reducerLabel,
} from "src/sections/common/Analysis/trialReducers";

const Cell = ({ children, align = "flex-start" }) => (
  <Box
    sx={{
      height: "100%",
      display: "flex",
      alignItems: "center",
      justifyContent: align,
      minWidth: 0,
    }}
  >
    {children}
  </Box>
);

Cell.propTypes = { children: PropTypes.node, align: PropTypes.string };

// Terminal outcomes the Updated column names with their chip, not a time.
const CHIP_KEYS = new Set(["Cancelled", "Failed", "PartialCompleted"]);

export function UpdatedCell(params) {
  const { node, data } = useCell(params);
  if (node.group || !data) return null;
  // options-truth L7b G4: a stopped run shows its Stopped chip (Resume on
  // hover when the ledger allows it), not a time as if it had finished.
  // the capability contract: so does a failed run and a run that finished with errors
  // (its real outcome, the one derivation), never a time like a clean run.
  const status = getRunStatus(data);
  if (status.active || status.waiting || CHIP_KEYS.has(status.key))
    return <RunStatusCell {...params} />;
  const iso = updatedOf(data);
  if (!iso) return null;
  return (
    <Cell>
      <RelativeTime
        iso={iso}
        by={data.created_by?.name}
        sx={{ typography: "s1", color: "text.secondary", whiteSpace: "nowrap" }}
      />
    </Cell>
  );
}

export function PassKCell(params) {
  const { node, data, context } = useCell(params);
  if (node.group || !data) return null;
  const pk = context.numbers?.byRun?.[data.run_id]?.passK;
  // the interface contract: a run that set its own reducer is read by it, named, with its
  // 95% interval (`pass^3 · 41% (33–49%)`).
  const own = context.numbers?.byRun?.[data.run_id]?.own;
  const ownText = own ? reducedText(own, { k: data.trials }) : null;
  // Review S3: beside a run that names its own rule, a run read by pass^k
  // names it too (the column is `From tries` then).
  const anyOwn = Object.values(context.numbers?.byRun ?? {}).some(
    (b) => b?.own,
  );
  const passKName = anyOwn
    ? `${reducerLabel("pass_hat_k", Math.max(1, Number(data.trials) || 1))} · `
    : "";
  return (
    <Cell align="flex-end">
      <Typography
        typography="s1"
        sx={{ ...NUM_SX }}
        color={pk?.rate == null && !ownText ? "text.disabled" : "text.primary"}
        noWrap
      >
        {ownText ??
          (pk?.rate == null
            ? EMPTY
            : `${passKName}${fmtPct(pk.rate)} (${pk.pass}/${pk.n})`)}
      </Typography>
    </Cell>
  );
}

export function WhereRanCell(params) {
  const { node, data } = useCell(params);
  if (node.group || !data) return null;
  const where = provenanceOf(data)?.where?.text ?? null;
  return (
    <Cell>
      <Typography
        typography="s2"
        color={where ? "text.primary" : "text.disabled"}
        noWrap
      >
        {where ?? EMPTY}
      </Typography>
    </Cell>
  );
}
