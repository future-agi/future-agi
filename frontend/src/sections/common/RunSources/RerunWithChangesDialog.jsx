// Rerun with changes (R4-SPEC D8; lane Ra MUST 8): what to change (the
// run's declared parameters as controls, each changed field marked with
// its old value; harness recipe fields through lane Rb's slots in
// compact use), what the machine accepts, which rows, tries, where it runs,
// and `Creates #3G · 10 tries · …` + Run (⌘↵).
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useRerunRun } from "src/api/develop/r4";
import { fmtCount } from "src/sections/common/format/fmt";
import {
  estimateText,
  matchPicked,
  rerunEstimate,
  runnerPays,
} from "src/sections/common/Runs/rerunEstimate";
import { perTryCapOf } from "src/sections/experiment-detail/ExperimentBarRightSection/rerunPlan";
import { notify } from "src/sections/common/ux/toast";
import { MOD_KEY_LABEL } from "src/utils/keymap";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import ParamControls from "./ParamControls";
import HarnessRecipe from "./HarnessRecipe";
import SourceLogo from "./SourceLogo";
import { entryOfConfig } from "./catalog";
import { changedParams, paramDefaults } from "./sourceModel";
import { costPerTry as modelCostPerTry, engineTry } from "./declaredUses";
import { recipeFromConfig } from "./recipe/recipeModel";
import {
  MediaChanges,
  PromptChanges,
  RecipeBudget,
  SourceChanges,
  budgetChangeLine,
  budgetOf,
  executionOfPick,
  machineBlock,
  promptChangesOf,
  runModelOf,
  whereOptions,
} from "./RerunDialogParts";
import { mediaChangesOf, mediaTypeOfConfig } from "./mediaChangesModel";
import {
  sourceChangeLine,
  sourceChangesOf,
  sourceKindOfConfig,
} from "./sourceChangesModel";

const show = (v) => (typeof v === "string" ? v : JSON.stringify(v));

/** `temperature 0.2 → 0` per changed param (pure). */
export function changeLine(schema, before, after) {
  return changedParams(
    (schema ?? []).map((p) => ({ ...p, default: before?.[p.name] })),
    after,
  )
    .map((n) => `${n} ${show(before?.[n])} → ${show(after?.[n])}`)
    .join(" · ");
}

/**
 * v2.1 g4 (knob:rerun-dialog:tries): up to 3 tries per row, like the Test a
 * fix dock: every try the one run engine plays has its own trace.
 */
export const MAX_RERUN_TRIES = 3;
export const clampRerunTries = (v) =>
  Math.min(MAX_RERUN_TRIES, Math.max(1, Math.round(Number(v) || 1)));

/**
 * v2.1 g4 (info:rerun-dialog:estimate): what a child's try costs against
 * its parent's, from the one cost model the run engine prices tries with
 * (declaredUses.engineTry: the model and its max tokens). The child plays
 * at its parent's recorded usage, so its price is the parent's cost per try
 * times this ratio. Other kinds: the models' list prices. Pure.
 */
export function childPriceRatio({
  kind,
  parentModel,
  childModel,
  parentMaxTokens = null,
  childMaxTokens = null,
}) {
  if (kind === "prompt" || kind === "agent") {
    const from = engineTry({
      kind,
      model: parentModel,
      maxTokens: parentMaxTokens,
    });
    const to = engineTry({
      kind,
      model: childModel ?? parentModel,
      maxTokens: childMaxTokens ?? parentMaxTokens,
    });
    if (from?.usd > 0 && to?.usd >= 0) return to.usd / from.usd;
  }
  return childModel && parentModel && childModel !== parentModel
    ? (modelCostPerTry(childModel) ?? 0) / (modelCostPerTry(parentModel) || 1)
    : 1;
}

/** The tries a rerun creates for a scope (pure). */
export function rerunTries({ scope, rows, failedRows, pickedRows, tries }) {
  const t = Math.max(1, Number(tries) || 1);
  const n =
    scope === "failed"
      ? Number(failedRows) || 0
      : scope === "rows"
        ? Number(pickedRows) || 0
        : Number(rows) || 0;
  return n * t;
}

