import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import RunChip from "src/sections/common/Runs/RunChip";
import ScoreCell from "src/sections/common/Metrics/ScoreCell";
import DirectionIcon from "src/sections/common/Metrics/DirectionIcon";
import CustomTooltip from "src/components/tooltip";
import { VOCAB } from "src/sections/common/vocab";

/**
 * True when a task row carries per-trial Check glyphs (R3 `full=true`).
 * Rows without a `check:*` glyph (every round-1 dataset) keep the score
 * cells exactly as before.
 */
export function hasTrialGlyphs(row, runs) {
  return (runs ?? []).some((r) =>
    (row?.results?.[r.run_id]?.eval_glyphs ?? []).some((g) =>
      Object.keys(g ?? {}).some((k) => k.startsWith("check:")),
    ),
  );
}

/**
 * Per-trial glyph rows of a task row: Check evals first (`check:*`), then
 * judge evals (`eval:*`), in the order the glyphs list them.
 */
export function glyphEvals(row, runs, metrics = []) {
  if (!hasTrialGlyphs(row, runs)) return [];
  const keys = [];
  (runs ?? []).forEach((r) =>
    (row?.results?.[r.run_id]?.eval_glyphs ?? []).forEach((g) =>
      Object.keys(g ?? {}).forEach((k) => {
        if (
          (k.startsWith("check:") || k.startsWith("eval:")) &&
          !keys.includes(k)
        )
          keys.push(k);
      }),
    ),
  );
  const defOf = (k) => metrics.find((m) => m.key === k);
  // The row's own check list (task rows keep it in `expected`) says what
  // each check asserts on this row: `lookup_order returned A-8830`.
  const rowChecks = [
    ...(Array.isArray(row?.expected?.value?.checks)
      ? row.expected.value.checks
      : []),
    ...(Array.isArray(row?.inputs?.checks) ? row.inputs.checks : []),
  ];
  const describe = (short) => {
    const c = rowChecks.find((x) => x?.key === short);
    const text = c?.description ?? c?.name ?? null;
    if (!text) return null;
    return /^[a-z]+_[a-z_]+\b/.test(text)
      ? text
      : text.charAt(0).toUpperCase() + text.slice(1);
  };
  const toItem = (k) => ({
    key: k,
    short: k.replace(/^(check|eval):/, ""),
    name: describe(k.replace(/^(check|eval):/, "")) ?? defOf(k)?.name ?? null,
    type: k.startsWith("check:") ? "check" : "judge",
  });
  // Checks that never ran on this row (every glyph "–" on every run) are kept:
  // `–` = not reached after an earlier check failed.
  return [
    ...keys.filter((k) => k.startsWith("check:")).map(toItem),
    ...keys
      .filter((k) => k.startsWith("eval:") && k !== "eval:task_success")
      .map(toItem),
  ];
}

const GLYPH_COLOR = {
  "✓": "success.main",
  "✕": "error.main",
  "–": "text.disabled",
};

// L5 G3: a skipped eval says why (`Skipped · no expected value`), never
// `Not scored` (the grid cell reads the same).
function skippedOf(result, evalKey) {
  const all = [
    result?.scores?.[evalKey],
    ...(result?.trials_detail ?? []).map((t) => t?.scores?.[evalKey]),
  ].filter(Boolean);
  if (!all.length || !all.every((s) => s?.status === "skipped")) return null;
  return all.find((s) => s?.skip_reason)?.skip_reason ?? "not scored";
}

function TrialGlyphs({ result, evalKey, type }) {
  const skipped = skippedOf(result, evalKey);
  if (skipped)
    return (
      <Typography
        typography="s2"
        color="text.disabled"
        sx={{ px: 1, py: 0.75 }}
        data-score-skipped=""
      >
        {`Skipped · ${skipped}`}
      </Typography>
    );
  const glyphs = (result?.eval_glyphs ?? []).map((g) => g?.[evalKey] ?? "–");
  if (!glyphs.length)
    return (
      <Typography
        typography="s2"
        color="text.disabled"
        sx={{ px: 1, py: 0.75 }}
      >
        Not scored
      </Typography>
    );
  return (
    <Box sx={{ px: 1, py: 0.75, display: "flex", gap: 0.5 }}>
      {glyphs.map((g, i) => {
        const reason =
          result?.trials_detail?.[i]?.scores?.[evalKey]?.reason ??
          (g === "–" ? "Not reached: an earlier check failed." : null);
        const title = `Trial ${i + 1} · ${g === "✓" ? "passed" : g === "✕" ? "failed" : "not reached"}${type === "judge" && reason ? ` · ${reason}` : g === "–" ? ` · ${reason}` : ""}`;
        return (
          <CustomTooltip
            key={i}
            show
            size="small"
            placement="top"
            arrow
            title={title}
          >
            <Typography
              component="span"
              typography="s1"
              fontWeight="fontWeightSemiBold"
              sx={{
                color: GLYPH_COLOR[g] ?? "text.secondary",
                lineHeight: "18px",
                minWidth: 10,
                textAlign: "center",
              }}
            >
              {g}
            </Typography>
          </CustomTooltip>
        );
      })}
    </Box>
  );
}

TrialGlyphs.propTypes = {
  result: PropTypes.object,
  evalKey: PropTypes.string,
  type: PropTypes.string,
};

/**
 * Scores on the open row: metrics × runs, heat + corner delta (ScoreCell).
 * A cell click opens its ScoreCard.
 */
