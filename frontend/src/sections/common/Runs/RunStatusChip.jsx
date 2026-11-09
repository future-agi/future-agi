import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Chip,
  IconButton,
  LinearProgress,
  Stack,
  Typography,
  keyframes,
} from "@mui/material";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { getRunStatus, etaText, runProgressOf } from "./runStatus";
import { canResumeRun, stoppedAtCostCap } from "./runResume";
import { waitingPhraseOf } from "./runLedger";
import { fmtTimeRelative } from "src/sections/common/format/fmt";

// CR#35: an ETA only once there is enough to estimate from. Exported for
// the leaderboard's stacked status cell (same rule, same words).
export const ETA_MIN_DONE = 5;
export const shortEta = (s) => {
  if (s == null || !Number.isFinite(s) || s < 0) return null;
  if (s < 60) return "<1 min left";
  const m = Math.round(s / 60);
  return m < 60
    ? `~${m} min left`
    : `~${Math.round((s / 3600) * 10) / 10} h left`;
};

const spin = keyframes`
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
`;

/**
 * StatusChip — the status pill alone, moved from ExperimentTab.jsx.
 * Same look (soft chip, 1 px border, 14 px icon spinning while active).
 *
 * @param {object} props
 * @param {string} props.status   backend status (`Running`, `Cancelled`, …)
 * @param {boolean} [props.compact]  20 px tall, 11 px text
 */
export const StatusChip = ({ status, compact = false, label }) => {
  const s = getRunStatus(status);
  return (
    <Chip
      variant="soft"
      label={label ?? s.label}
      size="small"
      icon={
        s.icon ? (
          <SvgColor
            src={s.icon}
            sx={{
              height: compact ? 12 : 14,
              width: compact ? 12 : 14,
              ...(s.spin && { animation: `${spin} 1s linear infinite` }),
            }}
          />
        ) : undefined
      }
      sx={{
        height: compact ? 20 : 24,
        fontSize: compact ? 11 : 12,
        fontWeight: 500,
        // IT-R5 G6 P1: the pill never truncates (`Running · 23/312`).
        flexShrink: 0,
        maxWidth: "none",
        "& .MuiChip-label": { overflow: "visible", textOverflow: "clip" },
        border: "1px solid",
        borderColor: (t) =>
          t.palette.mode === "dark" ? s.darkBorder ?? s.border : s.border,
        backgroundColor: (t) =>
          t.palette.mode === "dark" ? s.darkBg ?? s.bg : s.bg,
        color: (t) => (t.palette.mode === "dark" ? s.darkColor : s.color),
        "& .MuiChip-icon": {
          color: (t) => (t.palette.mode === "dark" ? s.darkColor : s.color),
        },
        "&:hover": {
          backgroundColor: (t) =>
            t.palette.mode === "dark" ? s.darkBg ?? s.bg : s.bg,
        },
      }}
    />
  );
};

StatusChip.propTypes = {
  status: PropTypes.string,
  compact: PropTypes.bool,
  // IT-R5 G6 P1 (additive): words from the run's own progress
  // (`Running · 23/312`); default the status label.
  label: PropTypes.string,
};

const statusTooltip = (run, info, prog) => {
  const lines = [];
  const head = [run?.label, info.label].filter(Boolean).join(" · ");
  if (head) lines.push(head);
  // R2D-36: what the run is doing right now (`Starting sandboxes 5 of 5`).
  if (run?.status_detail) lines.push(run.status_detail);
  if (info.active && prog.total)
    lines.push(
      `${prog.done} of ${prog.total} examples (${Math.round(prog.pct)}%)`,
    );
  const eta =
    info.active && prog.done >= ETA_MIN_DONE ? etaText(prog.eta_s) : null;
  if (eta) lines.push(eta);
  if (info.active && run?.started_at && prog.done > 0) {
    const secs = (Date.now() - Date.parse(run.started_at)) / 1000;
    if (secs > 0)
      lines.push(
        `${Math.round((prog.done / secs) * 10) / 10} examples/s · started ${fmtTimeRelative(run.started_at)}`,
      );
  }
  if (info.key === "Cancelled" && prog.total)
    lines.push(
      `Stopped at ${prog.done} of ${prog.total} examples · ${prog.left} left`,
    );
  // L7c g5 (state:stopped-at-cost-cap, action:resume:cost-capped): a run
  // its cost cap stopped says so, and why it offers no Resume.
  if (info.key === "Cancelled" && stoppedAtCostCap(run))
    lines.push("No Resume: a resume cannot spend past the run's cost cap.");
  if (info.waiting)
    lines.push(
      `${prog.done} of ${prog.total} results uploaded · ${waitingPhraseOf(run)}`,
    );
  if (prog.errors > 0)
    lines.push(
      `${prog.errors} ${prog.errors === 1 ? "example" : "examples"} errored`,
    );
  if (run?.first_error && (prog.errors > 0 || info.key === "Failed"))
    lines.push(`First error: ${run.first_error}`);
  return lines.length ? (
    <Stack spacing={0.25}>
      {lines.map((l) => (
        <Typography key={l} typography="s2">
          {l}
        </Typography>
      ))}
    </Stack>
  ) : null;
};

