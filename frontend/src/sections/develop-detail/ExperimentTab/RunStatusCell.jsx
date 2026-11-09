import React from "react";
import { runLedgerLine } from "src/sections/common/Runs/runLedger";
import PropTypes from "prop-types";
import { LinearProgress, Stack, Typography } from "@mui/material";
import RunStatusChip, {
  ETA_MIN_DONE,
  shortEta,
} from "src/sections/common/Runs/RunStatusChip";
import useRunActions from "src/sections/common/Runs/useRunActions";
import {
  getRunStatus,
  runProgressOf,
} from "src/sections/common/Runs/runStatus";
import { mostActiveRun } from "./leaderboardModel";
import PlaybackTag from "src/sections/common/Runs/PlaybackTag";
import { useCell } from "./cellContext";
import { canRunOf } from "./leaderboardAccess";

function RunStatusWithActions({ run, canEdit }) {
  const actions = useRunActions(run);
  return (
    <RunStatusChip
      run={run}
      compact
      actions={
        canEdit
          ? {
              onStop: actions.stop,
              onResume: actions.resume,
              busy: actions.pending,
            }
          : undefined
      }
    />
  );
}

RunStatusWithActions.propTypes = {
  run: PropTypes.object.isRequired,
  canEdit: PropTypes.bool,
};

/**
 * Critic r3 #3: progress sits UNDER the chip (bar + `14/48 · ~2 min left`),
 * so the column never clips it at 1280 / 1440. The bar shrinks first, then
 * the ETA ellipsises (the chip's tooltip carries it in full); the count
 * never cuts.
 */
export function RunProgressLine({ run }) {
  const info = getRunStatus(run);
  const prog = runProgressOf(run);
  const show =
    prog.total > 0 &&
    (info.active || info.waiting || prog.finished < prog.total);
  if (!show) return null;
  const eta =
    info.active && prog.done >= ETA_MIN_DONE ? shortEta(prog.eta_s) : null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      data-testid="run-progress-line"
      sx={{ minWidth: 0, maxWidth: "100%", height: 16 }}
    >
      <LinearProgress
        variant="determinate"
        value={prog.pct}
        color={info.key === "Cancelled" ? "inherit" : "primary"}
        // L2 G2 (a11y #8b): named, with the count as its value text.
        aria-label={`Run ${run?.label ?? ""} progress`.replace(/\s+/g, " ")}
        aria-valuetext={`${prog.finished} of ${prog.total} scored`}
        sx={{
          flex: "0 1 60px",
          minWidth: 24,
          height: 4,
          borderRadius: 2,
          bgcolor: "action.hover",
          color: "text.disabled",
        }}
      />
      <Stack direction="row" gap={0.5} sx={{ minWidth: 0 }}>
        <Typography
          typography="s3"
          color="text.secondary"
          // W1-6: the one ledger line of the run on hover.
          title={runLedgerLine(run) || undefined}
          data-testid="run-progress-count"
          sx={{
            fontVariantNumeric: "tabular-nums",
            whiteSpace: "nowrap",
            flexShrink: 0,
          }}
        >
          {`${prog.finished}/${prog.total}`}
        </Typography>
        {eta && (
          <Typography
            typography="s3"
            color="text.secondary"
            noWrap
            sx={{ minWidth: 0 }}
          >
            {`· ${eta}`}
          </Typography>
        )}
        {/* L2 G2 (S2): the clock plays back faster than real time. */}
        {info.active ? <PlaybackTag run={run} /> : null}
      </Stack>
    </Stack>
  );
}

RunProgressLine.propTypes = { run: PropTypes.object.isRequired };

/**
 * Status column: the run's chip (hover Stop / Resume, full ETA in
 * its tooltip) with live progress under it (`10/48 · ~2 min left`). A group
 * row shows its most active run's state and how many runs share it.
 */
const RunStatusCell = (params) => {
  const { node, data, context } = useCell(params);
  if (node.group) {
    const runs = (node.allLeafChildren ?? [])
      .map((n) => n.data)
      .filter(Boolean);
    const lead = mostActiveRun(runs);
    if (!lead) return null;
    const key = getRunStatus(lead).key;
    const same = runs.filter((r) => getRunStatus(r).key === key).length;
    return (
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ height: "100%" }}
      >
        <RunStatusChip run={lead} compact />
        {runs.length > 1 && same < runs.length && (
          <Typography typography="s3" color="text.secondary" noWrap>
            {`${same} of ${runs.length}`}
          </Typography>
        )}
      </Stack>
    );
  }
  if (!data) return null;
  return (
    <Stack
      justifyContent="center"
      gap={0.5}
      // AG Grid gives cells the row's line height (39 px); reset it so the
      // chip line stays 20 px and the progress line fits under it.
      sx={{ height: "100%", minWidth: 0, width: "100%", lineHeight: "20px" }}
    >
      {node.rowPinned ? (
        <RunStatusChip run={data} compact />
      ) : (
        // L7c g5 (access:ws-level): Stop / Resume start or end runs, so
        // they need the run permission (Edit + run), not edit alone.
        <RunStatusWithActions run={data} canEdit={canRunOf(context)} />
      )}
      {!node.rowPinned && <RunProgressLine run={data} />}
    </Stack>
  );
};

RunStatusCell.propTypes = {
  node: PropTypes.object,
  data: PropTypes.object,
  context: PropTypes.object,
};

export default RunStatusCell;
