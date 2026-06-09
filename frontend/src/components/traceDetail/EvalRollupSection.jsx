import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Chip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  classifyChoice,
  CHOICE_TONE,
} from "src/sections/projects/LLMTracing/evalTaskMock";

// ---------------------------------------------------------------------------
// FR-4 — "Span evaluations" rollup section for the TRACE DETAIL Evals panel.
// Additive: rendered above the existing (unchanged) eval list. Groups the
// trace's span eval results by eval name and rolls them up — Boolean as
// Pass X / Fail Y, Choice as label chips with real counts (never %), Numeric
// as mean + min/max. Each row expands to the contributing spans with
// click-through. Chips use the Future AGI standard outlined chip.
// ---------------------------------------------------------------------------

const PASS = 50;
const lower = (v) => String(v == null ? "" : v).toLowerCase();

const TONE = {
  [CHOICE_TONE.GOOD]: { border: "green.500", color: "green.500" },
  [CHOICE_TONE.PARTIAL]: { border: "warning.main", color: "warning.dark" },
  [CHOICE_TONE.BAD]: { border: "red.500", color: "red.500" },
};

const StdChip = ({ label, tone, borderColor, color }) => {
  const t = tone ? TONE[tone] || TONE[CHOICE_TONE.BAD] : null;
  return (
    <Chip
      size="small"
      label={label}
      variant="outlined"
      sx={{
        borderRadius: (theme) => theme.spacing(0.5),
        borderColor: borderColor || t?.border || "divider",
        color: color || t?.color || "text.primary",
        fontWeight: 400,
        typography: "s3",
        height: 20,
      }}
    />
  );
};
StdChip.propTypes = {
  label: PropTypes.string,
  tone: PropTypes.string,
  borderColor: PropTypes.string,
  color: PropTypes.string,
};

export const TYPE = {
  PERCENT: "percent",
  PASS_FAIL: "passfail",
  CHOICE: "choice",
};

const labelOf = (r) =>
  r.score_label ?? (Array.isArray(r.score_items) ? r.score_items[0] : null);

export function inferType(rows) {
  if (rows.some((r) => Array.isArray(r.score_items) && r.score_items.length))
    return TYPE.CHOICE;
  const labels = rows.map((r) => lower(r.score_label));
  if (
    labels.some((l) =>
      ["pass", "passed", "fail", "failed", "true", "false"].includes(l),
    )
  )
    return TYPE.PASS_FAIL;
  if (rows.some((r) => r.score != null && Number.isFinite(Number(r.score))))
    return TYPE.PERCENT;
  if (rows.some((r) => r.score_label)) return TYPE.CHOICE;
  return TYPE.PERCENT;
}

const rowErrored = (r) => r?.error === true || lower(r?.result) === "error";
const rowPass = (r) => {
  if (r.score != null) return Number(r.score) >= PASS;
  const l = lower(r.score_label);
  return l === "pass" || l === "passed" || l === "true";
};

// Inline rollup result for a group of span rows.
export const RollupResult = ({ type, rows }) => {
  const errored = rows.filter(rowErrored);
  const evaluated = rows.filter((r) => !rowErrored(r));

  if (type === TYPE.PASS_FAIL) {
    const pass = evaluated.filter(rowPass).length;
    const fail = evaluated.length - pass;
    return (
      <Box
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          flexWrap: "wrap",
        }}
      >
        {fail > 0 && (
          <StdChip
            label={`Fail ${fail}`}
            borderColor="red.500"
            color="red.500"
          />
        )}
        {errored.length > 0 && (
          <StdChip
            label={`Errored ${errored.length}`}
            borderColor="warning.main"
            color="warning.dark"
          />
        )}
        {pass > 0 && (
          <StdChip
            label={`Pass ${pass}`}
            borderColor="green.500"
            color="green.500"
          />
        )}
      </Box>
    );
  }

  if (type === TYPE.CHOICE) {
    const counts = new Map();
    for (const r of evaluated) {
      const l = labelOf(r);
      if (l == null) continue;
      counts.set(l, (counts.get(l) || 0) + 1);
    }
    const items = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
    return (
      <Box
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          flexWrap: "wrap",
        }}
      >
        {items.map(([label, count]) => (
          <StdChip
            key={label}
            label={`${label} ${count}`}
            tone={classifyChoice(label)}
          />
        ))}
        {errored.length > 0 && (
          <StdChip
            label={`Errored ${errored.length}`}
            borderColor="warning.main"
            color="warning.dark"
          />
        )}
      </Box>
    );
  }

  // Numeric — mean headline + min/max.
  const scores = evaluated
    .map((r) => Number(r.score))
    .filter((n) => Number.isFinite(n));
  if (!scores.length)
    return (
      <Typography sx={{ fontSize: 12, color: "text.disabled" }}>—</Typography>
    );
  const mean = scores.reduce((a, b) => a + b, 0) / scores.length;
  const min = Math.min(...scores);
  const max = Math.max(...scores);
  return (
    <Box sx={{ display: "inline-flex", alignItems: "center", gap: 0.75 }}>
      <Typography sx={{ fontSize: 12, fontWeight: 600 }}>
        {mean.toFixed(2)}
      </Typography>
      <Typography sx={{ fontSize: 10.5, color: "text.disabled" }}>
        min {min.toFixed(2)} · max {max.toFixed(2)}
      </Typography>
    </Box>
  );
};
RollupResult.propTypes = { type: PropTypes.string, rows: PropTypes.array };