const ActionButton = ({ title, icon, onClick, disabled, color }) => (
  <CustomTooltip show title={title} placement="top" size="small" arrow>
    <span>
      <IconButton
        size="small"
        disabled={disabled}
        onClick={(e) => {
          e.stopPropagation();
          onClick?.();
        }}
        sx={{
          p: 0.25,
          borderRadius: 0.5,
          border: "1px solid",
          borderColor: "divider",
          color: color ?? "text.secondary",
        }}
      >
        <Iconify icon={icon} width={14} />
      </IconButton>
    </span>
  </CustomTooltip>
);

ActionButton.propTypes = {
  title: PropTypes.string.isRequired,
  icon: PropTypes.string.isRequired,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
  color: PropTypes.string,
};

/**
 * RunStatusChip — a run's status chip with optional progress and actions
 * (PRODUCT-SPEC §4, moved from ExperimentTab `StatusChip`).
 *
 * Props (frozen):
 * @param {object} props.run  canonical Run (`status`, `progress`, `first_error`)
 * @param {boolean} [props.showProgress]  60 px bar + `142/248` while the run
 *   is not complete; ETA, errors and the first error in the tooltip
 * @param {boolean} [props.compact]  20 px chip
 * @param {{onStop?: Function, onResume?: Function, busy?: boolean}}
 *   [props.actions]  hover buttons: Stop while active, Resume when stopped.
 *   L7c g5 (action:rerun:status-chip-retry): no Retry hover button; its
 *   count named errored rows while it reran every failed or unplayed row.
 *   The run menu's Rerun › Failed rows is the one way to rerun them.
 */
const RunStatusChip = ({
  run,
  showProgress = false,
  compact = false,
  actions,
}) => {
  const info = getRunStatus(run);
  const prog = runProgressOf(run);
  const tooltip = statusTooltip(run, info, prog);
  const showBar =
    showProgress &&
    prog.total > 0 &&
    (info.active || info.waiting || prog.finished < prog.total);

  const canStop = actions?.onStop && info.active;
  // options-truth L7b G4: never Resume a run its cost cap stopped.
  const canResume =
    actions?.onResume &&
    info.key === "Cancelled" &&
    prog.left > 0 &&
    canResumeRun(run);
  const hasActions = canStop || canResume;

  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{
        minWidth: 0,
        "& .run-status-actions": { opacity: 0, transition: "opacity 120ms" },
        "&:hover .run-status-actions, &:focus-within .run-status-actions": {
          opacity: 1,
        },
      }}
    >
      <CustomTooltip
        show={!!tooltip}
        title={tooltip}
        placement="top"
        size="small"
        arrow
      >
        <Box component="span" sx={{ display: "inline-flex" }}>
          {/* L7c g5 (sdk:waiting-copy): a waiting run's chip says what it
              waits for with the one ledger noun (`Waiting for results`, the status's own word). */}
          {/* the capability contract: the chip's colour and word are the run's real
              outcome (info.key), never the raw `Completed` stamp. */}
          <StatusChip
            status={info.key}
            compact={compact}
            label={
              info.waiting
                ? `W${waitingPhraseOf(run).slice(1)}`
                : info.key === "Cancelled" && stoppedAtCostCap(run)
                  ? "Stopped at cost cap"
                  : undefined
            }
          />
        </Box>
      </CustomTooltip>
      {showBar && (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          sx={{ flexShrink: 0 }}
        >
          <LinearProgress
            variant="determinate"
            value={prog.pct}
            color={info.key === "Cancelled" ? "inherit" : "primary"}
            aria-label={`Run ${run?.label ?? ""} progress`.replace(/\s+/g, " ")}
            aria-valuetext={`${prog.finished} of ${prog.total} scored`}
            sx={{
              width: 60,
              height: 4,
              borderRadius: 2,
              bgcolor: "action.hover",
              color: "text.disabled",
            }}
          />
          <Typography
            typography="s3"
            color="text.secondary"
            sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}
          >
            {`${prog.finished}/${prog.total}`}
            {info.active && prog.done >= ETA_MIN_DONE && shortEta(prog.eta_s)
              ? ` · ${shortEta(prog.eta_s)}`
              : ""}
          </Typography>
        </Stack>
      )}
      {hasActions && (
        <Stack direction="row" gap={0.5} className="run-status-actions">
          {canStop && (
            <ActionButton
              title="Stop run"
              icon="material-symbols:stop-rounded"
              color="error.main"
              disabled={actions.busy}
              onClick={actions.onStop}
            />
          )}
          {canResume && (
            <ActionButton
              title={`Resume · ${prog.left} left`}
              icon="material-symbols:play-arrow-rounded"
              disabled={actions.busy}
              onClick={actions.onResume}
            />
          )}
        </Stack>
      )}
    </Stack>
  );
};

RunStatusChip.propTypes = {
  run: PropTypes.shape({
    label: PropTypes.string,
    status: PropTypes.string,
    first_error: PropTypes.string,
    progress: PropTypes.object,
  }),
  showProgress: PropTypes.bool,
  compact: PropTypes.bool,
  actions: PropTypes.shape({
    onStop: PropTypes.func,
    onResume: PropTypes.func,
    busy: PropTypes.bool,
  }),
};

export default RunStatusChip;
