import React, { useMemo } from "react";
import ErrorPolicySetting from "./ErrorPolicySetting";
import ScopePolicySummary from "../ScopePolicySummary";
import { reviewScoringLine } from "../reviewScoringLine";
import { metadataLine } from "../metadataLine";
import {
  plannedRunsLine,
  sampleLine,
} from "src/sections/common/Runs/runLedger";
import TrialReducerSetting from "./TrialReducerSetting";
import { sampleOfValues } from "../experimentFormValues";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  Link,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useController, useFieldArray, useWatch } from "react-hook-form";
import RecordToggle from "src/sections/common/RecordRuns/RecordToggle";
import { NOT_RECORDED_SUMMARY } from "src/sections/common/RecordRuns/recordModel";
import Iconify from "src/components/iconify";
import DirectionIcon from "src/sections/common/Metrics/DirectionIcon";
import { SplitChip } from "src/sections/common/Datasets/SplitChips";
import RunLetterBadge from "../RunLetterBadge";
import StartItBlock from "../StartItBlock";
import { useExperimentForm } from "../ExperimentFormContext";
import { CarryOverToggle } from "../StartFromExperiment";
import { CONCURRENCY_RANGE, EXPERIMENT_STEP, TRIALS_RANGE } from "../constants";
import { flattenRuns } from "../runPlan";
import { useRunExperimentStoreShallow } from "../../states";
import {
  carryOf,
  costLine,
  historyFromRuns,
  runEstimateLines,
} from "../costEstimateModel";
import {
  mediaUnitsOf,
  ownTriesLine,
  ownTriesOf,
  replayOf,
  runTriesOf,
} from "../estimateInputs";
import { useRunConfigs } from "src/api/develop/r2";
import { useEngineCostInputs } from "../useEngineCostInputs";
import { useCardsWithAgentUses } from "src/sections/common/RunSettings/useAgentUses";
import { Section } from "src/sections/common/ux/oneview";
import { useQuery } from "@tanstack/react-query";
import { fmtCost } from "src/sections/common/format/fmt";
import {
  PREVIEW_ROW_LIMIT,
  readAllTableRows,
  useScopePreviewRows,
} from "../scopeRows";
import { endpoints } from "src/utils/axios";
import { rowIdsFilter } from "src/sections/develop-detail/DataTab/DataGrid/dataGridView";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import {
  reviewLine,
  sideEffectRule,
} from "src/sections/common/RunSources/sourceModel";
import { AmberLine } from "src/sections/common/RunSources/parts";
import {
  emptyInputWarnings,
  rowsSendLine,
  runsCompareTable,
  capText,
} from "../reviewModel";
import { emptyInputNote, serverSampleRows } from "../scopePreview";
import { modalityWarnings } from "../variableMapping";
import { useModelInputs } from "../modelInputs";
import { mediaRunsOf } from "./mediaRuns";
import { defaultRecipe } from "src/sections/common/RunSources/recipe/recipeModel";
import { catalogEntry } from "src/sections/common/RunSources/catalog";
import { isSurfaceShown } from "src/launch/launchSwitch";

/**
 * R4 (lane Ra MUST 7, P4, P6): the answer first: how much will run, what
 * each row sends, and every warning that would change a result.
 */