const ScoreMatrix = ({
  metrics,
  runs,
  colors,
  baselineId,
  row,
  active,
  onPick,
}) => {
  const cols = `minmax(150px, 1.2fr) repeat(${runs.length}, minmax(96px, 1fr))`;
  // Task rows: one glyph per trial, Check evals first; the per-row judge
  // and the task score stay below as cells.
  const glyphRows = glyphEvals(row, runs, metrics);
  const cellMetrics = metrics.filter(
    (m) => !glyphRows.some((g) => g.key === m.key),
  );
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: cols,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      <Box
        sx={{
          px: 1.25,
          py: 0.75,
          bgcolor: "background.neutral",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography typography="s2" color="text.secondary">
          Eval
        </Typography>
      </Box>
      {runs.map((run) => (
        <Box
          key={run.run_id}
          sx={{
            px: 1,
            py: 0.5,
            bgcolor: "background.neutral",
            borderBottom: "1px solid",
            borderLeft: "1px solid",
            borderColor: "divider",
            display: "flex",
            minWidth: 0,
          }}
        >
          <RunChip
            run={run}
            color={colors[run.run_id]}
            isBaseline={run.run_id === baselineId && runs.length > 1}
            showName={false}
            showStatus={false}
          />
        </Box>
      ))}
      {glyphRows.map((ev, i) => (
        <React.Fragment key={`g:${ev.key}`}>
          <Stack
            direction="row"
            alignItems="center"
            gap={0.5}
            sx={{
              px: 1.25,
              py: 0.75,
              borderTop: i ? "1px solid" : "none",
              borderColor: "divider",
              minWidth: 0,
            }}
          >
            <Box
              component="span"
              sx={{
                flexShrink: 0,
                px: 0.5,
                borderRadius: "2px",
                bgcolor: "action.hover",
                color: "text.secondary",
                typography: "s3",
                fontWeight: 500,
                lineHeight: "16px",
              }}
            >
              {ev.type === "check" ? VOCAB.checkChip : VOCAB.judgeChip}
            </Box>
            <CustomTooltip
              show
              size="small"
              placement="top"
              arrow
              title={ev.name ? `${ev.short} · ${ev.name}` : ev.short}
            >
              <Typography typography="s2" noWrap>
                {ev.short}
                {ev.name ? (
                  <Box component="span" sx={{ color: "text.secondary" }}>
                    {` · ${ev.name}`}
                  </Box>
                ) : null}
              </Typography>
            </CustomTooltip>
          </Stack>
          {runs.map((run) => (
            <Box
              key={run.run_id}
              sx={{
                borderTop: i ? "1px solid" : "none",
                borderLeft: "1px solid",
                borderColor: "divider",
                minHeight: 34,
              }}
            >
              <TrialGlyphs
                result={row.results?.[run.run_id]}
                evalKey={ev.key}
                type={ev.type}
              />
            </Box>
          ))}
        </React.Fragment>
      ))}
      {cellMetrics.map((def, i) => (
        <React.Fragment key={def.key}>
          <Stack
            direction="row"
            alignItems="center"
            gap={0.5}
            sx={{
              px: 1.25,
              py: 0.75,
              borderTop: i || glyphRows.length ? "1px solid" : "none",
              borderColor: "divider",
              minWidth: 0,
            }}
          >
            <Typography typography="s2" noWrap>
              {def.name}
            </Typography>
            <DirectionIcon higherIsBetter={def.higher_is_better} size={12} />
          </Stack>
          {runs.map((run) => {
            const result = row.results?.[run.run_id];
            const score = result?.scores?.[def.key];
            const isActive =
              active?.metric === def.key && active?.run === run.run_id;
            const notScored = !score && result?.status !== "error";
            return (
              <Box
                key={run.run_id}
                role="button"
                tabIndex={0}
                aria-label={`${def.name} ${run.label}`}
                onClick={() =>
                  !notScored && onPick({ metric: def.key, run: run.run_id })
                }
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !notScored)
                    onPick({ metric: def.key, run: run.run_id });
                }}
                sx={{
                  borderTop: i || glyphRows.length ? "1px solid" : "none",
                  borderLeft: "1px solid",
                  borderColor: "divider",
                  cursor: notScored ? "default" : "pointer",
                  outline: isActive ? "2px solid" : "none",
                  outlineColor: "primary.main",
                  outlineOffset: -2,
                  minHeight: 34,
                }}
              >
                {result?.status === "missing" ? (
                  <Typography
                    typography="s2"
                    color="text.disabled"
                    sx={{ px: 1, py: 0.75 }}
                  >
                    Not in this run
                  </Typography>
                ) : (
                  <ScoreCell
                    def={def}
                    value={score?.override?.value ?? score?.value ?? null}
                    display={
                      score?.override ? undefined : score?.display ?? undefined
                    }
                    delta={result?.delta?.[def.key]}
                    movement={result?.movement?.[def.key]}
                    flaky={score?.flaky ? { std: score.std } : false}
                    override={
                      score?.override
                        ? {
                            display: score.override.display,
                            by: "Human correction",
                          }
                        : false
                    }
                    error={
                      result?.status === "error"
                        ? result.error ?? "Run failed"
                        : undefined
                    }
                  />
                )}
              </Box>
            );
          })}
        </React.Fragment>
      ))}
    </Box>
  );
};

ScoreMatrix.propTypes = {
  metrics: PropTypes.array.isRequired,
  runs: PropTypes.array.isRequired,
  colors: PropTypes.object.isRequired,
  baselineId: PropTypes.string,
  row: PropTypes.object.isRequired,
  active: PropTypes.object,
  onPick: PropTypes.func.isRequired,
};

export default ScoreMatrix;
