import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
// HO results R4: a reviewer's corrected output on this result.
import CorrectedOutputChip from "src/sections/develop-detail/Review/CorrectedOutputChip";
import {
  Box,
  Button,
  IconButton,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import { useSearchParams } from "react-router-dom";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "src/components/snackbar";
import { copyToClipboard } from "src/utils/utils";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import TraceChip from "src/sections/common/Trace/TraceChip";
import { cellTraceChipProps } from "src/sections/common/Trace/traceChipModel";
import { runKindOf } from "src/sections/common/Runs/runKind";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import CustomTooltip from "src/components/tooltip";
import { trialOutcome } from "../resultsSimple";
import MediaValue from "src/sections/common/Media/MediaValue";
import { conversationOf } from "src/sections/common/Media/mediaRef";
import GridValueSummary from "src/sections/common/Values/GridValueSummary";
import { mediaCellKind } from "src/sections/common/Media/mediaModel";
import { compactCellSx, isCompactCell } from "./ExampleCells";
import {
  ClampedText,
  MutedText,
  cellBoxSx,
  cellPropTypes,
  linesFor,
  notInRunLabel,
  carriedLabel,
  queuedLabel,
  primaryText,
} from "./cellCommon";
import { isRawFormat, rawValueText, trialDotsTip } from "./rawValue";

/** `‹ 1/3 ›` trial stepper (Output cells and drawer cards). */
export const TrialStepper = ({ trial, trials, onChange, size = "small" }) => (
  <Stack
    direction="row"
    alignItems="center"
    onClick={(e) => e.stopPropagation()}
    sx={{
      border: "1px solid",
      borderColor: "divider",
      borderRadius: 0.5,
      bgcolor: "background.paper",
      height: size === "small" ? 20 : 24,
    }}
  >
    <IconButton
      size="small"
      aria-label="Previous trial"
      disabled={trial <= 1}
      onClick={() => onChange(trial - 1)}
      sx={{ p: 0, borderRadius: 0 }}
    >
      <Iconify icon="mdi:chevron-left" width={16} />
    </IconButton>
    <Typography
      typography="s3"
      color="text.secondary"
      sx={{ px: 0.25, fontVariantNumeric: "tabular-nums" }}
    >
      {trial}/{trials}
    </Typography>
    <IconButton
      size="small"
      aria-label="Next trial"
      disabled={trial >= trials}
      onClick={() => onChange(trial + 1)}
      sx={{ p: 0, borderRadius: 0 }}
    >
      <Iconify icon="mdi:chevron-right" width={16} />
    </IconButton>
  </Stack>
);

TrialStepper.propTypes = {
  trial: PropTypes.number.isRequired,
  trials: PropTypes.number.isRequired,
  onChange: PropTypes.func.isRequired,
  size: PropTypes.oneOf(["small", "medium"]),
};

/** Diff source for the output of `runId` under the diff mode. */
export function diffSource(row, runId, { diff, baselineId }) {
  if (diff === "expected") {
    const name = row?.expected?.primary;
    const value = name ? row?.expected?.value?.[name] : row?.expected?.value;
    const text = primaryText(value);
    return text || null;
  }
  if (diff === "baseline" && runId !== baselineId) {
    const base = row?.results?.[baselineId];
    return base?.status === "completed" || base?.status === "running"
      ? base.output ?? null
      : null;
  }
  return null;
}

/** Other runs that ran this row (for `Compare with…`). */
export function otherRunsOnRow(row, runId, runsById = {}) {
  return Object.entries(row?.results ?? {})
    .filter(
      ([id, r]) =>
        id !== runId &&
        r &&
        !["missing", "queued"].includes(r.status) &&
        runsById[id],
    )
    .map(([id]) => runsById[id]);
}

/**
 * The always-visible trace of one result and trial (R2-SPEC §2.1). Click
 * (or Enter) opens the result drawer on `dtab=trajectory` for this run,
 * row and trial; `⋯` has Open trajectory · Open full trace · Compare with…
 * · Copy trace id.
 */
export const CellTraceChip = ({ row, runId, result, trial, ctx }) => {
  const [searchParams, setSearchParams] = useSearchParams();
  const [compareAnchor, setCompareAnchor] = useState(null);
  const [fullTrace, setFullTrace] = useState(false);
  const anchorRef = useRef(null);
  const run = ctx.runsById?.[runId];
  const chip = cellTraceChipProps(result, trial, {
    sourceKind: run ? runKindOf(run) : undefined,
  });
  const traceId = result?.trace_ids?.[trial - 1] ?? null;
  const others = otherRunsOnRow(row, runId, ctx.runsById);

  const open = (extra = {}) => {
    if (!row?.example_id) return;
    const next = new URLSearchParams(searchParams);
    next.set("row", row.example_id);
    next.set("run", runId);
    next.set("dtab", extra.dtab ?? "trajectory");
    if (trial > 1) next.set("trial", String(trial));
    else next.delete("trial");
    if (extra.vs) next.set("vs", extra.vs);
    else next.delete("vs");
    // Opening pushes (Back closes); switching inside an open drawer replaces.
    setSearchParams(next, { replace: Boolean(searchParams.get("row")) });
  };

  const menu = [
    ...(ctx.onSideBySide
      ? [
          {
            label: "Open side by side",
            icon: "mdi:compare-horizontal",
            onClick: () => ctx.onSideBySide(row, runId, trial),
          },
        ]
      : []),
    {
      label: "Open trajectory",
      icon: "mdi:transit-connection-variant",
      onClick: () => open(),
    },
    {
      label: "Open full trace ↗",
      icon: "mdi:open-in-new",
      disabled: !traceId,
      onClick: () => setFullTrace(true),
    },
    {
      label: "Compare with… ▸",
      icon: "mdi:compare-horizontal",
      disabled: !others.length,
      onClick: () => setCompareAnchor(anchorRef.current),
    },
    {
      label: "Copy trace id",
      icon: "mdi:content-copy",
      disabled: !traceId,
      onClick: () => {
        copyToClipboard(traceId);
        enqueueSnackbar("Trace id copied", { variant: "success" });
      },
    },
  ];

  return (
    <Box
      ref={anchorRef}
      data-trace-chip=""
      onClick={(e) => e.stopPropagation()}
      sx={{ display: "inline-flex", minWidth: 0, maxWidth: "100%" }}
    >
      <TraceChip
        {...chip}
        compact
        onOpen={() =>
          open({ dtab: chip.state === "infra" ? "artifacts" : "trajectory" })
        }
        menu={menu}
      />
      <Menu
        open={Boolean(compareAnchor)}
        anchorEl={compareAnchor}
        onClose={() => setCompareAnchor(null)}
        onClick={(e) => e.stopPropagation()}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{ paper: { sx: { minWidth: 220, mt: 0.5 } } }}
        MenuListProps={{ dense: true }}
      >
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ px: 1.5, pt: 0.5, pb: 0.25, display: "block" }}
        >
          Compare this trace with
        </Typography>
        {others.map((r) => (
          <MenuItem
            key={r.run_id}
            onClick={() => {
              setCompareAnchor(null);
              open({ vs: r.run_id });
            }}
          >
            <ListItemText
              primaryTypographyProps={{ typography: "s1", noWrap: true }}
            >
              {[r.label, r.name].filter(Boolean).join(" ")}
            </ListItemText>
          </MenuItem>
        ))}
      </Menu>
      {fullTrace && traceId ? (
        <TraceDetailDrawerV2
          traceId={traceId}
          open={fullTrace}
          onClose={() => setFullTrace(false)}
        />
      ) : null}
    </Box>
  );
};