export default function RerunWithChangesDialog({
  open,
  onClose,
  run,
  nextLabel,
  failedRows,
  rowNoun = "row",
  initialScope = "all",
  initialRowIds = [],
  machine,
  machines = [],
  rowLabels = [],
  perTry,
}) {
  const config = run?.config ?? {};
  const schemaKey = JSON.stringify(config.params?.schema ?? []);
  const valuesKey = JSON.stringify(config.params?.values ?? {});
  const schema = useMemo(() => JSON.parse(schemaKey), [schemaKey]);
  const before = useMemo(
    () => ({ ...paramDefaults(schema), ...JSON.parse(valuesKey) }),
    [schema, valuesKey],
  );
  const [values, setValues] = useState(before);
  const [scope, setScope] = useState(initialScope);
  const [rowsText, setRowsText] = useState(initialRowIds.join(", "));
  const [tries, setTries] = useState(clampRerunTries(run?.trials ?? 1));
  // options-truth G6: the recipe, the prompt knobs and where it runs are
  // part of the request (never dropped).
  const [recipe, setRecipe] = useState(null);
  const [promptDraft, setPromptDraft] = useState({});
  const [sourceDraft, setSourceDraft] = useState({});
  const [mediaDraft, setMediaDraft] = useState({});
  const [where, setWhere] = useState("same");
  const [created, setCreated] = useState(null);
  // L2 G9: the harness recipe's per-try limits (max turns, cost cap, time
  // limit), edited here and sent with the recipe.
  const budgetKey = JSON.stringify(budgetOf(config.harness));
  const budgetBefore = useMemo(() => JSON.parse(budgetKey), [budgetKey]);
  const [budget, setBudget] = useState(budgetBefore);
  // Reset only when another run (or request) opens the dialog.
  useEffect(() => {
    setValues(before);
    setScope(initialScope);
    setTries(clampRerunTries(run?.trials ?? 1));
    setRecipe(null);
    setPromptDraft({});
    setSourceDraft({});
    setMediaDraft({});
    setWhere("same");
    setCreated(null);
    setBudget(budgetBefore);
    setRowsText(initialRowIds.join(", "));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.run_id, initialScope, initialRowIds.join(",")]);
  const rerun = useRerunRun();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const entry = entryOfConfig(config);
  const kind = config.kind ?? run?.kind;
  const local = config.execution?.mode === "local";
  const agentName =
    config.execution?.agent_name ?? run?.name?.split(" · ")[0] ?? "the agent";
  const rows = run?.examples ?? run?.progress?.total ?? 0;
  const typed = rowsText
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  // Picked rows are checked against the run's rows: `T99` is flagged, not
  // counted (the estimate counts matched rows only).
  const { matched: picked, unmatched } = rowLabels.length
    ? matchPicked(typed, rowLabels)
    : { matched: typed, unmatched: [] };
  const total = rerunTries({
    scope,
    rows,
    failedRows,
    pickedRows: picked.length,
    tries,
  });
  // IT-R5 G6 P5: the one estimator (the Rerun menu's), from the run's own
  // cost and time per try: `49 tasks · 147 tries · ~$1.79 · ~2 min`.
  const execution = executionOfPick(where);
  const childMode =
    execution.mode === "same" ? config.execution?.mode : execution.mode;
  const runnerPaid =
    execution.mode === "same"
      ? perTry?.runnerPaid ?? runnerPays(run)
      : execution.mode === "ci";
  // A model change prices the tries at the chosen model (MODEL_PRICES).
  const promptChanges = promptChangesOf(config, promptDraft);
  // L7b G3: an endpoint / A2A / MCP run's own edited fields.
  const sourceChanges = sourceChangesOf(config, sourceDraft);
  // L7b G3: a TTS / STT / image run's model (and TTS voice).
  const mediaType = mediaTypeOfConfig(config);
  const mediaChanges = mediaChangesOf(config, mediaDraft);
  const parentModel = config.harness?.model ?? runModelOf(config);
  const childModel =
    recipe?.model ?? promptDraft.model ?? values.model ?? parentModel;
  const parentMaxTokens =
    config.model?.params?.max_tokens ?? config.model?.params?.maxTokens ?? null;
  const priceRatio = childPriceRatio({
    kind,
    parentModel,
    childModel,
    parentMaxTokens,
    childMaxTokens:
      promptDraft.maxTokens !== undefined && promptDraft.maxTokens !== ""
        ? Number(promptDraft.maxTokens)
        : parentMaxTokens,
  });
  const baseCost = perTry?.costPerTry ?? run?.metrics?.cost_per_attempt;
  const est = rerunEstimate({
    rows: total / Math.max(1, tries),
    trials: tries,
    costPerTry:
      baseCost != null && priceRatio > 0 ? baseCost * priceRatio : baseCost,
    msPerTry: perTry?.msPerTry,
    concurrency: perTry?.concurrency ?? config.execution?.concurrency,
    runnerPaid,
    maxCostPerTry: perTry?.maxCostPerTry ?? perTryCapOf(config),
  });
  const changes = [
    changeLine(schema, before, values),
    sourceChangeLine(sourceChanges),
  ]
    .filter(Boolean)
    .join(" · ");
  const changedValues = Object.fromEntries(
    Object.entries(values).filter(
      ([k, v]) => JSON.stringify(before[k]) !== JSON.stringify(v),
    ),
  );
  const budgetChanges = budgetChangeLine(budgetBefore, budget);
  // The recipe sent: the editor's (model, permissions, MCP) with the limits.
  const sentRecipe =
    kind === "harness" && (recipe || budgetChanges)
      ? {
          ...(recipe ?? recipeFromConfig(config.harness ?? null, entry)),
          budget: {
            ...(recipe?.budget ?? budgetBefore),
            ...Object.fromEntries(
              Object.entries(budget).filter(([, v]) => v != null && v !== ""),
            ),
          },
        }
      : null;
  // The owner confirms only on the connected machine this rerun runs on.
  const runsOnId =
    childMode === "local"
      ? execution.machine_id ??
        (where === "same" ? config.execution?.machine_id : null)
      : null;
  const confirmMachine = runsOnId
    ? machines.find((m) => m.id === runsOnId) ??
      (machine?.id === runsOnId ? machine : null)
    : null;
  const options = whereOptions(config, machines, Date.now(), run);
  const currentBlock = machineBlock(machine);
  const blocked =
    (where === "same" ? currentBlock : null) ??
    options.find((o) => o.value === where)?.block ??
    (total === 0 ? "Nothing to rerun in this scope" : null);

  const submit = () => {
    if (blocked || rerun.isPending) return;
    const params = Object.fromEntries(
      Object.entries(values).filter(
        ([k, v]) => JSON.stringify(before[k]) !== JSON.stringify(v),
      ),
    );
    rerun.mutate(
      {
        runId: run.run_id,
        params,
        ...(sentRecipe ? { recipe: sentRecipe } : {}),
        changes: mediaType
          ? mediaChanges
          : [...promptChanges, ...sourceChanges],
        execution,
        scope,
        row_ids: scope === "rows" ? picked : [],
        trials: tries,
      },
      {
        onSuccess: (r) => {
          queryClient.invalidateQueries({
            queryKey: ["develop", "dataset-runs", run.dataset_id],
          });
          // L3 G9 (fix 7/8): the experiment's run list (header Rerun menu,
          // Changed configs only) and its matrix list the new run.
          queryClient.invalidateQueries({ queryKey: ["experiment"] });
          // A rerun that waits (CI job, your terminal, the machine owner)
          // stays here with its command and the stand-in.
          if (/^waiting_/.test(r?.state ?? "")) {
            setCreated(r);
            return;
          }
          onClose();
          const compare = `/dashboard/develop/${run.dataset_id}/compare/data?runs=${run.run_id},${r.run_id}&baseline=${run.run_id}`;
          notify(
            <span>
              {`Started ${r.label}${changes ? ` · ${changes}` : ""} · Compare with ${run.label} · `}
              <Box
                component="button"
                type="button"
                onClick={() => navigate(compare)}
                sx={{
                  all: "unset",
                  cursor: "pointer",
                  textDecoration: "underline",
                  fontWeight: 600,
                }}
              >
                Open compare
              </Box>
            </span>,
            { variant: "success", key: `rerun-${r.run_id}` },
          );
        },
        onError: (e) =>
          notify(getRequestErrorMessage(e, "Could not start the rerun."), {
            variant: "error",
            key: "rerun-error",
          }),
      },
    );
  };

  // dataset integration FE-0 / options-truth L5 G2: Future AGI runs every run this
  // dialog opens on (prompt, agent, HTTP): no machine, CI or owner wait.
  const waitLabel = "Run";
  if (!run) return null;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      onKeyDown={(e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
          e.preventDefault();
          submit();
        }
      }}
      data-testid="rerun-dialog"
    >
      <DialogTitle component="div" sx={{ pb: 1 }}>
        <Stack direction="row" alignItems="center" gap={1}>
          {entry ? <SourceLogo entry={entry} size={20} /> : null}
          <Typography
            typography="m3"
            fontWeight="fontWeightSemiBold"
            sx={{ flex: 1 }}
          >
            {`Rerun ${run.label} with changes`}
          </Typography>
          <IconButton size="small" aria-label="Close" onClick={onClose}>
            <Iconify icon="mdi:close" width={18} />
          </IconButton>
        </Stack>
        <Typography typography="s2" color="text.secondary" noWrap>
          {run.name}
        </Typography>
      </DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          <Stack spacing={1}>
            <Typography typography="s1" fontWeight="fontWeightSemiBold">
              What to change
            </Typography>
            {local && schema.length ? (
              <Typography
                typography="s2"
                color="text.secondary"
                data-testid="machine-accepts"
              >
                {`${config.execution?.label ?? "This machine"} accepts: ${schema.map((p) => p.name).join(", ")}`}
              </Typography>
            ) : null}
            {kind === "harness" ? (
              <Stack spacing={1}>
                <HarnessRecipe
                  compact
                  entry={entry}
                  mode={config.execution?.mode}
                  value={config.harness ?? {}}
                  onChange={(x) => setRecipe(x?.harness ?? null)}
                  ids={{ datasetId: run.dataset_id, runLabel: nextLabel }}
                  confirmMachine={confirmMachine}
                />
                <RecipeBudget
                  before={budgetBefore}
                  value={budget}
                  onChange={setBudget}
                />
              </Stack>
            ) : schema.length ? (
              <ParamControls
                schema={schema}
                values={values}
                onChange={setValues}
                original={before}
                showReset={false}
              />
            ) : mediaType ? (
              <MediaChanges
                config={config}
                value={mediaDraft}
                onChange={setMediaDraft}
              />
            ) : kind === "prompt" || kind === "agent" ? (
              <PromptChanges
                config={config}
                value={promptDraft}
                onChange={setPromptDraft}
              />
            ) : sourceKindOfConfig(config) ? (
              <SourceChanges
                config={config}
                value={sourceDraft}
                onChange={setSourceDraft}
              />
            ) : (
              <Typography typography="s2" color="text.secondary">
                Reruns the same endpoint, agent or tool with the rows and tries
                below. To change the endpoint itself, use Edit experiment.
              </Typography>
            )}
            {local && childMode === "local" ? (
              <Typography typography="s3" color="text.secondary">
                {`Fields ${agentName} didn't declare can't change here: the worker would refuse them.`}
              </Typography>
            ) : null}
          </Stack>
          <Stack spacing={0.5}>
            <Typography typography="s1" fontWeight="fontWeightSemiBold">
              Which rows
            </Typography>
            <RadioGroup
              value={scope}
              onChange={(e) => setScope(e.target.value)}
            >
              <FormControlLabel
                value="all"
                control={<Radio size="small" />}
                label={
                  <Typography typography="s2">{`All ${fmtCount(rows, rowNoun)}`}</Typography>
                }
              />
              <FormControlLabel
                value="failed"
                disabled={!failedRows}
                control={<Radio size="small" />}
                label={
                  <Typography typography="s2">
                    {failedRows == null
                      ? `Failed ${rowNoun}s`
                      : `Failed ${rowNoun}s (${fmtCount(failedRows)})`}
                  </Typography>
                }
              />
              <FormControlLabel
                value="rows"
                control={<Radio size="small" />}
                label={<Typography typography="s2">Pick rows…</Typography>}
              />
            </RadioGroup>
            {scope === "rows" ? (
              <TextField
                size="small"
                value={rowsText}
                onChange={(e) => setRowsText(e.target.value)}
                placeholder={rowNoun === "task" ? "T4, T12, T26" : "4, 12, 26"}
                inputProps={{ "aria-label": "Rows to rerun" }}
                sx={{ maxWidth: 320, ml: 3.5 }}
              />
            ) : null}
            {scope === "rows" && unmatched.length ? (
              <Typography
                typography="s2"
                color="warning.dark"
                sx={{ ml: 3.5 }}
                data-testid="rerun-unmatched"
              >
                {`${unmatched.join(", ")} ${unmatched.length === 1 ? "is" : "are"} not in ${run.label}; ${picked.length ? `rerunning ${fmtCount(picked.length, rowNoun)}` : "pick rows from this run"}.`}
              </Typography>
            ) : null}
          </Stack>
          <Stack direction="row" alignItems="center" gap={3} flexWrap="wrap">
            <Stack direction="row" alignItems="center" gap={1}>
              <Typography typography="s2" color="text.secondary">
                Tries per row
              </Typography>
              <TextField
                size="small"
                type="number"
                value={tries}
                onChange={(e) => setTries(clampRerunTries(e.target.value))}
                inputProps={{
                  min: 1,
                  max: MAX_RERUN_TRIES,
                  "aria-label": "Tries per row",
                  style: { textAlign: "right" },
                }}
                sx={{ width: 72 }}
              />
            </Stack>
          </Stack>
          {blocked && total > 0 ? (
            <Typography typography="s2" color="warning.dark">
              {blocked}
            </Typography>
          ) : null}
        </Stack>
      </DialogContent>
      <DialogActions
        sx={{
          px: 3,
          py: 1.5,
          gap: 1,
          flexWrap: runnerPaid ? "wrap" : "nowrap",
        }}
      >
        <Typography
          typography="s2"
          color="text.secondary"
          // IT-R5 G6 P5: the whole estimate shows (two lines at most),
          // never cut by a long `Create #3G and wait for CI`.
          sx={{
            // A runner-paid run's estimate takes its own line.
            flex: runnerPaid ? "1 1 100%" : 1,
            minWidth: 0,
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
          data-testid="rerun-creates"
        >
          {created
            ? `Created ${created.label} · ${estimateText(est, rowNoun)}`
            : `Creates ${nextLabel ?? "a new run"} · ${estimateText(est, rowNoun)}`}
        </Typography>
        <Button
          onClick={onClose}
          sx={{ color: "text.secondary", ml: runnerPaid ? "auto" : undefined }}
        >
          Cancel
        </Button>
        <CustomTooltip
          show={Boolean(blocked)}
          title={blocked ?? ""}
          size="small"
          placement="top"
        >
          <span>
            <LoadingButton
              variant="contained"
              color="primary"
              loading={rerun.isPending}
              disabled={Boolean(blocked) || Boolean(created)}
              onClick={submit}
              data-testid="rerun-run"
              endIcon={
                <Typography
                  component="span"
                  typography="s3"
                  sx={{ opacity: 0.7 }}
                >
                  {`${MOD_KEY_LABEL}↵`}
                </Typography>
              }
            >
              {created ? `${created.label} created` : waitLabel}
            </LoadingButton>
          </span>
        </CustomTooltip>
      </DialogActions>
    </Dialog>
  );
}

RerunWithChangesDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  run: PropTypes.object,
  nextLabel: PropTypes.string,
  failedRows: PropTypes.number,
  rowNoun: PropTypes.string,
  initialScope: PropTypes.string,
  initialRowIds: PropTypes.array,
  machine: PropTypes.object,
  machines: PropTypes.array,
  rowLabels: PropTypes.array,
  // IT-R5 G6 P5: rerunPlan's per-run numbers ({costPerTry, msPerTry,
  // trials, concurrency, runnerPaid}), so the dialog and the Rerun menu
  // print one estimate.
  perTry: PropTypes.object,
};