const SpanResult = ({ type, row }) => {
  if (rowErrored(row))
    return (
      <Typography sx={{ fontSize: 11, fontWeight: 600, color: "warning.dark" }}>
        Error
      </Typography>
    );
  if (type === TYPE.PASS_FAIL) {
    const p = rowPass(row);
    return (
      <Typography
        sx={{
          fontSize: 11,
          fontWeight: 700,
          color: p ? "green.500" : "red.500",
        }}
      >
        {p ? "Pass" : "Fail"}
      </Typography>
    );
  }
  if (type === TYPE.CHOICE) {
    const l = labelOf(row);
    return l != null ? (
      <StdChip label={String(l)} tone={classifyChoice(l)} />
    ) : (
      <span>—</span>
    );
  }
  const n = Number(row.score);
  return (
    <Typography sx={{ fontSize: 11, fontWeight: 600 }}>
      {Number.isFinite(n) ? n.toFixed(2) : "—"}
    </Typography>
  );
};
SpanResult.propTypes = { type: PropTypes.string, row: PropTypes.object };

const GroupRow = ({ group, onSelectSpan }) => {
  const [expanded, setExpanded] = useState(false);
  const canExpand = group.rows.length > 0;
  const notEvaluated = group.rows.filter(
    (r) =>
      r.score == null &&
      r.score_label == null &&
      !(Array.isArray(r.score_items) && r.score_items.length) &&
      !rowErrored(r),
  ).length;
  return (
    <>
      <Box
        onClick={() => canExpand && setExpanded((p) => !p)}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 1.5,
          py: 0.6,
          borderBottom: "1px solid",
          borderColor: "divider",
          cursor: canExpand ? "pointer" : "default",
          "&:hover": { bgcolor: "rgba(0,0,0,0.02)" },
        }}
      >
        <Box sx={{ width: 14, flexShrink: 0 }}>
          {canExpand && (
            <Iconify
              icon={expanded ? "mdi:chevron-down" : "mdi:chevron-right"}
              width={14}
              color="text.disabled"
            />
          )}
        </Box>
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography noWrap sx={{ fontSize: 12, fontWeight: 600 }}>
            {group.evalName}
          </Typography>
          <Typography sx={{ fontSize: 10.5, color: "text.secondary" }}>
            Span-level eval — rollup of {group.rows.length} span
            {group.rows.length === 1 ? "" : "s"}
            {notEvaluated > 0 ? ` · +${notEvaluated} not evaluated` : ""}
          </Typography>
        </Box>
        <RollupResult type={group.type} rows={group.rows} />
      </Box>

      {expanded &&
        group.rows.map((row, i) => (
          <Box
            key={row.id || i}
            onClick={(e) => {
              e.stopPropagation();
              if (row.spanId && onSelectSpan) onSelectSpan(row.spanId);
            }}
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
              pl: 4,
              pr: 1.5,
              py: 0.4,
              bgcolor: "background.default",
              borderBottom: "1px solid",
              borderColor: "divider",
              cursor: row.spanId ? "pointer" : "default",
              "&:hover": row.spanId ? { bgcolor: "action.hover" } : {},
            }}
          >
            <Typography
              noWrap
              sx={{
                flex: 1,
                minWidth: 0,
                fontSize: 11,
                color: "text.secondary",
              }}
            >
              {row.spanName || row.spanId || "span"}
            </Typography>
            <SpanResult type={group.type} row={row} />
            {row.spanId && onSelectSpan && (
              <Iconify
                icon="mdi:eye-outline"
                width={12}
                color="text.disabled"
              />
            )}
          </Box>
        ))}
    </>
  );
};
GroupRow.propTypes = { group: PropTypes.object, onSelectSpan: PropTypes.func };

const EvalRollupSection = ({ evals, onSelectSpan }) => {
  const groups = useMemo(() => {
    const byName = new Map();
    for (const r of Array.isArray(evals) ? evals : []) {
      const key = r.eval_name || r.eval_config_id || "eval";
      if (!byName.has(key)) byName.set(key, { evalName: key, rows: [] });
      byName.get(key).rows.push(r);
    }
    return Array.from(byName.values()).map((g) => ({
      ...g,
      type: inferType(g.rows),
    }));
  }, [evals]);

  if (groups.length === 0) return null;

  return (
    <Box
      sx={{ borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
    >
      <Box
        sx={{
          px: 1.5,
          py: 0.75,
          display: "flex",
          alignItems: "center",
          gap: 0.75,
        }}
      >
        <Iconify
          icon="mdi:layers-triple-outline"
          width={14}
          color="purple.500"
        />
        <Typography sx={{ fontSize: 12, fontWeight: 700 }}>
          Span evaluations
        </Typography>
        <Typography sx={{ fontSize: 10.5, color: "text.secondary" }}>
          rolled up across this trace&apos;s spans
        </Typography>
      </Box>
      {groups.map((g) => (
        <GroupRow key={g.evalName} group={g} onSelectSpan={onSelectSpan} />
      ))}
    </Box>
  );
};

EvalRollupSection.propTypes = {
  evals: PropTypes.array,
  onSelectSpan: PropTypes.func,
};

export default EvalRollupSection;