CellTraceChip.propTypes = {
  row: PropTypes.object,
  runId: PropTypes.string.isRequired,
  result: PropTypes.object,
  trial: PropTypes.number.isRequired,
  ctx: PropTypes.object.isRequired,
};

const DOT_COLOR = {
  pass: "green.600",
  fail: "red.600",
  flaky: "amber.500",
  none: "text.disabled",
};

/**
 * `●●○ 2/3 trials` (RC#5, R3 MUST 7): one dot per trial (filled pass,
 * hollow fail) + the count; one trial reads `Pass` / `Fail`.
 */
export const TrialDots = ({ outcome, metricName }) => {
  if (!outcome || outcome.tone === "none") return null;
  const tip =
    outcome.n === 1
      ? `${outcome.label} on ${metricName ?? "the pass/fail eval"}`
      : trialDotsTip(outcome, metricName);
  return (
    <CustomTooltip show size="small" placement="top" arrow title={tip}>
      <Stack
        direction="row"
        alignItems="center"
        gap={0.5}
        sx={{ flexShrink: 0 }}
        aria-label={tip}
      >
        <Stack direction="row" gap={0.25} aria-hidden>
          {outcome.dots.map((ok, i) => (
            <Box
              // eslint-disable-next-line react/no-array-index-key
              key={i}
              sx={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                border: "1.5px solid",
                borderColor: ok ? DOT_COLOR.pass : DOT_COLOR.fail,
                bgcolor: ok ? DOT_COLOR.pass : "transparent",
              }}
            />
          ))}
        </Stack>
        <Typography
          typography="s3"
          color={
            outcome.tone === "pass"
              ? "green.700"
              : outcome.tone === "fail"
                ? "red.700"
                : "text.secondary"
          }
          sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}
        >
          {outcome.label}
        </Typography>
      </Stack>
    </CustomTooltip>
  );
};

