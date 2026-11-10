import PropTypes from "prop-types";
import { forwardRef } from "react";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import TrialsPicker from "./TrialsPicker";

/**
 * Bulk-action bar for the scenario table.
 *
 * Renders IN-PLACE where the search / group-by / filter toolbar normally lives
 * — same row of the SectionCard, same height, same padding. When one or more
 * scenarios are checked the toolbar transforms into this bar (Gmail /
 * GitHub-changes pattern): one location for both states, so the actions appear
 * where the eye already is.
 *
 * Hierarchy (left → right):
 *   [count] ✕  ·  Select all N matching  |  Delete  Repeats[k▾]  ▶ Run (N)
 *   context       escalation (paging)        destructive  dial  PRIMARY
 *
 * Editing is a per-row action (the pencil opens the edit drawer), not a bulk
 * one, so the bar carries no Edit button.
 *
 * `matching` carries our pagination's predicate-selection context so a
 * selection can escalate to "all N matching" without every id being loaded.
 * Each of `onRun` / `onTrialsChange` is optional and gates its own button.
 * When the environment can't run yet, `runBlockedReason` keeps Run on the bar,
 * disabled, with the reason on hover, rather than leaving it out.
 */
export default function SelectionBar({
  count, trials, onTrialsChange, onRun, runBlockedReason, onDelete, onClear, matching,
}) {
  const k = Math.max(1, Math.min(20, Number(trials) || 1));
  const totalRuns = count * k;
  const estSeconds = totalRuns * 10; // ~10s / run in the mock player
  const timeHint = estSeconds < 60
    ? `~${estSeconds}s`
    : `~${Math.max(1, Math.round(estSeconds / 60))} min`;
  const runLabel = `Run simulation (${count})`;

  // Predicate-selection escalation: in include-mode, offer "select all N
  // matching" once the whole visible page is checked and there's more beyond it.
  const isAll = matching?.mode === "all";
  const canEscalate =
    matching && !isAll && matching.pageCount > 0 && matching.total > matching.pageCount;

  return (
    <Stack
      direction="row"
      alignItems="center"
      columnGap={1.25}
      rowGap={1}
      flexWrap="wrap"
      sx={{
        width: "100%",
        // It replaces the toolbar row, so it looks like it: the toolbar's edges
        // (the count starts on the search's, Run ends on Add's), its 32px
        // controls, and no band of its own. Narrow, the actions wrap under the
        // count, right-aligned, as the toolbar's controls wrap under the search.
        pb: 1.5,
      }}
    >
      {/* CONTEXT — count + clear-all in one pill. The × is the primary
          clear-all, on the same object as the count. */}
      <Stack
        direction="row" alignItems="center" spacing={0.75}
        sx={{
          flexShrink: 0,
          height: 32, pl: 0.75, pr: 0.5, borderRadius: "6px",
          border: "1px solid", borderColor: "divider",
          bgcolor: "background.paper",
        }}
      >
        {isAll ? (
          // Whole-match scope stated as one phrase so the escalation reads as
          // done (and the paging contract can assert it as a unit).
          <Typography sx={{
            typography: "m3", fontWeight: "fontWeightMedium",
            color: "text.primary", whiteSpace: "nowrap", px: 0.5,
          }}>
            All {count.toLocaleString()} matching {count === 1 ? "scenario" : "scenarios"} selected
          </Typography>
        ) : (
          <>
            <Box sx={{
              minWidth: 22, height: 22, px: 0.5, borderRadius: "4px",
              display: "flex", alignItems: "center", justifyContent: "center",
              bgcolor: "action.selected", color: "text.primary",
              typography: "s3", fontWeight: "fontWeightMedium", fontVariantNumeric: "tabular-nums",
            }}>
              {count}
            </Box>
            <Typography sx={{
              typography: "m3", fontWeight: "fontWeightMedium",
              color: "text.primary", whiteSpace: "nowrap", pl: 0.25,
            }}>
              {count === 1 ? "scenario selected" : "scenarios selected"}
            </Typography>
          </>
        )}
        <CustomTooltip show arrow size="small" title="Clear selection">
          <Box
            component="button"
            type="button"
            onClick={onClear}
            aria-label="Clear selection"
            sx={{
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              width: 22, height: 22, borderRadius: "4px",
              bgcolor: "transparent", border: "none", cursor: "pointer",
              color: "text.subtitle",
              transition: "background-color 120ms, color 120ms",
              "&:hover": {
                bgcolor: "action.hover",
                color: "text.primary",
              },
            }}
          >
            <Iconify icon="solar:close-linear" width={14} />
          </Box>
        </CustomTooltip>
      </Stack>

      {/* ESCALATION — bridge to the whole match while the page is fully checked
          and there's more beyond it. Disappears once taken (mode → all). */}
      {canEscalate && (
        <Button
          size="small"
          variant="text"
          color="inherit"
          onClick={matching.onSelectAll}
          sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
        >
          Select all {matching.total.toLocaleString()} matching
        </Button>
      )}

      {/* The actions move as one: on a narrow bar they wrap together, right-aligned. */}
      <Stack direction="row" alignItems="center" spacing={1.25} sx={{ ml: "auto", flexShrink: 0 }}>
        {/* DESTRUCTIVE — Delete */}
        <CustomTooltip show arrow size="small" title="Delete selected scenarios">
          <Button
            size="small"
            onClick={onDelete}
            variant="outlined"
            color="inherit"
            startIcon={<Iconify icon="solar:trash-bin-trash-linear" width={14} />}
            sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
          >
            Delete
          </Button>
        </CustomTooltip>

        {/* TRIALS — reliability dial */}
        {onTrialsChange && (
          <TrialsPicker trials={k} onChange={onTrialsChange} scenarioCount={count} />
        )}

        {/* PRIMARY — Run N selected (× k). Unavailable: shown disabled, the
            reason on hover (a disabled button fires no events, so its
            tooltip sits on a wrapper). */}
        {onRun ? (
          <CustomTooltip
            show
            arrow
            size="small"
            title={k > 1
              ? `${count} ${count === 1 ? "scenario" : "scenarios"} × ${k} repeats = ${totalRuns} runs · ${timeHint}`
              : `${count} ${count === 1 ? "scenario" : "scenarios"} × 1 repeat · ${timeHint}`}
          >
            <RunButton label={runLabel} onClick={() => onRun(k)} />
          </CustomTooltip>
        ) : runBlockedReason ? (
          <CustomTooltip show arrow size="small" title={runBlockedReason}>
            <Box component="span" sx={{ display: "inline-flex", flexShrink: 0 }}>
              <RunButton label={runLabel} disabled />
            </Box>
          </CustomTooltip>
        ) : null}

      </Stack>

    </Stack>
  );
}
// The bar's primary action: Run simulation (N).
const RunButton = forwardRef(function RunButton({ label, onClick, disabled = false, ...other }, ref) {
  return (
    <Button
      ref={ref}
      {...other}
      size="small"
      variant="contained"
      disabled={disabled}
      onClick={onClick}
      startIcon={<Iconify icon="solar:play-bold" width={14} />}
      sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
    >
      {label}
    </Button>
  );
});
RunButton.propTypes = {
  label: PropTypes.string.isRequired,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
};

SelectionBar.propTypes = {
  count: PropTypes.number.isRequired,
  trials: PropTypes.number,
  onTrialsChange: PropTypes.func,
  onRun: PropTypes.func,
  // Why Run is unavailable; shows the button disabled with this on hover.
  runBlockedReason: PropTypes.string,
  onDelete: PropTypes.func.isRequired,
  onClear: PropTypes.func.isRequired,
  // Select-all-matching context from the pagination predicate. Absent → a plain
  // page selection with no escalation. { mode, total, pageCount, onSelectAll }.
  matching: PropTypes.shape({
    mode: PropTypes.oneOf(["include", "all"]),
    total: PropTypes.number,
    pageCount: PropTypes.number,
    onSelectAll: PropTypes.func,
  }),
};
