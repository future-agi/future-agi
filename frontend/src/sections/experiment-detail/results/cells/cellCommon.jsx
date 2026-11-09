import React from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { STATUS_STYLES } from "src/sections/common/Runs/runStatus";

// Shared bits of the results grid cells (PRODUCT-SPEC §3.10.7).

/** Why a run has no result for a row: outside its split or its version. */
export function notInRunLabel(run) {
  const names = (run?.splits ?? []).map((sp) => sp.name).filter(Boolean);
  if (names.length) return `Not in split ${names.join(", ")}`;
  if (run?.dataset_version?.number)
    return `Not in v${run.dataset_version.number}`;
  return "Not in this run";
}

const DONE = new Set(["Completed", "Cancelled", "Failed", "PartialCompleted"]);

/** True while a run still waits for results from the user's own terminal. */
export const waitingForUser = (run) =>
  (run?.progress?.status ?? run?.status) === "WaitingForResults";

/**
 * iterate r2 G3 (P9): a queued cell — L7c g6 (after:waiting-cells): a run
 * that waits for results says the run status's own words (the leaderboard
 * chip, banner and footer: `Waiting for results`), else `Queued`.
 */
export const queuedLabel = (run) => {
  if (waitingForUser(run))
    return STATUS_STYLES.WaitingForResults?.label ?? "Waiting for results";
  // options-truth L3 G3 (R4): a stopped run never reaches its other rows.
  const why = `${run?.stop_reason ?? ""} ${run?.status_detail ?? ""} ${run?.progress?.status_detail ?? ""}`;
  if (/cost cap/i.test(why)) return "Not run · stopped at cost cap";
  if (stoppedRun(run)) return "Not run · stopped";
  return "Queued";
};

/** True when a run ended before every row ran (cap, cancel, stop). */
export function stoppedRun(run) {
  const st = run?.progress?.status ?? run?.status;
  return (
    Boolean(run?.stop_reason) ||
    st === "Stopped" ||
    st === "Cancelled" ||
    (st === "PartialCompleted" &&
      /stop/i.test(String(run?.status_detail ?? "")))
  );
}

/** `Carried from #1A` for a result a carry-over brought in, else null. */
export const carriedLabel = (result) =>
  result?.carried_from ? `Carried from ${result.carried_from}` : null;

/** True while a run is still producing or scoring results. */
export const runUnfinished = (run) => {
  const st = run?.progress?.status ?? run?.status;
  return Boolean(st) && !DONE.has(st);
};

/** The other filled field names of an object (after the primary one). */
export function extraFieldNames(value) {
  if (!value || typeof value !== "object") return [];
  const filled = Object.entries(value).filter(
    ([, v]) =>
      v != null &&
      !(typeof v === "string" && !v.trim()) &&
      !(Array.isArray(v) && !v.length),
  );
  const primary = filled.find(([, v]) => typeof v === "string" && v.trim());
  return filled.map(([k]) => k).filter((k) => k !== primary?.[0]);
}

/**
 * P9: why a score cell has no score — `no expected value`, `run not
 * finished`, else `the eval returned no score`.
 */
export function notScoredReason({ run, row, def, score }) {
  // options-truth L3 G3: a composite whose earlier check failed.
  if (score?.not_reached) return "not reached: an earlier check failed";
  if (runUnfinished(run)) return "run not finished";
  const exp = row?.expected?.value;
  const hasExpected =
    exp &&
    typeof exp === "object" &&
    Object.values(exp).some((v) => v != null && String(v).trim() !== "");
  if (def?.needs_expected !== false && !hasExpected) return "no expected value";
  return "the eval returned no score";
}

/** Lines that fit a row of `height` px at the cell's 20 px line height. */
export const linesFor = (height, reserved = 0) =>
  Math.max(1, Math.floor(((height ?? 100) - 16 - reserved) / 20));

/** First non-empty string field of an object, else a JSON preview. */
export function primaryText(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value !== "object") return String(value);
  const entries = Object.entries(value);
  const str = entries.find(([, v]) => typeof v === "string" && v.trim());
  if (str) return str[1];
  const first = entries.find(([, v]) => v != null);
  return first ? JSON.stringify(first[1]) : "";
}

