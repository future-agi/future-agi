import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Popover, Stack, Typography } from "@mui/material";
import { useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { useRunConfigs } from "src/api/develop/r2";
import { useExperimentForm } from "./ExperimentFormContext";
import {
  carryOf,
  costLine,
  estimateHelpOf,
  historyFromRuns,
  reviewLine,
  runEstimateLines,
  smokeSuggestion,
} from "./costEstimateModel";
import { mediaUnitsOf, replayOf } from "./estimateInputs";
import { editRunScope } from "./experimentFormValues";
import { flattenRuns } from "./runPlan";
import { useEngineCostInputs } from "./useEngineCostInputs";

/**
 * options-truth L3 G3: the inputs every estimate surface reads (Review,
 * footer, Details): the rows that will run (a door's picked rows, else the
 * new rows when carry-over is on, else every row), the experiment's "At
 * most N at once" and the replay mode.
 */
function useEstimateInputs(values) {
  const {
    datasetId,
    versions,
    exampleCount,
    rows: sampleRows,
    columns,
  } = useExperimentForm();
  // L5 G3: the engine's inputs (agent node models, custom model prices).
  const engine = useEngineCostInputs(values?.promptConfig);
  const r1 = useRunConfigs(datasetId);
  const history = useMemo(() => historyFromRuns(r1.data?.results), [r1.data]);
  const picked = values?.rowIds?.length ?? 0;
  const carry = picked
    ? null
    : carryOf({ values, versions, exampleCount, history });
  const rows = picked || (carry ? carry.toRun : exampleCount);
  return {
    history,
    carry,
    rows,
    maxConcurrency: Number(values?.maxConcurrency) || null,
    replay: replayOf(values),
    promptConfig: engine.promptConfig,
    customPrices: engine.customPrices,
    mediaUnits: mediaUnitsOf({
      cards: values?.promptConfig,
      rows: sampleRows,
      columns,
    }),
  };
}
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import CustomTooltip from "src/components/tooltip";

/**
 * Cost before you run (R2-SPEC F10): one line above `Run experiment`, e.g.
 * `162 agent calls (54 rows × 3 trials) · ≈ $0.16 · judges 324 calls ·
 * ≈ $0.07 · about 1 min`, with `How is this estimated?`. Over $10 it turns
 * amber and offers the smallest split first.
 */
const CostEstimateLine = ({ control, setValue }) => {
  const { splits } = useExperimentForm();
  const values = useWatch({ control });
  const {
    history,
    rows,
    maxConcurrency,
    replay,
    promptConfig,
    customPrices,
    mediaUnits,
  } = useEstimateInputs(values);
  const [help, setHelp] = useState(null);
  const inputs = {
    promptConfig,
    trials: values.trials,
    evals: values.userEvalMetrics,
    history,
    maxConcurrency,
    replay,
    customPrices,
    mediaUnits,
  };
  const line = costLine({ ...inputs, rows });
  if (!line) return null;
  const smoke =
    isSurfaceShown("exp.new.splitScope") &&
    line.overBudget &&
    !(values.splitIds ?? []).length
      ? smokeSuggestion({ ...inputs, splits, rows })
      : null;
  const amber = line.overBudget;

  return (
    <Stack spacing={0.25} data-testid="cost-estimate" sx={{ pt: 1 }}>
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ minWidth: 0 }}
      >
        <Iconify
          icon={amber ? "mdi:alert-outline" : "mdi:cash-multiple"}
          width={16}
          sx={{ color: amber ? "amber.700" : "text.secondary", flexShrink: 0 }}
        />
        <Typography
          typography="s2"
          color={amber ? "amber.700" : "text.secondary"}
          sx={{ fontVariantNumeric: "tabular-nums", minWidth: 0 }}
        >
          {line.text}
        </Typography>
      </Stack>
      <Stack direction="row" alignItems="center" gap={0.75} sx={{ pl: 2.75 }}>
        {line.note ? (
          <Typography typography="s3" color="text.disabled">
            {`${line.note.charAt(0).toUpperCase()}${line.note.slice(1)}.`}
          </Typography>
        ) : null}
        <Button
          size="small"
          onClick={(e) => setHelp(e.currentTarget)}
          sx={{ typography: "s3", px: 0.5, py: 0, minWidth: 0, flexShrink: 0 }}
        >
          How is this estimated?
        </Button>
      </Stack>
      {smoke ? (
        <Stack direction="row" alignItems="center" gap={0.75} sx={{ pl: 2.75 }}>
          <Typography typography="s2" color="amber.700">
            {smoke.text}
          </Typography>
          <Button
            size="small"
            onClick={() =>
              setValue("splitIds", [smoke.split.id], { shouldDirty: true })
            }
            sx={{ typography: "s3", px: 0.5, py: 0, minWidth: 0 }}
          >
            Use it
          </Button>
        </Stack>
      ) : null}
      <Popover
        open={Boolean(help)}
        anchorEl={help}
        onClose={() => setHelp(null)}
        anchorOrigin={{ vertical: "top", horizontal: "left" }}
        transformOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{ paper: { sx: { p: 1.5, width: 400 } } }}
      >
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          sx={{ mb: 0.75 }}
        >
          How is this estimated?
        </Typography>
        <Box component="ul" sx={{ m: 0, pl: 2, userSelect: "text" }}>
          {estimateHelpOf({ promptConfig }).map((t) => (
            <Typography
              key={t}
              component="li"
              typography="s2"
              color="text.secondary"
              sx={{ mb: 0.5 }}
            >
              {t}
            </Typography>
          ))}
        </Box>
        {line.rough ? (
          <Typography typography="s3" color="amber.700">
            No earlier run of this setup, so the amount is rough.
          </Typography>
        ) : null}
      </Popover>
    </Stack>
  );
};

