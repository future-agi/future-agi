// W1-9 B5 (CP-08) · Paths compare, per step: visits, pass share through the
// step, mean time, tokens and cost, and failing checks, baseline → candidate
// with the change, sorted by the biggest change. A row filters the paths to
// those through the step; `Open tries` opens its tries side by side.
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { fmtCost, fmtMs, fmtPct } from "src/sections/common/format/fmt";
import { compareSteps, stepCompareText } from "./stepCompare";

const COLS =
  "minmax(140px,1.4fr) repeat(5, minmax(84px,1fr)) minmax(150px,1.6fr) 84px";
const int = (v) => Math.round(v).toLocaleString("en-US");

function Pair({ a, b, f, delta, good }) {
  if (a == null && b == null)
    return (
      <Typography typography="s3" color="text.disabled">
        —
      </Typography>
    );
  const tone =
    delta == null || delta === 0 || good == null
      ? "text.secondary"
      : delta > 0 === good
        ? "green.600"
        : "red.600";
  return (
    <Stack sx={{ minWidth: 0 }}>
      <Typography
        typography="s3"
        noWrap
        sx={{ fontVariantNumeric: "tabular-nums" }}
      >
        {`${a == null ? "—" : f(a)} → ${b == null ? "—" : f(b)}`}
      </Typography>
      {delta != null && delta !== 0 ? (
        <Typography
          typography="s3"
          sx={{ color: tone, fontVariantNumeric: "tabular-nums" }}
        >
          {delta > 0 ? "+" : "−"}
          {f(Math.abs(delta))}
        </Typography>
      ) : null}
    </Stack>
  );
}
Pair.propTypes = {
  a: PropTypes.number,
  b: PropTypes.number,
  f: PropTypes.func.isRequired,
  delta: PropTypes.number,
  good: PropTypes.bool,
};

const pctOf = (v) => fmtPct(v, { dp: 0 });
const ptsOf = (v) => `${(Math.round(v * 10) / 10).toFixed(1)} pts`;

export default function StepCompareTable({
  baseAgg,
  candAgg,
  labels,
  selected,
  onSelect,
  onOpenTries,
}) {
  const rows = useMemo(
    () => compareSteps(baseAgg, candAgg),
    [baseAgg, candAgg],
  );
  if (!rows.length) return null;
  const head = (t) => (
    <Typography
      typography="s3"
      color="text.secondary"
      fontWeight="fontWeightSemiBold"
      noWrap
    >
      {t}
    </Typography>
  );
  return (
    <Box
      data-testid="paths-step-compare"
      sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
    >
      <Stack
        direction="row"
        alignItems="baseline"
        gap={1}
        sx={{ px: 1.25, py: 0.75 }}
      >
        <Typography typography="s2" fontWeight="fontWeightSemiBold">
          Steps compared
        </Typography>
        <Typography typography="s3" color="text.secondary">
          {`${labels.base} → ${labels.cand} · per step, biggest change first`}
        </Typography>
      </Stack>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: COLS,
          gap: 1,
          px: 1.25,
          py: 0.5,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        {head("Step")}
        {head("Visits")}
        {head("Pass through step")}
        {head("Mean time")}
        {head("Mean tokens")}
        {head("Mean cost")}
        {head("Failing checks")}
        <span />
      </Box>
      {rows.map((r) => (
        <Box
          key={r.id}
          role="button"
          tabIndex={0}
          title={stepCompareText(r)}
          data-testid="step-compare-row"
          data-step={r.id}
          aria-pressed={selected === r.id}
          onClick={() => onSelect?.(r.id)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onSelect?.(r.id);
          }}
          sx={{
            display: "grid",
            gridTemplateColumns: COLS,
            gap: 1,
            px: 1.25,
            py: 0.6,
            alignItems: "center",
            cursor: "pointer",
            borderTop: "1px solid",
            borderColor: "divider",
            bgcolor: selected === r.id ? "action.selected" : undefined,
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          <Stack sx={{ minWidth: 0 }}>
            <Typography typography="s2" noWrap fontWeight="fontWeightMedium">
              {r.end ? "End (the try's end state)" : r.name}
            </Typography>
            {r.only ? (
              <Typography typography="s3" color="text.secondary" noWrap>
                {r.only === "cand"
                  ? `Only in ${labels.cand}`
                  : `Only in ${labels.base}`}
              </Typography>
            ) : null}
          </Stack>
          <Pair
            a={r.end ? null : r.base?.visits}
            b={r.end ? null : r.cand?.visits}
            f={int}
            delta={r.delta.visits}
          />
          <Stack sx={{ minWidth: 0 }}>
            <Typography
              typography="s3"
              noWrap
              sx={{ fontVariantNumeric: "tabular-nums" }}
            >
              {r.end
                ? "—"
                : `${r.base?.passShare == null ? "—" : pctOf(r.base.passShare)} → ${r.cand?.passShare == null ? "—" : pctOf(r.cand.passShare)}`}
            </Typography>
            {r.delta.passPts ? (
              <Typography
                typography="s3"
                sx={{ color: r.delta.passPts > 0 ? "green.600" : "red.600" }}
              >
                {r.delta.passPts > 0 ? "+" : "−"}
                {ptsOf(Math.abs(r.delta.passPts))}
              </Typography>
            ) : null}
          </Stack>
          <Pair
            a={r.base?.meanMs}
            b={r.cand?.meanMs}
            f={fmtMs}
            delta={r.delta.ms}
            good={false}
          />
          <Pair
            a={r.base?.meanTokens}
            b={r.cand?.meanTokens}
            f={int}
            delta={r.delta.tokens}
            good={false}
          />
          <Pair
            a={r.base?.meanCost}
            b={r.cand?.meanCost}
            f={fmtCost}
            delta={r.delta.cost}
            good={false}
          />
          <Stack sx={{ minWidth: 0 }}>
            {r.checks.length ? (
              r.checks.map((c) => (
                <Typography
                  key={c.name}
                  typography="s3"
                  noWrap
                  data-testid="step-compare-check"
                  sx={{
                    color:
                      c.delta < 0
                        ? "green.600"
                        : c.delta > 0
                          ? "red.600"
                          : "text.secondary",
                  }}
                >
                  {`✕ ${c.name} ${c.base} → ${c.cand}`}
                </Typography>
              ))
            ) : (
              <Typography typography="s3" color="text.disabled">
                none
              </Typography>
            )}
          </Stack>
          <Button
            size="small"
            color="inherit"
            onClick={(e) => {
              e.stopPropagation();
              onOpenTries?.(r.id);
            }}
            sx={{ typography: "s3", px: 0.5, minWidth: 0 }}
            data-testid="step-compare-open"
          >
            Open tries
          </Button>
        </Box>
      ))}
    </Box>
  );
}

StepCompareTable.propTypes = {
  baseAgg: PropTypes.object,
  candAgg: PropTypes.object,
  labels: PropTypes.shape({ base: PropTypes.string, cand: PropTypes.string }),
  selected: PropTypes.string,
  onSelect: PropTypes.func,
  onOpenTries: PropTypes.func,
};
