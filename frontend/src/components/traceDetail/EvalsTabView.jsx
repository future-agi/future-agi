import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Markdown from "react-markdown";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import EvalErrorLocalization from "./EvalErrorLocalization";

// Kept locally so callers that don't supply an onFixWithFalcon handler still
// see the informational toast — preserves pre-integration behavior.
function defaultFixNotice() {
  enqueueSnackbar("Fix with Falcon — not available in this view", {
    variant: "info",
  });
}

/**
 * Shared evals tab view — the visual presentation originally lived inside
 * src/components/traceDetail/SpanDetailPane.jsx as EvalsTabContent. It was
 * extracted here so both the trace detail drawer and the voice drawer can
 * render identical eval tables.
 *
 * The component is data-shape agnostic: callers pass a pre-normalized list
 * of eval rows matching the `EvalRow` shape below. Use
 * `collectAllEvalsFromEntry` for the trace drawer's span-subtree case, or
 * map your own data into the canonical shape for other drawers.
 */

/**
 * EvalRow shape (each item in the `evals` prop):
 * {
 *   id:           string (unique, used as React key)
 *   eval_name:    string — primary display name
 *   score:        number 0-100 | null — drives the colored badge
 *   score_label?: string — optional override for the score cell (e.g. "Pass")
 *   explanation?: string — markdown explanation, expands inline
 *   spanName?:    string — optional span context
 *   spanId?:      string — optional, enables "View span" action
 * }
 */

/**
 * Walk a trace entry subtree and flatten all eval_scores into a flat list.
 * Keeps the trace drawer's existing behavior — exported so SpanDetailPane
 * can continue using it without duplication.
 */
export function collectAllEvalsFromEntry(entry) {
  const rows = [];
  function walk(e, spanId) {
    const s = e?.observation_span || {};
    const evals = e?.eval_scores || [];
    for (const ev of evals) {
      rows.push({
        // Spread the raw eval so error_analysis, cell_id, selected_input_key,
        // input_data, input_types, error_localizer_status flow through to
        // EvalTableRow → EvalErrorLocalization automatically.
        ...ev,
        id: `${s.id || spanId || "root"}-${ev.eval_config_id || ev.eval_name || rows.length}`,
        spanId: s.id || spanId,
        spanName: s.name || "unnamed",
        spanType: s.observation_type || "unknown",
      });
    }
    if (e?.children?.length) {
      for (const child of e.children) walk(child, null);
    }
  }
  walk(entry, null);
  return rows;
}

// `score >= 50` matches the summary's totalPass threshold. Some evals
// (label-based pass/fail) may come through with no numeric score — fall back
// to the textual label so we still hide Fix on those. Anything else (null
// score, arbitrary label) is treated as not-passed and keeps the button.
const isPassedEval = (ev) => {
  if (ev?.score != null) return ev.score >= 50;
  const label = (ev?.score_label || "").trim().toLowerCase();
  return label === "pass" || label === "passed" || label === "true";
};

/** Score → colored background + text — traffic-light pattern */
export function scoreColor(score) {
  if (score == null)
    return {
      bg: (theme) => alpha(theme.palette.text.disabled, 0.08),
      text: "text.disabled",
    };
  if (score >= 80)
    return {
      bg: (theme) => alpha(theme.palette.success.main, 0.08),
      text: "success.dark",
    };
  if (score >= 50)
    return {
      bg: (theme) => alpha(theme.palette.warning.main, 0.08),
      text: "warning.dark",
    };
  return {
    bg: (theme) => alpha(theme.palette.error.main, 0.08),
    text: "error.main",
  };
}

