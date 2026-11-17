// Result drawer · Trajectory: the focused run's try in
// RunTrajectory (Steps · Graph · Timeline on Observe's components) with
// the provenance line above, `Compare with ▾` (`vs=`) for compare mode and a
// try switcher that opens on the first FAILED try (`Try 2 of 3 · failed`).
// Trace and Trajectory are one tab: `dtab=trace` lands here on Timeline.
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useSearchParams } from "react-router-dom";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useRunConfigs } from "src/api/develop/r2";
import { runLabel, runShortName } from "src/sections/common/Runs/runLabel";
import RunProvenance from "src/sections/common/Runs/RunProvenance";
import { ItemsMenu } from "src/sections/common/ux/oneview";
import { readR2Params } from "src/sections/common/Analysis/analysisLinks";
import RunTrajectory from "src/sections/common/Trajectory/RunTrajectory";
import {
  rowLabel,
  useDatasetKind,
} from "src/sections/common/Datasets/datasetKind";
import useRunMarkers from "src/sections/common/Trajectory/useRunMarkers";
import { patchSearch } from "../useResultsUrlState";
import { RESULT_DRAWER_TAB_PROPTYPES } from "./ResultDrawerTabs.propTypes";
import { parseVs, formatVs } from "./PairPicker";
import { traceIdFor, trialCount } from "./alignSteps";
import EvalStepsList from "./EvalStepsList";

const OUTCOME_WORD = {
  pass: "passed",
  fail: "failed",
  error: "errored",
  infra: "didn't run",
  carried: "carried over",
};

const colorOf = (c) => (typeof c === "string" ? c : c?.solid ?? null);
const nameOf = (run) =>
  run ? [runLabel(run), runShortName(run)].filter(Boolean).join(" ") : "";

/** `‹ Try 2 of 3 · failed ›` */
function TrySwitcher({ trial, trials, outcome, onChange }) {
  if (trials < 2) return null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.25}
      data-testid="try-switcher"
    >
      <IconButton
        size="small"
        aria-label="Previous try"
        disabled={trial <= 1}
        onClick={() => onChange(trial - 1)}
      >
        <Iconify icon="mdi:chevron-left" width={16} />
      </IconButton>
      <Typography
        typography="s2"
        sx={{
          whiteSpace: "nowrap",
          fontVariantNumeric: "tabular-nums",
          color:
            outcome === "fail" || outcome === "error"
              ? "error.main"
              : "text.primary",
        }}
      >
        {`Try ${trial} of ${trials}${OUTCOME_WORD[outcome] ? ` · ${OUTCOME_WORD[outcome]}` : ""}`}
      </Typography>
      <IconButton
        size="small"
        aria-label="Next try"
        disabled={trial >= trials}
        onClick={() => onChange(trial + 1)}
      >
        <Iconify icon="mdi:chevron-right" width={16} />
      </IconButton>
    </Stack>
  );
}
TrySwitcher.propTypes = {
  trial: PropTypes.number,
  trials: PropTypes.number,
  outcome: PropTypes.string,
  onChange: PropTypes.func,
};

/** P10: `Compare with` when the view holds one run. */
export const NO_OTHER_RUN =
  "Only one run in this experiment — add a run to compare";

