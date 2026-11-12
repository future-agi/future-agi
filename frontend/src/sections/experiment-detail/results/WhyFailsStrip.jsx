import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Button, Stack, Typography } from "@mui/material";
import { useLocation, useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import {
  iterateHref,
  taskCompareHref,
} from "src/sections/common/Analysis/analysisLinks";
import { useRootCauses } from "../matrix/useMatrixData";
import { useRunConfigs } from "src/api/develop/r2";
import { canIterate } from "../iterate/iterateModel";
import { causeMethodLabel } from "./causeMethod";

const lowerFirst = (s) => (s ? s[0].toLowerCase() + s.slice(1) : s);

/**
 * `Why #2B fails: order id loses its "A-" prefix in classify_intent (step
 * 1) · 6 rows`, or null. Pure; `label(id)` names a run.
 */
export function whyFailsText(cause, label, { winning = null, nameOf } = {}) {
  if (!cause?.title) return null;
  // iterate r3 G3 (P2): never `Why #2G fails` on the run that wins the
  // verdict — `Why #2G got worse on T13 (1 of 2): …`, or nothing.
  const onWinner =
    winning && winning.runId === cause.run_id && winning.tone === "better";
  const covered = onWinner
    ? (cause.rows ?? [])
        .map((r) => r.row_id)
        .filter((id) => (winning.worseRows ?? []).includes(id))
    : [];
  if (onWinner && !covered.length) return null;
  // A finding without a named eval says nothing yet: no strip.
  const title = /\bunknown\b/i.test(cause.title)
    ? cause.failed_eval_name
      ? cause.title.replace(/\bunknown\b/i, cause.failed_eval_name)
      : null
    : cause.title;
  if (!title) return null;
  const node = cause.first_divergence?.node ?? cause.node ?? null;
  const step = cause.first_divergence?.step_n ?? null;
  const where = node
    ? ` in ${node}${Number.isFinite(step) ? ` (step ${step})` : ""}`
    : "";
  const n = cause.rows?.length ?? 0;
  if (onWinner) {
    const names = covered.map((id) => nameOf?.(id) ?? null).filter(Boolean);
    const on = names.length
      ? names.slice(0, 2).join(", ")
      : `${covered.length} rows`;
    return `Why ${label(cause.run_id)} got worse on ${on} (${covered.length} of ${winning.worseRows.length}): ${lowerFirst(title)}${where}`;
  }
  return `Why ${label(cause.run_id)} fails: ${lowerFirst(title)}${where} · ${n} ${n === 1 ? "row" : "rows"}`;
}

/**
 * The rule-based finding for the run in focus and its two next steps
 * (`Compare a trace`, `Test a fix on these 6`), or null. Shared by the
 * strip and IT-R4 G3's `See why ▾` split button (one finding source).
 */
export function useWhyFails({
  datasetId,
  runIds,
  baselineId,
  focusRunId,
  candidateId,
  labelOf,
  winning,
  nameOf,
}) {
  const { pathname, search } = useLocation();
  const configs = useRunConfigs(datasetId, { enabled: Boolean(datasetId) });
  const q = useRootCauses(
    { datasetId, runIds, baselineId },
    { enabled: (runIds ?? []).length > 1 },
  );
  const cause = useMemo(() => {
    const causes = (q.data?.causes ?? []).filter((c) =>
      (runIds ?? []).includes(c.run_id),
    );
    // Only about the runs the page is about (the open row's run, the
    // header's candidate, the baseline) — never a side run.
    const order = [focusRunId, candidateId, baselineId].filter(Boolean);
    for (const id of order) {
      const c = causes.find((x) => x.run_id === id && x.rows?.length);
      if (c) return c;
    }
    return null;
  }, [q.data, runIds, focusRunId, candidateId, baselineId]);
  const text = whyFailsText(cause, labelOf, { winning, nameOf });
  if (!cause || !text) return null;
  const rows = cause.rows.map((r) => r.row_id).filter(Boolean);
  const passer =
    (runIds ?? []).find(
      (id) => id !== cause.run_id && id === cause.baseline_run_id,
    ) ?? (runIds ?? []).find((id) => id !== cause.run_id);
  const traceHref = rows[0]
    ? taskCompareHref({
        datasetId,
        runIds: [cause.run_id, passer].filter(Boolean),
        baselineId: cause.run_id,
        rowId: rows[0],
        from: `${pathname}${search}`,
      })
    : null;
  // dataset integration FE-0: no Test a fix door for a run it does not apply to.
  const causeRun = (configs.data?.results ?? []).find(
    (r) => r.run_id === cause.run_id,
  );
  const fixHref =
    causeRun && !canIterate(causeRun)
      ? null
      : `${pathname}${iterateHref(search, {
          runId: cause.run_id,
          scope: "rows",
          rows,
          cause: cause.id,
        })}`;
  const fixLabel =
    rows.length === 1
      ? "Test a fix on this row"
      : `Test a fix on these ${rows.length}`;
  return { cause, text, rows, traceHref, fixHref, fixLabel };
}

/**
 * iterate r2 G3 (P12): the Falcon finding for the run in focus, on the
 * Data tab (it was only on Summary) — one line and its two next steps.
 */
export default function WhyFailsStrip(props) {
  const navigate = useNavigate();
  const why = useWhyFails(props);
  if (!why) return null;
  const { text, traceHref, fixHref, fixLabel } = why;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{
        minWidth: 0,
        minHeight: 24,
        px: 1,
        borderRadius: 0.75,
        bgcolor: (t) => `${t.palette.warning.main}14`,
      }}
      data-testid="results-why-fails"
    >
      <Iconify
        icon="mdi:format-list-checks"
        width={16}
        sx={{ color: "amber.600", flexShrink: 0 }}
      />
      <Typography
        typography="s2"
        fontWeight={600}
        noWrap
        title={`${text}. ${causeMethodLabel(why.cause)}`}
        sx={{ minWidth: 0, flex: "0 1 auto" }}
      >
        {text}
      </Typography>
      <Typography
        typography="s3"
        color="text.secondary"
        noWrap
        data-testid="results-why-method"
        sx={{ minWidth: 0, flex: "0 2 auto" }}
      >
        {causeMethodLabel(why.cause)}
      </Typography>
      {traceHref ? (
        <Button
          size="small"
          variant="text"
          onClick={() => navigate(traceHref)}
          data-testid="results-why-compare"
          sx={{
            height: 24,
            px: 1,
            typography: "s2",
            flexShrink: 0,
            whiteSpace: "nowrap",
          }}
        >
          Compare a trace
        </Button>
      ) : null}
      {fixHref ? (
        <Button
          size="small"
          variant="outlined"
          onClick={() => navigate(fixHref)}
          startIcon={<Iconify icon="mdi:source-branch" width={14} />}
          data-testid="results-why-fix"
          sx={{
            height: 24,
            px: 1,
            typography: "s2",
            flexShrink: 0,
            whiteSpace: "nowrap",
          }}
        >
          {fixLabel}
        </Button>
      ) : null}
    </Stack>
  );
}

WhyFailsStrip.propTypes = {
  datasetId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  baselineId: PropTypes.string,
  focusRunId: PropTypes.string,
  candidateId: PropTypes.string,
  labelOf: PropTypes.func.isRequired,
  /** P2: the verdict's lead `{runId, tone, worseRows}`. */
  winning: PropTypes.object,
  nameOf: PropTypes.func,
};