/** Single eval row with collapsible explanation + optional "View span" */
const EvalTableRow = ({ ev, onSelectSpan, showSpanColumn, onFixWithFalcon }) => {
  const [expanded, setExpanded] = useState(false);
  const sc = scoreColor(ev.score);
  const evalName = ev.eval_name || ev.eval_config_id || "Eval";
  const explanation = ev.explanation || ev.eval_explanation;
  const scoreLabel =
    ev.score_label != null
      ? ev.score_label
      : ev.score != null
        ? `${ev.score}%`
        : "—";

  // Error localization visibility — surfaced for every eval that has
  // enough identifiers to drive either the cell-based or trace-based
  // flow. Rows with just an explanation still expand without it.
  const initialAnalysis = ev.error_analysis || ev.errorAnalysis || null;
  const cellId = ev.cell_id || ev.cellId;
  const observationSpanId =
    ev.observation_span_id || ev.observationSpanId || ev.spanId || ev.span_id;
  const customEvalConfigId =
    ev.custom_eval_config_id ||
    ev.customEvalConfigId ||
    ev.eval_config_id ||
    ev.evalConfigId;
  const projectVersionId =
    ev.project_version_id || ev.projectVersionId || ev.version_id;
  const initialStatus =
    ev.error_localizer_status || ev.errorLocalizerStatus || null;
  const hasErrorLocalization =
    !!initialAnalysis ||
    !!cellId ||
    !!initialStatus ||
    !!(observationSpanId && customEvalConfigId);
  const canExpand = !!explanation || hasErrorLocalization;

  return (
    <>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          px: 1.5,
          py: 0.5,
          borderBottom: "1px solid",
          borderColor: "divider",
          "&:hover": { bgcolor: "rgba(0,0,0,0.02)" },
          minHeight: 32,
        }}
      >
        {/* Expand chevron */}
        <Box
          onClick={() => canExpand && setExpanded((p) => !p)}
          sx={{
            width: 20,
            flexShrink: 0,
            cursor: canExpand ? "pointer" : "default",
          }}
        >
          {canExpand && (
            <Iconify
              icon={expanded ? "mdi:chevron-down" : "mdi:chevron-right"}
              width={14}
              color="text.disabled"
            />
          )}
        </Box>

        {/* Eval name — widens to fill space when Span column is hidden */}
        <Typography
          noWrap
          onClick={() => canExpand && setExpanded((p) => !p)}
          sx={{
            width: showSpanColumn ? "30%" : "60%",
            fontSize: 11.5,
            fontWeight: 500,
            cursor: canExpand ? "pointer" : "default",
          }}
        >
          {evalName}
        </Typography>

        {/* Score with colored bg */}
        <Box sx={{ width: "15%" }}>
          <Typography
            sx={{
              display: "inline-block",
              fontSize: 11.5,
              fontWeight: 600,
              color: sc.text,
              bgcolor: sc.bg,
              px: 0.75,
              py: 0.15,
              borderRadius: "3px",
              minWidth: 36,
            }}
          >
            {scoreLabel}
          </Typography>
        </Box>

        {/* Span name — hidden for single-call views (voice drawer) */}
        {showSpanColumn && (
          <Typography
            noWrap
            sx={{ width: "30%", fontSize: 10.5, color: "text.secondary" }}
          >
            {ev.spanName || ""}
          </Typography>
        )}

        {/* Action buttons */}
        <Box
          sx={{
            width: "25%",
            display: "flex",
            gap: 0.5,
            justifyContent: "flex-end",
            flexShrink: 0,
          }}
        >
          {ev.spanId && onSelectSpan && (
            <Box
              onClick={(e) => {
                e.stopPropagation();
                onSelectSpan(ev.spanId);
              }}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                gap: "3px",
                px: 0.5,
                py: 0.15,
                borderRadius: "3px",
                cursor: "pointer",
                fontSize: 10,
                color: "text.disabled",
                "&:hover": {
                  color: "primary.main",
                  bgcolor: "rgba(120,87,252,0.06)",
                },
              }}
            >
              <Iconify icon="mdi:eye-outline" width={12} />
              <span>View span</span>
            </Box>
          )}
        </Box>
      </Box>

      {/* Expanded area — markdown explanation + error localization +
          Fix with Falcon. Renders whenever any of those pieces exist. */}
      {expanded && canExpand && (
        <Box
          sx={{
            px: 1.5,
            pl: 4.5,
            py: 0.75,
            bgcolor: "background.default",
            borderBottom: "1px solid",
            borderColor: "divider",
            display: "flex",
            flexDirection: "column",
            gap: 1,
          }}
        >
          {explanation && (
            <Box
              sx={{
                fontSize: 11,
                color: "text.secondary",
                lineHeight: 1.6,
                "& p": { m: 0, mb: 0.5 },
                "& ul, & ol": { m: 0, pl: 2 },
                "& li": { mb: 0.25 },
                "& strong": { fontWeight: 600, color: "text.primary" },
                "& code": {
                  bgcolor: (theme) => alpha(theme.palette.text.disabled, 0.15),
                  px: 0.5,
                  borderRadius: "2px",
                  fontSize: 10,
                },
              }}
            >
              <Markdown>{explanation}</Markdown>
            </Box>
          )}

          {/* Error localization section — shows run-on-demand button when
              not yet run, spinner while running, or the highlighted input
              segments when complete. Supports both the cell-based flow
              (dataset / simulate) and the trace-based flow (observe
              drawer) via different IDs. */}
          {hasErrorLocalization && (
            <EvalErrorLocalization
              cellId={cellId}
              observationSpanId={observationSpanId}
              customEvalConfigId={customEvalConfigId}
              projectVersionId={projectVersionId}
              initialAnalysis={initialAnalysis}
              initialStatus={initialStatus}
              datapoint={ev.datapoint}
              selectedInputKey={ev.selected_input_key || ev.selectedInputKey}
            />
          )}

          {/* Fix with Falcon — hidden for passed evals (nothing to fix);
              shown for failed and unscored rows whenever expanded. */}
          {!isPassedEval(ev) && (
            <Box
              onClick={(e) => {
                e.stopPropagation();
                if (onFixWithFalcon) {
                  onFixWithFalcon({ level: "eval", ev });
                } else {
                  defaultFixNotice();
                }
              }}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                gap: 0.5,
                px: 0.75,
                py: 0.25,
                alignSelf: "flex-start",
                border: "1px solid",
                borderColor: (theme) => alpha(theme.palette.primary.main, 0.4),
                borderRadius: "4px",
                cursor: "pointer",
                bgcolor: (theme) => alpha(theme.palette.primary.main, 0.06),
                "&:hover": {
                  bgcolor: (theme) => alpha(theme.palette.primary.main, 0.12),
                },
              }}
            >
              <Iconify icon="mdi:creation" width={12} color="primary.main" />
              <Typography
                sx={{ fontSize: 10, fontWeight: 600, color: "primary.main" }}
              >
                Fix with Falcon
              </Typography>
            </Box>
          )}
        </Box>
      )}
    </>
  );
};