/** Count of the other non-empty fields of an object. */
export function extraFieldCount(value) {
  if (!value || typeof value !== "object") return 0;
  const filled = Object.values(value).filter(
    (v) =>
      v != null &&
      !(typeof v === "string" && !v.trim()) &&
      !(Array.isArray(v) && !v.length),
  );
  return Math.max(0, filled.length - 1);
}

/**
 * `text` with every case-insensitive match of `query` wrapped in <mark>
 * (RC#11 search highlight). Pure; exported for tests.
 */
export function highlightParts(text, query) {
  const s = String(text ?? "");
  const q = String(query ?? "").trim();
  if (!q || !s) return [{ text: s, hit: false }];
  const out = [];
  const lower = s.toLowerCase();
  const needle = q.toLowerCase();
  let i = 0;
  for (;;) {
    const at = lower.indexOf(needle, i);
    if (at < 0) break;
    if (at > i) out.push({ text: s.slice(i, at), hit: false });
    out.push({ text: s.slice(at, at + q.length), hit: true });
    i = at + q.length;
  }
  if (i < s.length) out.push({ text: s.slice(i), hit: false });
  return out;
}

const markSx = {
  bgcolor: (t) => `${t.palette.warning.main}3D`,
  color: "inherit",
  borderRadius: "2px",
  px: "1px",
};

/** Text clamped to the row height (13 px / 20 px, like the dataset grid). */
export const ClampedText = ({
  text,
  lines,
  color = "text.primary",
  sx,
  highlight,
}) => (
  <Typography
    typography="s2_1"
    color={color}
    sx={{
      display: "-webkit-box",
      WebkitLineClamp: lines,
      WebkitBoxOrient: "vertical",
      overflow: "hidden",
      whiteSpace: "pre-wrap",
      wordBreak: "break-word",
      ...sx,
    }}
  >
    {highlight && typeof text === "string"
      ? highlightParts(text, highlight).map((p, i) =>
          p.hit ? (
            // eslint-disable-next-line react/no-array-index-key
            <Box component="mark" key={i} sx={markSx}>
              {p.text}
            </Box>
          ) : (
            p.text
          ),
        )
      : text}
  </Typography>
);

ClampedText.propTypes = {
  text: PropTypes.node,
  lines: PropTypes.number,
  color: PropTypes.string,
  sx: PropTypes.object,
  highlight: PropTypes.string,
};

/** Muted one-line state text (`Queued`, `Not in v5`). */
export const MutedText = ({ children, icon }) => (
  <Box
    sx={{
      display: "flex",
      alignItems: "center",
      gap: 0.5,
      color: "text.disabled",
      minWidth: 0,
    }}
  >
    {icon && <Iconify icon={icon} width={14} />}
    <Typography typography="s2" color="text.disabled" noWrap>
      {children}
    </Typography>
  </Box>
);

MutedText.propTypes = { children: PropTypes.node, icon: PropTypes.string };

/** Small hover action in a cell corner. */
export const CellAction = ({ title, icon, onClick, className }) => (
  <CustomTooltip show size="small" placement="top" arrow title={title}>
    <IconButton
      className={className}
      size="small"
      aria-label={title}
      onClick={(e) => {
        e.stopPropagation();
        onClick?.(e);
      }}
      sx={{
        p: 0.25,
        bgcolor: "background.paper",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      <Iconify icon={icon} width={14} />
    </IconButton>
  </CustomTooltip>
);

CellAction.propTypes = {
  title: PropTypes.string.isRequired,
  icon: PropTypes.string.isRequired,
  onClick: PropTypes.func,
  className: PropTypes.string,
};

/** Hover-revealed actions row, top-right of a cell. */
export const HoverActions = ({ children }) => (
  <Box
    className="result-cell-actions"
    sx={{
      position: "absolute",
      top: 6,
      right: 6,
      display: "flex",
      gap: 0.5,
      opacity: 0,
      transition: "opacity 120ms",
      zIndex: 1,
    }}
  >
    {children}
  </Box>
);

HoverActions.propTypes = { children: PropTypes.node };

export const cellBoxSx = {
  position: "relative",
  height: "100%",
  width: "100%",
  px: 1.25,
  py: 1,
  display: "flex",
  flexDirection: "column",
  gap: 0.5,
  overflow: "hidden",
  "&:hover .result-cell-actions": { opacity: 1 },
};

export const cellPropTypes = {
  data: PropTypes.object,
  context: PropTypes.object,
  node: PropTypes.object,
  runId: PropTypes.string,
  metricKey: PropTypes.string,
};