function R4ReviewBlock({ values, runs, setValue }) {
  const ctx = useExperimentForm();
  const { kind: datasetKind, dataset } = useDatasetKind(ctx.datasetId);
  const rowNoun = datasetKind === "task_suite" ? "task" : "row";
  const setStep = useRunExperimentStoreShallow((st) => st.setCurrentStep);
  // A door's selected rows: read them all (the sample is only a page).
  const ids = values.rowIds ?? [];
  const { data: selected } = useQuery({
    queryKey: ["experiment-create", "scope-rows", ctx.datasetId, ids.join(",")],
    queryFn: () =>
      readAllTableRows(endpoints.develop.getDatasetDetail(ctx.datasetId), {
        filters: JSON.stringify([rowIdsFilter(ids)]),
      }),
    select: (res) => res.rows,
    enabled: Boolean(ctx.datasetId && ids.length),
  });
  const tries = Number(values.trials) || 1;
  const cards = values.promptConfig ?? [];
  const local = cards
    .map((c) => c?.r4)
    .find((r) => r?.mode === "local" && r?.form === "connect");
  // One estimate with the footer (costEstimateModel.costLine + history).
  const r1 = useRunConfigs(ctx.datasetId);
  const history = useMemo(() => historyFromRuns(r1.data?.results), [r1.data]);
  // options-truth L3 G3: carry-over on prices only the new rows (the same
  // number the carry line and the footer show).
  const carry = values.rowIds?.length
    ? null
    : carryOf({
        values,
        versions: ctx.versions,
        exampleCount: ctx.exampleCount,
        history,
      });
  const rows = values.rowIds?.length
    ? values.rowIds.length
    : carry
      ? carry.toRun
      : Number(ctx.exampleCount) || 0;
  // S5: each run's at a time never passes the experiment's "At most".
  const maxConcurrency = Number(values.maxConcurrency) || null;
  const replay = replayOf(values);
  // L5 G3: priced by the engine's inputs (agent node models, custom prices).
  const engine = useEngineCostInputs(cards);
  // W6 A3: agent cards carry what their version uses (Review Model / Tools).
  const usesCards = useCardsWithAgentUses(cards);
  const mediaUnits = mediaUnitsOf({
    cards,
    rows: ctx.rows,
    columns: ctx.columns,
  });
  const cost = costLine({
    promptConfig: engine.promptConfig,
    rows,
    trials: tries,
    evals: values.userEvalMetrics ?? [],
    history,
    maxConcurrency,
    replay,
    customPrices: engine.customPrices,
    mediaUnits,
  });
  const line = reviewLine({
    runs: runs.length,
    rows,
    tries,
    costUsd: cost?.total,
    timeText: cost?.timeText ?? null,
    localConcurrency: local ? local.limits?.concurrency : null,
    rowNoun,
  });
  // IT-R1 G1 P6: runs on the user's machine read their own limits: one
  // line per run (cost, cap, at a time, time) and a headline that splits
  // what Future AGI bills from what the user's model keys pay.
  // IT-R2 G1 P6: one cost and time story for every experiment (the footer
  // and the Review read the same function).
  const perRun = runEstimateLines({
    cards: engine.promptConfig,
    rows,
    trials: tries,
    estimate: cost?.estimate,
    ordinal: ctx.nextOrdinal,
    history,
    maxConcurrency,
    replay,
    customPrices: engine.customPrices,
    mediaUnits,
  });
  // Each run's own tries (the value the create path runs).
  const sideEffects = cards
    .filter((c) => c?.r4)
    .map(
      (c) =>
        sideEffectRule({
          kind: c.r4.kind,
          agentName: c.r4.agentName,
          sideEffectTools:
            c.r4.agent?.side_effect_tools ?? c.r4.uses?.sideEffectTools ?? [],
          environment: dataset?.environment,
          safety: c.r4.safety,
          tries: runTriesOf(c, tries),
          rows,
          rowNoun,
        }).reviewWarning,
    )
    .filter(Boolean);
  // L5 G3: the rows that run: picked rows, else the picked splits' rows,
  // else every row (column stats); the chosen Expected answers column.
  const splitIds = values.rowIds?.length ? [] : values.splitIds ?? [];
  // v2.1 (data:version, review:empty-input-warning): the rows in scope of
  // the pinned version; with a random sample, every row in scope is read
  // and the sample's own rows are counted (the same pick the run makes).
  const sampled = sampleOfValues(values);
  const { data: scopeRead } = useScopePreviewRows({
    datasetId: ctx.datasetId,
    versions: ctx.versions,
    versionId: values.datasetVersionId ?? null,
    splitIds,
    all: !values.rowIds?.length,
  });
  // Only a complete read stands for the run's rows; a scope over the
  // preview limit is reported as not checked.
  const fullScope = scopeRead?.complete ? scopeRead.rows : null;
  const unchecked = Boolean(
    !values.rowIds?.length &&
      scopeRead &&
      !scopeRead.complete &&
      (values.userEvalMetrics ?? []).length,
  );
  const scopeRows = values.rowIds?.length
    ? selected ?? []
    : fullScope
      ? serverSampleRows(fullScope, sampled)
      : [];
  const scoped = Boolean(values.rowIds?.length || splitIds.length || sampled);
  const empties = emptyInputWarnings(
    values.userEvalMetrics ?? [],
    scopeRows,
    ctx.columns ?? [],
    { useStats: false, referenceColumnId: values.columnId ?? null },
  );
  const sends = rowsSendLine(cards, ctx.mappingColumns ?? ctx.columns ?? [], {
    inputColumn: ctx.inputColumn,
  });
  // L3 G7 W6: a run whose model cannot read a mapped column's modality.
  const modelInputs = useModelInputs(runs.some((r) => r?.model));
  const unread = runs.flatMap((r) =>
    r?.model
      ? modalityWarnings(
          [
            {
              letter: r.letter,
              model: r.model,
              inputs: modelInputs?.[r.model] ?? [],
            },
          ],
          cards[r.configIndex]?.variableMapping ?? {},
          ctx.mappingColumns ?? ctx.columns ?? [],
        )
      : [],
  );
  // IT-R2 G1 P7: what each run uses, and a flag when runs differ.
  const table =
    runs.length > 1
      ? runsCompareTable(usesCards, runs, {
          ordinal: ctx.nextOrdinal,
          maxConcurrency,
          defaultsOf: (id) =>
            defaultRecipe(catalogEntry(id), dataset?.environment),
        })
      : null;
  const sameLimits = () => {
    const first = table?.rows.find((r) => r.atOnce)?.atOnce;
    if (!first) return;
    setValue(
      "promptConfig",
      cards.map((c) =>
        c?.r4?.limits
          ? {
              ...c,
              r4: { ...c.r4, limits: { ...c.r4.limits, concurrency: first } },
            }
          : c,
      ),
      { shouldDirty: true },
    );
  };
  if (!runs.length) return null;
  return (
    <Stack spacing={0.75} data-testid="r4-review">
      <Typography
        typography="s1"
        fontWeight="fontWeightSemiBold"
        data-testid="review-line"
      >
        {perRun ? perRun.headline : line}
      </Typography>
      {isSurfaceShown("exp.new.runSettings") && perRun?.worstMath ? (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="review-worst-math"
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          {perRun.worstMath}
        </Typography>
      ) : null}
      {perRun ? (
        <Stack spacing={0.25} data-testid="review-run-lines">
          {perRun.lines.map((l) => (
            <Typography
              key={l.label}
              typography="s2"
              color="text.secondary"
              sx={{ fontVariantNumeric: "tabular-nums" }}
            >
              {l.text}
            </Typography>
          ))}
          {perRun.judges ? (
            <Typography typography="s2" color="text.secondary">
              {perRun.judges}
            </Typography>
          ) : null}
          {perRun.gaps.map((g) => (
            <Typography
              key={g}
              typography="s2"
              color="text.secondary"
              data-testid="review-gap"
              sx={{ fontStyle: "italic" }}
            >
              {g}
            </Typography>
          ))}
        </Stack>
      ) : null}
      {isSurfaceShown("exp.new.runSettings") && table ? (
        <Box
          component="table"
          data-testid="review-runs-table"
          sx={{
            borderCollapse: "collapse",
            width: "100%",
            typography: "s2",
            "& th": {
              textAlign: "left",
              color: "text.secondary",
              fontWeight: "fontWeightMedium",
              pb: 0.25,
              pr: 1.5,
            },
            "& td": {
              py: 0.25,
              pr: 1.5,
              borderTop: "1px solid",
              borderColor: "divider",
              fontVariantNumeric: "tabular-nums",
            },
          }}
        >
          <thead>
            <tr>
              <th>Run</th>
              <th>Model</th>
              <th>Tools</th>
              <th>MCP</th>
              {table.hasPermissions ? <th>Secrets</th> : null}
              <th>At once</th>
              <th>Cap</th>
            </tr>
          </thead>
          <tbody>
            {table.rows.map((r) => (
              <tr key={r.label}>
                <td>{`${r.label} ${r.name}`}</td>
                <td>{r.model ?? "—"}</td>
                <td>{r.tools ?? "—"}</td>
                <td>{r.mcp ?? "—"}</td>
                {table.hasPermissions ? <td>{r.permissions ?? "—"}</td> : null}
                <td>{r.atOnceResolved ?? r.atOnce ?? "—"}</td>
                <td>
                  {r.capIsJudges
                    ? `judge spend stops at ${fmtCost(r.capPerTry)}`
                    : capText(r.capPerTry, {
                        perRun: r.capIsRun,
                        tries:
                          (Number(rows) || 0) *
                          runTriesOf(cards[r.configIndex], tries),
                      }) ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </Box>
      ) : null}
      {isSurfaceShown("exp.new.runSettings") && table?.differ ? (
        <AmberLine icon="mdi:swap-horizontal">
          <span data-testid="runs-differ">{table.differ}</span>
          {table.limitsDiffer ? (
            <Button
              size="small"
              onClick={sameLimits}
              sx={{
                minWidth: 0,
                px: 0.5,
                typography: "s2",
                verticalAlign: "baseline",
              }}
            >
              Use the same limits for all
            </Button>
          ) : null}
        </AmberLine>
      ) : null}
      {sends ? (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="rows-send"
        >
          {sends}
        </Typography>
      ) : null}
      {unread.map((w) => (
        <AmberLine key={`${w.letter}-${w.variable}`}>
          <span data-testid="modality-warning">{`#${ctx.nextOrdinal ?? ""}${w.letter} · ${w.text}`}</span>
        </AmberLine>
      ))}
      {sideEffects.map((w) => (
        <AmberLine key={w} icon="mdi:flash-alert-outline">
          {w}
        </AmberLine>
      ))}
      {empties.map((w) => (
        <AmberLine key={`${w.index}-${w.column}`}>
          <span data-testid="empty-input-warning">
            {emptyInputNote(w, rowNoun, { selected: scoped })}
          </span>{" "}
          <Button
            size="small"
            onClick={() => setStep(EXPERIMENT_STEP.EVALUATIONS)}
            sx={{
              minWidth: 0,
              px: 0.5,
              typography: "s2",
              verticalAlign: "baseline",
            }}
          >
            Map a column
          </Button>
          <Button
            size="small"
            color="error"
            onClick={() =>
              setValue(
                "userEvalMetrics",
                (values.userEvalMetrics ?? []).filter((_, i) => i !== w.index),
                { shouldDirty: true },
              )
            }
            sx={{
              minWidth: 0,
              px: 0.5,
              typography: "s2",
              verticalAlign: "baseline",
            }}
          >
            {`Remove ${w.evalName}`}
          </Button>
        </AmberLine>
      ))}
      {unchecked ? (
        <AmberLine>
          <span data-testid="empty-input-unchecked">
            {`Empty inputs not checked: more than ${PREVIEW_ROW_LIMIT.toLocaleString("en-US")} ${rowNoun}s in scope`}
          </span>
        </AmberLine>
      ) : null}
    </Stack>
  );
}
R4ReviewBlock.propTypes = {
  values: PropTypes.object.isRequired,
  runs: PropTypes.array.isRequired,
  setValue: PropTypes.func.isRequired,
};

const NumberSetting = ({
  control,
  name,
  label,
  range,
  helper,
  disabled,
  width = 140,
}) => {
  const { field, fieldState } = useController({ control, name });
  return (
    <Box>
      <TextField
        type="number"
        size="small"
        label={label}
        value={field.value ?? ""}
        disabled={disabled}
        onChange={(e) =>
          field.onChange(e.target.value === "" ? "" : Number(e.target.value))
        }
        inputProps={{ min: range.min, max: range.max, step: 1 }}
        error={Boolean(fieldState.error)}
        sx={{ width, bgcolor: "background.paper" }}
        data-testid={`setting-${name}`}
      />
      <Typography
        typography="s3"
        color="text.secondary"
        sx={{ mt: 0.5, maxWidth: 300 }}
      >
        {helper}
      </Typography>
    </Box>
  );
};
NumberSetting.propTypes = {
  control: PropTypes.object,
  name: PropTypes.string,
  label: PropTypes.string,
  range: PropTypes.object,
  helper: PropTypes.string,
  disabled: PropTypes.bool,
  width: PropTypes.number,
};

const SummaryCard = ({ title, step, children }) => {
  const { setCurrentStep } = useRunExperimentStoreShallow((s) => ({
    setCurrentStep: s.setCurrentStep,
  }));
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
        px: 1.5,
        py: 1.25,
        bgcolor: "background.paper",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", mb: 0.75 }}>
        <Typography
          typography="s2"
          fontWeight="fontWeightSemiBold"
          color="text.secondary"
        >
          {title}
        </Typography>
        <Box sx={{ flex: 1 }} />
        {step != null ? (
          <Link
            component="button"
            type="button"
            underline="hover"
            typography="s2"
            onClick={() => setCurrentStep(step)}
          >
            Edit
          </Link>
        ) : null}
      </Box>
      {children}
    </Box>
  );
};
SummaryCard.propTypes = {
  title: PropTypes.string,
  step: PropTypes.number,
  children: PropTypes.node,
};

const Line = ({ children }) => (
  <Box
    sx={{
      display: "flex",
      alignItems: "center",
      gap: 0.75,
      minHeight: 24,
      typography: "s2",
      minWidth: 0,
    }}
  >
    {children}
  </Box>
);
Line.propTypes = { children: PropTypes.node };

/**
 * Step 4 · Review & run (PRODUCT-SPEC §3.8): trials, concurrency and
 * metadata, then a summary of every step with Edit links.
 */
const ReviewStep = ({ control, setValue, isEditing = false }) => {
  const formCtx = useExperimentForm();
  const setCurrentStep = useRunExperimentStoreShallow(
    (st) => st.setCurrentStep,
  );
  const { nextOrdinal, versions, splits, exampleCount, userColumns } = formCtx;
  // Review S1: a task suite's rows are tasks on every line here.
  const { kind: reviewDatasetKind } = useDatasetKind(formCtx.datasetId);
  const reviewNoun = reviewDatasetKind === "task_suite" ? "task" : "row";
  const values = useWatch({ control });
  const { fields, append, remove } = useFieldArray({
    control,
    name: "metadata",
  });
  const runs =
    values.experimentType === "llm" || !values.experimentType
      ? flattenRuns(values.promptConfig ?? [])
      : [];
  // L3 G7 W5: a TTS / STT / Image form lists the runs it creates.
  const mediaRuns = mediaRunsOf(values.experimentType, values.promptConfig);
  const latest = Math.max(0, ...(versions ?? []).map((v) => v.number));
  const version =
    (versions ?? []).find((v) => v.id === values.datasetVersionId) ??
    (versions ?? []).find((v) => v.number === latest);
  const chosen = (splits ?? []).filter((s) =>
    (values.splitIds ?? []).includes(s.id),
  );
  const selectedRows = values.rowIds?.length ?? 0;
  // options-truth L1 finalize: a run card with its own limits carries its
  // own "at once" number wherever it runs; the experiment's number then caps
  // every run (each run plays at the smaller of the two), so it sits under
  // Advanced with that meaning instead of claiming to set each run's number.
  const anyOwnNumber = (values.promptConfig ?? []).some((c) => Boolean(c?.r4));
  const reference = (userColumns ?? []).find((c) => c.id === values.columnId);
  // options-truth L3 G3 (S5): a run card with its own tries plays those;
  // the experiment's Trials is only for runs that set none (prompt,
  // agent graph, endpoint).
  const cardsNow = values.promptConfig ?? [];
  const ownLine = ownTriesLine(cardsNow, runs, { ordinal: nextOrdinal });
  const takesTrials = runs.some((r) => !ownTriesOf(cardsNow[r.configIndex]));
  const triesEach = [
    ...new Set(
      runs.map((r) => runTriesOf(cardsNow[r.configIndex], values.trials)),
    ),
  ];
  // L6 launch switch (review:summary-cards, review:compare-table,
  // review:run-lines): dev runs every row once at its own concurrency and
  // stores no versions, splits, samples, trials, caps or metadata, so a
  // launch build shows none of the lines that claim them.
  const scopeShown = isSurfaceShown("exp.new.scope");
  const runSettingsShown = isSurfaceShown("exp.new.runSettings");
  const recordShown = isSurfaceShown("exp.new.record");
  // A stale value must remain clearable even while F05 is held. These
  // controls do not enable scope: serverBody still blocks the submission.
  const policyShown = scopeShown || values.errorPolicy === "exclude";
  const metadataShown = scopeShown || Boolean(metadataLine(values.metadata));
  const settingsShown =
    policyShown || metadataShown || runSettingsShown || recordShown;
  // Error-policy wording is the scope summary's; this line reduces tries only.
  const scoringLine = reviewScoringLine({
    trialReducer: values.trialReducer,
    maxTries: Math.max(1, ...triesEach.map(Number)),
  });
  const metaLine = metadataLine(values.metadata);

  return (
    <Stack spacing={2}>
      {!isEditing && scopeShown ? (
        <R4ReviewBlock values={values} runs={runs} setValue={setValue} />
      ) : null}
      {!isEditing && recordShown ? (
        <StartItBlock
          cards={values.promptConfig ?? []}
          runs={runs}
          ordinal={nextOrdinal ?? undefined}
          ctx={formCtx}
        />
      ) : null}
      {!isEditing && recordShown ? (
        <CarryOverToggle control={control} setValue={setValue} />
      ) : null}
      {settingsShown ? (
        <>
          <Box>
            <Typography
              typography="s1_2"
              fontWeight="fontWeightMedium"
              sx={{ mb: 1 }}
            >
              Settings
            </Typography>
            <Box sx={{ display: "flex", gap: 3, flexWrap: "wrap" }}>
              {runSettingsShown && (takesTrials || !runs.length) ? (
                <NumberSetting
                  control={control}
                  name="trials"
                  label={
                    ownLine ? "Trials (prompt and endpoint runs)" : "Trials"
                  }
                  range={TRIALS_RANGE}
                  disabled={isEditing}
                  helper={
                    ownLine
                      ? "Runs each example N times in the runs that set no tries of their own. The other runs keep their own tries."
                      : "Runs each example N times. Scores are averaged per example; examples whose trials disagree are flagged as flaky."
                  }
                />
              ) : null}
              {runSettingsShown && ownLine ? (
                <Box data-testid="review-own-tries" sx={{ maxWidth: 320 }}>
                  <Stack direction="row" alignItems="baseline" gap={0.75}>
                    <Typography typography="s2" color="text.primary">
                      {ownLine}
                    </Typography>
                    {!isEditing ? (
                      <Link
                        component="button"
                        type="button"
                        underline="hover"
                        typography="s2"
                        onClick={() =>
                          setCurrentStep(EXPERIMENT_STEP.CONFIGURATION)
                        }
                      >
                        Edit
                      </Link>
                    ) : null}
                  </Stack>
                  <Typography
                    typography="s3"
                    color="text.secondary"
                    sx={{ mt: 0.5 }}
                  >
                    Set on each run&apos;s card (Tries per row).
                  </Typography>
                </Box>
              ) : null}
              {policyShown && (
                <ErrorPolicySetting control={control} disabled={isEditing} />
              )}
              {runSettingsShown && Math.max(1, ...triesEach.map(Number)) > 1 ? (
                <TrialReducerSetting
                  control={control}
                  tries={Math.max(...triesEach.map(Number))}
                  disabled={isEditing}
                />
              ) : null}
              {/* IT-R2 G1 P8: a run with its own number keeps it (on its card);
              the experiment-wide cap is Advanced. */}
              {runSettingsShown &&
                (anyOwnNumber ? (
                  <Section
                    id="review.advanced"
                    surface="newExperiment"
                    title="Advanced"
                    defaultOpen={false}
                    summary="At most N at once in any run"
                  >
                    <Box sx={{ pt: 0.5, pl: 3 }}>
                      <NumberSetting
                        control={control}
                        name="maxConcurrency"
                        label="At most N at once in any run"
                        width={260}
                        range={CONCURRENCY_RANGE}
                        disabled={isEditing}
                        helper="Each run uses the number on its card and never goes above this one. Prompt, agent and endpoint runs use this number."
                      />
                    </Box>
                  </Section>
                ) : (
                  <NumberSetting
                    control={control}
                    name="maxConcurrency"
                    label="At once"
                    width={140}
                    range={CONCURRENCY_RANGE}
                    disabled={isEditing}
                    helper="Examples each run processes at once. Lower it if you hit provider rate limits."
                  />
                ))}
              {!isEditing && recordShown ? (
                <RecordToggle control={control} />
              ) : null}
            </Box>
          </Box>

          {metadataShown && (
            <Box>
              <Box sx={{ display: "flex", alignItems: "center", mb: 0.75 }}>
                <Typography typography="s1_2" fontWeight="fontWeightMedium">
                  Metadata
                </Typography>
                <Box sx={{ flex: 1 }} />
                <Button
                  size="small"
                  startIcon={<Iconify icon="mdi:plus" width={16} />}
                  onClick={() => append({ key: "", value: "" })}
                  disabled={isEditing}
                >
                  Add field
                </Button>
              </Box>
              <Stack spacing={0.75}>
                {fields.map((f, i) => (
                  <Box
                    key={f.id}
                    sx={{ display: "flex", gap: 1, alignItems: "center" }}
                  >
                    <MetaField
                      control={control}
                      name={`metadata.${i}.key`}
                      placeholder="key"
                      width={200}
                    />
                    <MetaField
                      control={control}
                      name={`metadata.${i}.value`}
                      placeholder="value"
                    />
                    <IconButton
                      size="small"
                      aria-label="Remove field"
                      onClick={() => remove(i)}
                    >
                      <Iconify icon="mdi:close" width={16} />
                    </IconButton>
                  </Box>
                ))}
              </Stack>
            </Box>
          )}
        </>
      ) : null}

      <Box sx={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1 }}>
        <SummaryCard
          title={`Runs · ${runs.length || mediaRuns.length}`}
          step={EXPERIMENT_STEP.CONFIGURATION}
        >
          <Stack spacing={0.25}>
            <Line>
              <Typography typography="s2" fontWeight="fontWeightMedium" noWrap>
                {values.name || "Untitled experiment"}
              </Typography>
            </Line>
            {(runs.length ? runs : mediaRuns).map((r) => (
              <Line key={r.letter}>
                <RunLetterBadge letter={r.letter} size={18} />
                <Typography typography="s2" noWrap>
                  {nextOrdinal != null ? `#${nextOrdinal}${r.letter} · ` : ""}
                  {r.name}
                </Typography>
              </Line>
            ))}
          </Stack>
        </SummaryCard>
        <SummaryCard title="Data" step={EXPERIMENT_STEP.DATA}>
          <Stack spacing={0.25}>
            {isSurfaceShown("dataset.versions") ? (
              <Line>
                {version
                  ? `v${version.number}${version.tags?.[0] ? ` · ${version.tags[0]}` : ""}${version.number === latest ? " · latest" : ""}`
                  : "Latest version"}
              </Line>
            ) : null}
            <Line>
              {selectedRows ? (
                <Typography typography="s2">{`These ${selectedRows} selected rows`}</Typography>
              ) : chosen.length ? (
                chosen.map((s) => (
                  <SplitChip key={s.id} split={s} size="small" />
                ))
              ) : (
                <Typography typography="s2">All rows</Typography>
              )}
              {exampleCount != null && !selectedRows ? (
                <Typography typography="s2" color="text.secondary">
                  {exampleCount} examples
                </Typography>
              ) : null}
            </Line>
            {scopeShown && sampleOfValues(values) ? (
              <Line>
                <Typography typography="s2" data-testid="review-sample">
                  {sampleLine(
                    {
                      ...sampleOfValues(values),
                      of: formCtx.rowsInScope,
                    },
                    { noun: reviewNoun },
                  )}
                </Typography>
              </Line>
            ) : null}
            <Line>
              <Typography typography="s2" color="text.secondary">
                Expected answers
              </Typography>
              <Typography typography="s2">
                {reference ? reference.name : "None"}
              </Typography>
            </Line>
          </Stack>
        </SummaryCard>
        <SummaryCard
          title={`Evaluations · ${(values.userEvalMetrics ?? []).length}`}
          step={EXPERIMENT_STEP.EVALUATIONS}
        >
          <Stack spacing={0.25}>
            {(values.userEvalMetrics ?? []).map((e, i) => (
              <Line key={`${e.name}-${i}`}>
                <Typography typography="s2" noWrap>
                  {e.name}
                </Typography>
                <DirectionIcon
                  higherIsBetter={e.higherIsBetter ?? null}
                  size={12}
                />
              </Line>
            ))}
          </Stack>
        </SummaryCard>
        {settingsShown ? (
          <SummaryCard title="Settings">
            <Stack spacing={0.25}>
              {runSettingsShown && (
                <Line>
                  {triesEach.length > 1 || (ownLine && !takesTrials)
                    ? ownLine
                    : `${triesEach[0] ?? (values.trials || 1)} ${Number(triesEach[0] ?? values.trials) === 1 ? "trial" : "trials"}`}{" "}
                  ·{" "}
                  {anyOwnNumber
                    ? `at most ${values.maxConcurrency || 10} at once per run`
                    : `concurrency ${values.maxConcurrency || 10}`}
                </Line>
              )}
              {scopeShown && <ScopePolicySummary values={values} />}
              {runSettingsShown && scoringLine ? (
                <Line>
                  <Typography
                    typography="s2"
                    data-testid="review-scoring-summary"
                  >
                    {scoringLine}
                  </Typography>
                </Line>
              ) : null}
              {runSettingsShown && (
                <Line>
                  <Typography
                    typography="s2"
                    data-testid="review-planned-tries"
                  >
                    {plannedRunsLine(
                      runs.map((r) => ({
                        label:
                          nextOrdinal != null
                            ? `#${nextOrdinal}${r.letter}`
                            : r.letter,
                        tries: runTriesOf(
                          cardsNow[r.configIndex],
                          values.trials,
                        ),
                      })),
                      {
                        rows: exampleCount,
                        noun: reviewNoun,
                        scope: sampleOfValues(values)
                          ? "random"
                          : chosen.map((s) => s.name).join(" ∪ ") || null,
                      },
                    )}
                  </Typography>
                </Line>
              )}
              {scopeShown && metaLine ? (
                <Line>
                  <Typography typography="s2" color="text.secondary" noWrap>
                    {metaLine}
                  </Typography>
                </Line>
              ) : null}
              {!isEditing && recordShown && values.record === false ? (
                <Line>
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    data-testid="review-record-summary"
                  >
                    {NOT_RECORDED_SUMMARY}
                  </Typography>
                </Line>
              ) : null}
            </Stack>
          </SummaryCard>
        ) : null}
      </Box>
    </Stack>
  );
};

const MetaField = ({ control, name, placeholder, width }) => {
  const { field } = useController({ control, name });
  return (
    <TextField
      size="small"
      value={field.value ?? ""}
      onChange={field.onChange}
      placeholder={placeholder}
      sx={{
        width: width ?? undefined,
        flex: width ? undefined : 1,
        bgcolor: "background.paper",
      }}
    />
  );
};
MetaField.propTypes = {
  control: PropTypes.object,
  name: PropTypes.string,
  placeholder: PropTypes.string,
  width: PropTypes.number,
};

ReviewStep.propTypes = {
  control: PropTypes.object.isRequired,
  setValue: PropTypes.func,
  isEditing: PropTypes.bool,
};

export default ReviewStep;