TrialDots.propTypes = {
  outcome: PropTypes.object,
  metricName: PropTypes.string,
};

/**
 * One run's output on one row. Diff modes through TextDiff, trials with a
 * stepper, error cells with `Re-run cell`, and the result's trace as an
 * always-visible TraceChip (R2-SPEC §2.1).
 */
const OutputCell = ({ data, context, runId }) => {
  const result = data?.results?.[runId];
  const [trial, setTrial] = useState(1);
  const ctx = context ?? {};
  const run = ctx.runsById?.[runId];

  if (!result || result.status === "missing")
    return (
      <Box sx={cellBoxSx}>
        <MutedText icon="mdi:minus-circle-outline">
          {notInRunLabel(run)}
        </MutedText>
      </Box>
    );
  if (result.status === "queued")
    return (
      <Box sx={cellBoxSx}>
        <MutedText icon="mdi:clock-outline">{queuedLabel(run)}</MutedText>
      </Box>
    );
  if (result.status === "error")
    return (
      <Stack
        sx={{
          ...(isCompactCell(ctx) ? compactCellSx : cellBoxSx),
          bgcolor: "var(--score-red-light)",
        }}
      >
        <Typography
          typography="s2_1"
          color="red.700"
          fontWeight={500}
          sx={{
            flex: isCompactCell(ctx) ? 1 : undefined,
            minWidth: 0,
            display: "-webkit-box",
            WebkitLineClamp: isCompactCell(ctx) ? 1 : 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {`Error · ${result.error ?? "Run failed on this row"}`}
        </Typography>
        <Stack direction="row" alignItems="center" gap={1}>
          <CellTraceChip
            row={data}
            runId={runId}
            result={result}
            trial={1}
            ctx={ctx}
          />
          {!ctx.readOnly && ctx.onRerunCell && (
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              startIcon={<Iconify icon="mdi:refresh" width={14} />}
              onClick={(e) => {
                e.stopPropagation();
                ctx.onRerunCell(data, runId);
              }}
              sx={{
                alignSelf: "flex-start",
                height: 24,
                typography: "s2",
                bgcolor: "background.paper",
              }}
            >
              Re-run cell
            </Button>
          )}
        </Stack>
      </Stack>
    );

  const trials = result.trial_outputs?.length ?? 1;
  const current = Math.min(trial, trials);
  const outcome = trialOutcome(result, ctx.primaryMetricKey);
  const stored =
    trials > 1 ? result.trial_outputs[current - 1] : result.output ?? "";
  // L7c g6: Raw JSON values shows the stored output as JSON (no diff, no
  // media summary).
  const raw = isRawFormat(ctx);
  const text = raw ? rawValueText(stored) : stored;
  const from =
    !raw && ctx.diff && ctx.diff !== "off"
      ? diffSource(data, runId, ctx)
      : null;
  const metricName = ctx.defsByKey?.[ctx.primaryMetricKey]?.name;
  const carried = carriedLabel(result);
  const chip = (
    <CellTraceChip
      row={data}
      runId={runId}
      result={result}
      trial={current}
      ctx={ctx}
    />
  );
  const stepper =
    trials > 1 ? (
      <TrialStepper trial={current} trials={trials} onChange={setTrial} />
    ) : null;
  // R4 (lane M): media, call and conversation outputs render through
  // MediaValue / the chat chip (a word diff of two call JSONs is noise);
  // text outputs below are unchanged.
  const richKind =
    !raw && typeof text === "string" && /^\s*[[{]/.test(text)
      ? // calls first (a call has turns too), then chats, then media
        mediaCellKind(text, undefined) ?? (conversationOf(text) ? "chat" : null)
      : null;
  if (richKind) {
    const rich =
      richKind === "chat" ? (
        <GridValueSummary value={text} dataType="json" bare />
      ) : (
        <MediaValue value={text} size="cell" dense={isCompactCell(ctx)} />
      );
    return (
      <Stack
        sx={
          isCompactCell(ctx)
            ? compactCellSx
            : { ...cellBoxSx, justifyContent: "space-between" }
        }
        data-output-media={richKind}
      >
        <Box
          sx={{
            flex: isCompactCell(ctx) ? 1 : undefined,
            minWidth: 0,
            display: "flex",
            alignItems: "center",
            minHeight: 24,
          }}
        >
          {rich}
        </Box>
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          sx={{ minWidth: 0, flexShrink: 0 }}
        >
          {isCompactCell(ctx) ? null : chip}
          {isCompactCell(ctx) ? null : <Box sx={{ flex: 1 }} />}
          <TrialDots outcome={outcome} metricName={metricName} />
          {isCompactCell(ctx) ? null : stepper}
        </Stack>
      </Stack>
    );
  }
  // A3-04 Compact: one line, trial pips always; the steps chip and the
  // trial pager show on row hover / keyboard focus (`fa-compact-hover`).
  if (isCompactCell(ctx))
    return (
      <Stack sx={compactCellSx}>
        <CorrectedOutputChip result={result} compact />
        <Box
          sx={{
            flex: 1,
            minWidth: 0,
            maxHeight: 20,
            overflow: "hidden",
            "& p": { typography: "s2_1" },
          }}
        >
          {from != null && text ? (
            <TextDiff from={from} to={text} />
          ) : text ? (
            <ClampedText text={text} lines={1} highlight={ctx.search} />
          ) : (
            <MutedText>Empty output</MutedText>
          )}
        </Box>
        {/* Over the end of the line (the text keeps its start). */}
        <Stack
          className="fa-compact-hover"
          direction="row"
          alignItems="center"
          gap={0.75}
          sx={{
            position: "absolute",
            right: 8,
            top: "50%",
            transform: "translateY(-50%)",
            pl: 1,
            bgcolor: "background.paper",
            boxShadow: (t) => `-12px 0 10px -2px ${t.palette.background.paper}`,
            zIndex: 1,
          }}
        >
          {chip}
          {stepper}
        </Stack>
        <Box sx={{ flexShrink: 0 }}>
          <TrialDots outcome={outcome} metricName={metricName} />
        </Box>
      </Stack>
    );
  // Comfortable and taller: the trace chip row is always there (20 px
  // chip), so the text clamps the same at every density.
  const reserved = 26;
  const lines = linesFor(ctx.rowHeight, reserved);

  return (
    <Stack sx={cellBoxSx}>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: "hidden",
          maxHeight: lines * 20,
          "& p": { typography: "s2_1" },
        }}
      >
        {from != null && text ? (
          <TextDiff from={from} to={text} />
        ) : text ? (
          <ClampedText text={text} lines={lines} highlight={ctx.search} />
        ) : (
          <MutedText>Empty output</MutedText>
        )}
      </Box>
      <Stack direction="row" alignItems="center" gap={1} sx={{ minWidth: 0 }}>
        {chip}
        {carried ? (
          <Typography
            typography="s3"
            color="text.secondary"
            noWrap
            data-testid="cell-carried-from"
            sx={{
              px: 0.5,
              borderRadius: 0.5,
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            {carried}
          </Typography>
        ) : null}
        <CorrectedOutputChip result={result} />
        <Box sx={{ flex: 1 }} />
        <TrialDots outcome={outcome} metricName={metricName} />
        {result.status === "running" && (
          <Typography typography="s3" color="text.disabled" noWrap>
            Scoring…
          </Typography>
        )}
        {stepper}
      </Stack>
    </Stack>
  );
};

OutputCell.propTypes = cellPropTypes;

export default OutputCell;