EvalTableRow.propTypes = {
  ev: PropTypes.object.isRequired,
  onSelectSpan: PropTypes.func,
  showSpanColumn: PropTypes.bool,
  onFixWithFalcon: PropTypes.func,
};

/**
 * Main shared view. Renders:
 *  - summary bar (pass/fail counts, progress, optional "Fix with Falcon")
 *  - search + "Add Evals" toolbar
 *  - sticky table header + scrollable rows
 */
const EvalsTabView = ({
  evals,
  onSelectSpan,
  emptyMessage,
  showSpanColumn = true,
  onFixWithFalcon,
}) => {
  const [search, setSearch] = useState("");
  const list = useMemo(() => (Array.isArray(evals) ? evals : []), [evals]);

  const totalPass = useMemo(
    () => list.filter((e) => e.score != null && e.score >= 50).length,
    [list],
  );
  const totalFail = useMemo(
    () => list.filter((e) => e.score != null && e.score < 50).length,
    [list],
  );
  const passRate =
    list.length > 0 ? Math.round((totalPass / list.length) * 100) : 0;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return list;
    return list.filter(
      (e) =>
        (e.eval_name || "").toLowerCase().includes(q) ||
        (e.eval_config_id || "").toLowerCase().includes(q) ||
        (e.spanName || "").toLowerCase().includes(q),
    );
  }, [list, search]);

  if (list.length === 0) {
    return (
      <Box sx={{ textAlign: "center", py: 4, px: 2, color: "text.secondary" }}>
        <Iconify
          icon="mdi:chart-box-outline"
          width={32}
          sx={{ mb: 1, opacity: 0.4 }}
        />
        <Typography variant="body2" fontSize={12}>
          {emptyMessage || "No evaluations available"}
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Summary bar */}
      <Box
        sx={{
          px: 1.5,
          py: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 0.5 }}>
          <Box sx={{ flex: 1 }}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.75,
                mb: 0.5,
              }}
            >
              <Typography sx={{ fontSize: 13, fontWeight: 600 }}>
                {totalPass}/{list.length} passed
              </Typography>
              {totalFail > 0 && (
                <Typography
                  sx={{
                    fontSize: 11,
                    color: "error.main",
                    fontWeight: 500,
                    bgcolor: (theme) => alpha(theme.palette.error.main, 0.08),
                    px: 0.5,
                    py: 0.1,
                    borderRadius: "3px",
                  }}
                >
                  {totalFail} failed
                </Typography>
              )}
            </Box>
            <Box
              sx={{
                height: 4,
                borderRadius: 2,
                bgcolor: (theme) => alpha(theme.palette.text.disabled, 0.12),
                overflow: "hidden",
              }}
            >
              <Box
                sx={{
                  height: "100%",
                  width: `${passRate}%`,
                  bgcolor:
                    passRate >= 80
                      ? "success.main"
                      : passRate >= 50
                        ? "warning.main"
                        : "error.main",
                  borderRadius: 2,
                  transition: "width 300ms",
                }}
              />
            </Box>
          </Box>
        </Box>

        {/* Summary-bar Fix with Falcon — hidden when every eval passed
            (nothing failing to escalate). Uses isPassedEval so unscored /
            label-based non-passes also keep the button visible (e.g. an
            eval with score=null and no Pass label still shows up as
            non-passing in the "X/N passed" text). */}
        {list.some((e) => !isPassedEval(e)) && (
          <Box
            onClick={() => {
              if (onFixWithFalcon) {
                const failing = list.filter((e) => !isPassedEval(e));
                onFixWithFalcon({
                  level: "span",
                  failingEvals: failing,
                  allEvals: list,
                });
              } else {
                defaultFixNotice();
              }
            }}
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 0.5,
              mt: 1,
              px: 1,
              py: 0.35,
              border: "1px solid",
              borderColor: (theme) => alpha(theme.palette.primary.main, 0.4),
              borderRadius: "6px",
              cursor: "pointer",
              bgcolor: (theme) => alpha(theme.palette.primary.main, 0.06),
              "&:hover": {
                bgcolor: (theme) => alpha(theme.palette.primary.main, 0.12),
                borderColor: (theme) => alpha(theme.palette.primary.main, 0.5),
              },
            }}
          >
            <Iconify icon="mdi:creation" width={14} color="primary.main" />
            <Typography
              sx={{ fontSize: 11, fontWeight: 600, color: "primary.main" }}
            >
              Fix with Falcon
            </Typography>
          </Box>
        )}
      </Box>

      {/* Search + Add Evals */}
      <Box
        sx={{
          px: 1.5,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
          display: "flex",
          gap: 0.75,
          alignItems: "center",
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            flex: 1,
            px: 0.75,
            py: 0.25,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
          }}
        >
          <Iconify icon="mdi:magnify" width={12} color="text.disabled" />
          <Box
            component="input"
            placeholder="Search evals..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            sx={{
              border: "none",
              outline: "none",
              flex: 1,
              fontSize: 11,
              color: "text.primary",
              bgcolor: "transparent",
              py: 0.15,
              "&::placeholder": { color: "text.disabled" },
            }}
          />
        </Box>
        <Box
          onClick={() =>
            enqueueSnackbar("Add Evals — coming soon", { variant: "info" })
          }
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            px: 1,
            py: 0.35,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            cursor: "pointer",
            bgcolor: "background.paper",
            flexShrink: 0,
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          <Iconify
            icon="mdi:plus-circle-outline"
            width={13}
            color="text.secondary"
          />
          <Typography
            sx={{ fontSize: 11, fontWeight: 500, color: "text.secondary" }}
          >
            Add Evals
          </Typography>
        </Box>
      </Box>

      {/* Table */}
      <Box sx={{ flex: 1, overflow: "auto" }}>
        <Box
          sx={{
            display: "flex",
            px: 1.5,
            py: 0.5,
            bgcolor: "background.default",
            borderBottom: "1px solid",
            borderColor: "divider",
            position: "sticky",
            top: 0,
            zIndex: 1,
          }}
        >
          <Box sx={{ width: 20, flexShrink: 0 }} />
          <Typography
            sx={{
              width: showSpanColumn ? "30%" : "60%",
              fontSize: 11,
              fontWeight: 600,
              color: "text.secondary",
              display: "flex",
              alignItems: "center",
              gap: 0.5,
            }}
          >
            <Iconify icon="mdi:checkbox-marked-circle-outline" width={12} />
            Evaluation metric
          </Typography>
          <Typography
            sx={{
              width: "15%",
              fontSize: 11,
              fontWeight: 600,
              color: "text.secondary",
            }}
          >
            Score
          </Typography>
          {showSpanColumn && (
            <Typography
              sx={{
                width: "30%",
                fontSize: 11,
                fontWeight: 600,
                color: "text.secondary",
              }}
            >
              Span
            </Typography>
          )}
          <Box sx={{ width: "25%" }} />
        </Box>

        {filtered.map((ev) => (
          <EvalTableRow
            key={ev.id || ev.eval_name}
            ev={ev}
            onSelectSpan={onSelectSpan}
            showSpanColumn={showSpanColumn}
            onFixWithFalcon={onFixWithFalcon}
          />
        ))}
      </Box>
    </Box>
  );
};

EvalsTabView.propTypes = {
  evals: PropTypes.array,
  onSelectSpan: PropTypes.func,
  emptyMessage: PropTypes.string,
  showSpanColumn: PropTypes.bool,
  onFixWithFalcon: PropTypes.func,
};

export default EvalsTabView;