CostEstimateLine.propTypes = {
  control: PropTypes.object.isRequired,
  setValue: PropTypes.func.isRequired,
};

export default CostEstimateLine;

/**
 * R3 P3 (SVP-4.5) the Review step's one plain line, in the footer:
 * `Run 30 tasks × 3 tries × 2 runs = 180 attempts · ≈ $2.10 · about 4 min`.
 * The full breakdown (judges, carry-over, smoke split) is CostEstimateLine
 * behind `Details` (EstimateDetails, A2-13); the tooltip says how it is
 * estimated.
 */
export function SimpleEstimate({ control, chip = false }) {
  const { datasetId, nextOrdinal, editBase, mappingColumns } =
    useExperimentForm();
  const { kind } = useDatasetKind(datasetId);
  const formValues = useWatch({ control });
  // L7b G2 (edit:estimate): an Edit save prices only what runs (re-run and
  // new cards) through the same estimator; kept runs cost $0.
  const scope = useMemo(
    () =>
      editBase
        ? editRunScope(editBase.values, formValues, {
            runs: editBase.runs ?? [],
            flatten: flattenRuns,
            columns: mappingColumns ?? [],
          })
        : null,
    [editBase, formValues, mappingColumns],
  );
  const values = scope
    ? { ...formValues, promptConfig: scope.promptConfig }
    : formValues;
  const {
    history,
    rows,
    maxConcurrency,
    replay,
    promptConfig,
    customPrices,
    mediaUnits,
  } = useEstimateInputs(values);
  const line = reviewLine({
    promptConfig,
    rows,
    trials: values.trials,
    evals: values.userEvalMetrics,
    history,
    maxConcurrency,
    replay,
    customPrices,
    mediaUnits,
    taskSuite: kind === "task_suite",
  });
  if (scope && !scope.promptConfig.length)
    return (
      <Typography
        typography="s2"
        color="text.primary"
        data-testid={chip ? "config-running-total" : "review-estimate"}
        sx={{ fontVariantNumeric: "tabular-nums", minWidth: 0 }}
      >
        Nothing re-runs · $0
      </Typography>
    );
  if (!line) return null;
  // IT-R1 G1 P6: with runs on the user's machine, the footer says the same
  // headline as Review (one number source for tries, cost, caps and time).
  // IT-R2 G1 P6: the footer says Review's headline, on one line.
  const perRun = runEstimateLines({
    cards: promptConfig ?? [],
    rows,
    trials: values.trials,
    estimate: line.line?.estimate,
    ordinal: nextOrdinal,
    history,
    maxConcurrency,
    replay,
    customPrices,
    mediaUnits,
  });
  return (
    <CustomTooltip
      show
      title={estimateHelpOf({ promptConfig }).join(" ")}
      size="small"
      placement="top"
      arrow
    >
      <Typography
        typography="s2"
        color={line.overBudget ? "amber.700" : "text.primary"}
        data-testid={chip ? "config-running-total" : "review-estimate"}
        // IT-R5 G1 P4: never clipped; wraps to 2 lines when it must.
        sx={{
          fontVariantNumeric: "tabular-nums",
          minWidth: 0,
          ...(chip
            ? {
                px: 0.75,
                py: 0.125,
                borderRadius: 0.75,
                border: "1px solid",
                borderColor: "divider",
                whiteSpace: "nowrap",
                flexShrink: 0,
              }
            : {}),
        }}
      >
        {scope
          ? [
              scope.rerun.length ? `Re-run ${scope.rerun.join(", ")}` : null,
              scope.added.length ? `New ${scope.added.join(", ")}` : null,
              // The same estimator's numbers for just these runs (the chip's
              // run count is replaced by their labels).
              ...(chip
                ? String(perRun?.chip ?? "")
                    .split(" · ")
                    .slice(1)
                : [perRun?.short ?? line.text]),
            ]
              .filter(Boolean)
              .join(" · ")
          : chip
            ? perRun?.chip ?? line.text
            : perRun
              ? perRun.short
              : line.text}
      </Typography>
    </CustomTooltip>
  );
}

SimpleEstimate.propTypes = {
  control: PropTypes.object,
  /** Additive (IT-R5 G1 P4): the Configuration step's running total. */
  chip: PropTypes.bool,
};