const TrajectoryTab = ({
  datasetId,
  row,
  runs,
  baselineId,
  focusedRunId,
  trial,
  colors,
  focusedRun: focusedRunProp,
}) => {
  const [sp, setSp] = useSearchParams();
  const patch = (updates) =>
    setSp(patchSearch(window.location.search, updates), { replace: true });

  // `dtab=trace` (R3 Trace tab) → Trajectory on Timeline, no history entry.
  useEffect(() => {
    if (sp.get("dtab") === "trace")
      patch({ dtab: "trajectory", tview: "timeline" });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const { data: cfg } = useRunConfigs(datasetId);
  const { kind: datasetKind } = useDatasetKind(datasetId);
  const byId = useMemo(() => {
    const out = {};
    (runs ?? []).forEach((r) => {
      out[r.run_id] = r;
    });
    (cfg?.results ?? []).forEach((r) => {
      out[r.run_id] = { ...(out[r.run_id] ?? {}), ...r };
    });
    return out;
  }, [runs, cfg]);
  const focused =
    (focusedRunProp?.config ? focusedRunProp : null) ??
    byId[focusedRunId] ??
    (runs ?? [])[0] ??
    null;
  const vs = parseVs(readR2Params(sp).vs);
  // iterate r1 G3 (P9): with a baseline in view the tab opens on both runs
  // side by side (the baseline as the second lane); `vs=none` = one lane.
  const defaultVs =
    baselineId && baselineId !== focused?.run_id && byId[baselineId]
      ? baselineId
      : null;
  const compareId = vs
    ? !vs.none && vs.runId !== focused?.run_id
      ? vs.runId
      : null
    : defaultVs;
  const compareRun = compareId ? byId[compareId] ?? null : null;

  const rm = useRunMarkers(
    {
      datasetId,
      rowId: row?.example_id,
      runIds: [focused?.run_id, compareId].filter(Boolean),
    },
    { enabled: Boolean(row?.example_id) },
  );
  const focusedInfo = rm.byRun[focused?.run_id];
  const result = row?.results?.[focused?.run_id] ?? null;
  const trials = Math.max(
    trialCount(result),
    focusedInfo?.outcomes.length ?? 0,
    1,
  );
  // The drawer opens on the first failed try unless the URL names one.
  const urlTrial = sp.get("trial");
  const focusTrial = Math.min(
    trials,
    Math.max(1, urlTrial ? trial || 1 : focusedInfo?.firstFailed ?? trial ?? 1),
  );
  const outcome = focusedInfo?.outcomes.find(
    (o) => o.trial === focusTrial,
  )?.outcome;
  const compareResult = compareId ? row?.results?.[compareId] ?? null : null;
  const compareInfo = rm.byRun[compareId];
  const compareTrial = compareId
    ? Math.min(
        vs?.trial ?? compareInfo?.firstFailed ?? focusTrial,
        Math.max(
          trialCount(compareResult),
          compareInfo?.outcomes.length ?? 0,
          1,
        ),
      )
    : null;

  const traceA =
    traceIdFor(result, focusTrial) ?? rm.traceOf(focused?.run_id, focusTrial);
  const traceB = compareId
    ? traceIdFor(compareResult, compareTrial) ??
      rm.traceOf(compareId, compareTrial)
    : null;
  const marksFor = (runId, t) => rm.marksOf(runId, t);

  const [menu, setMenu] = useState(null);
  const pickable = (runs ?? []).filter((r) => r.run_id !== focused?.run_id);
  const menuItems = [
    ...pickable.map((r) => ({
      id: r.run_id,
      label: nameOf(r),
      secondary: r.run_id === baselineId ? "Baseline" : undefined,
      checked: r.run_id === compareId,
      run: () => patch({ vs: formatVs({ runId: r.run_id }) }),
    })),
    ...(compareId
      ? [
          { divider: true },
          {
            id: "none",
            label: "Stop comparing",
            icon: "mdi:close",
            run: () => patch({ vs: "none" }),
          },
        ]
      : []),
  ];
  const noTraceReason =
    result && !traceA
      ? result.status === "missing"
        ? `${runLabel(focused)} did not run this row.`
        : undefined
      : undefined;

  if (!row || !focused)
    return (
      <Box sx={{ p: 2 }}>
        <Typography typography="s2" color="text.secondary">
          Loading the trajectory…
        </Typography>
      </Box>
    );

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }} data-testid="trajectory-tab">
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          px: 2,
          pt: 1,
          pb: 0.75,
          minWidth: 0,
          flexWrap: "wrap",
          rowGap: 0.5,
        }}
      >
        <Box sx={{ flex: 1, minWidth: 200 }}>
          <RunProvenance run={focused} variant="line" />
        </Box>
        <TrySwitcher
          trial={focusTrial}
          trials={trials}
          outcome={outcome}
          onChange={(t) => patch({ trial: t > 1 ? t : 1 })}
        />
        <CustomTooltip
          show
          title={
            pickable.length
              ? "Compare this try with another run in view, on one graph and one time axis"
              : NO_OTHER_RUN
          }
          size="small"
          type="black"
        >
          <span>
            <Button
              size="small"
              color="inherit"
              // P10: one run in view → nothing to compare with (said, not an
              // empty menu).
              disabled={!pickable.length}
              onClick={(e) => setMenu(e.currentTarget)}
              endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
              sx={{ typography: "s2", flexShrink: 0 }}
              data-testid="compare-with"
            >
              {compareRun
                ? `Compare with ${runLabel(compareRun)}`
                : "Compare with"}
            </Button>
          </span>
        </CustomTooltip>
        <ItemsMenu
          open={Boolean(menu)}
          anchorEl={menu}
          onClose={() => setMenu(null)}
          items={menuItems}
        />
      </Stack>
      {/* P9: the trajectory takes the panel's remaining height (the panel
          body is the one scroller; the footer holds Test a fix). */}
      <Box
        sx={{
          flex: "1 1 auto",
          // IT-R3 G4 P2: never below what the trajectory needs (its tree
          // keeps 240 px; an open step adds its detail): the drawer body
          // scrolls instead of squeezing the tree to one row.
          minHeight: 320,
          mx: 2,
          mb: 1.5,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflow: "hidden",
        }}
      >
        <RunTrajectory
          key={`${traceA}|${traceB}`}
          traceId={traceA}
          runId={focused.run_id}
          rowId={row.example_id}
          trial={focusTrial}
          run={focused}
          compareTraceId={traceB}
          compareRunId={compareId}
          compareRun={compareRun}
          datasetId={datasetId}
          label={nameOf(focused)}
          compareLabel={compareRun ? nameOf(compareRun) : undefined}
          colors={{
            a: colorOf(colors?.[focused.run_id]),
            b: compareId ? colorOf(colors?.[compareId]) : undefined,
          }}
          markers={{
            a: marksFor(focused.run_id, focusTrial),
            b: compareId ? marksFor(compareId, compareTrial) : null,
          }}
          emptyReason={noTraceReason}
          surface="drawer"
          inDrawerTabs
          // IT-R5 G4 S2 (S7): the root names the row and the try it shows.
          rootLabels={{
            a: `${rowLabel(datasetKind, row.row_order)} · try ${focusTrial} of ${trials}`,
            b: compareId
              ? `${rowLabel(datasetKind, row.row_order)} · try ${compareTrial} of ${Math.max(trialCount(compareResult), compareInfo?.outcomes.length ?? 0, 1)}`
              : undefined,
          }}
          // S4 (G4, additive): open on the first error; the drawer's `Why it
          // failed` quotes it from the same trace query.
          initialFocus="first-error"
        />
      </Box>
      {/* W1-9 B3 (E39): the evals that judged this try, after its steps. */}
      <EvalStepsList
        traceId={traceA}
        runId={focused.run_id}
        runLabel={runLabel(focused)}
      />
    </Stack>
  );
};

TrajectoryTab.propTypes = {
  ...RESULT_DRAWER_TAB_PROPTYPES,
  colors: PropTypes.object,
  focusedRun: PropTypes.object,
};

export default TrajectoryTab;
