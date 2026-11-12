import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Divider,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import { useWatch } from "react-hook-form";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import axios, { endpoints } from "src/utils/axios";
import { useRunConfigs } from "src/api/develop/r2";
import { useDatasetRuns } from "src/api/develop/runs";
import { experimentReadPolicy } from "src/api/develop/experiment-read-policy";
import { absentTemplateOf } from "src/launch/absentPathGuard";
import { SHOW_UNRELEASED_PARTS, isLaunchBuild } from "src/launch/launchSwitch";
import { useLayoutPrefs } from "src/sections/common/ux/oneview/layoutPrefs";
import { getRandomId } from "src/utils/utils";
import { hasSavedExperimentConfigs } from "./ExperimentSourceGate";
import { escapeModelKey, formModelParams } from "./modelParamNames";
import RunLetterBadge from "./RunLetterBadge";
import { useExperimentForm } from "./ExperimentFormContext";
import { valuesFromExperiment } from "./experimentFormValues";
import { carryOverLine, historyFromRuns } from "./costEstimateModel";

const NOT_IN_RELEASE = "Not available in this release";
const NOTHING_TO_COPY =
  "Started blank: no earlier experiment on this dataset has a run that can be copied. Add runs yourself.";

export const START_FROM_PRESET = "bakeoff-v2";

// Form-only (like notCopied; never in the create body or the More settings
// summary): the one-shot was handled in this wizard. The form outlives
// this component, which remounts when the first card arrives or the run
// picker opens and closes.
const PRESET_DONE = "startFromPresetDone";

// W8: the run list Start from picks from. Copies need each run's config
// (configsById); metrics and lineage are not shown here.
const START_FROM_INCLUDE = "config";

// W8: the run list is on the Results launch hold (launchHoldRoutes.js), and
// useRunConfigs refuses it in every launch build, so Start from never sent
// it: "Could not load previous experiments" with a Retry that sent nothing,
// then "Not available in this release" even on datasets with experiments.
// Start from reads it the way the Experiments tab does (useDatasetRuns) and
// counts it held only where the build refuses it for every reader: the
// absent-path guard is installed (the condition installAbsentPathGuard uses).
const absentPathGuardOn = () => isLaunchBuild() && !SHOW_UNRELEASED_PARTS;

/**
 * Listed experiments on the dataset, newest first, from R1 runs. The
 * Results catalogue has no experiment number, run letters or row count
 * (only each run's own `#n` and its progress): `ordinal` is null then.
 */
export function experimentsOf(runs) {
  const by = new Map();
  (runs ?? [])
    .filter((r) => r?.listed !== false && r?.experiment_id)
    .forEach((r) => {
      const e = by.get(r.experiment_id) ?? {
        id: r.experiment_id,
        ordinal: r.experiment_ordinal ?? null,
        name: r.experiment_name ?? "",
        versionNumber: r.dataset_version?.number ?? null,
        runs: [],
      };
      e.runs.push(r);
      by.set(r.experiment_id, e);
    });
  return [...by.values()]
    .map((e) => ({
      ...e,
      runs: e.runs.sort(
        (a, b) =>
          String(a.letter ?? "").localeCompare(String(b.letter ?? "")) ||
          (Number(a.ordinal) || 0) - (Number(b.ordinal) || 0),
      ),
      rows: Math.max(
        0,
        ...e.runs.map((r) => Number(r.examples ?? r.progress?.total) || 0),
      ),
      trials: Math.max(1, ...e.runs.map((r) => Number(r.trials) || 1)),
    }))
    .sort((a, b) => (b.ordinal ?? 0) - (a.ordinal ?? 0));
}

/** `#8`, or the experiment's name when the catalogue has no number. */
const sourceLabelOf = (e) =>
  e?.ordinal != null ? `#${e.ordinal}` : e?.name || "the earlier experiment";

/**
 * The ⌘K verb for the one-shot: `Start from last experiment (#8)`, or the
 * experiment's name when the run list has no experiment number, or no
 * suffix when neither is known.
 */
export function startFromLastLabel(run) {
  const which =
    run?.experiment_ordinal != null
      ? `#${run.experiment_ordinal}`
      : run?.experiment_name || null;
  return which
    ? `Start from last experiment (${which})`
    : "Start from last experiment";
}

/**
 * A run Start from copies as a card (a prompt, an agent graph or an
 * endpoint run); code, harness, voice, upload and trace runs are not
 * (experimentFormValues.valuesFromExperiment counts them as not copied).
 * The Results run list names what ran only by `task_kind`: a prompt or an
 * agent graph run is copied from the experiment's saved configs; an
 * external run (it ran outside Future AGI) is not.
 */
export function copyableRun(r) {
  const kind = r?.config?.kind ?? r?.kind ?? null;
  return (
    ["prompt", "agent"].includes(r?.target_type) ||
    ["prompt", "agent", "http", "a2a", "mcp_tool"].includes(kind) ||
    ["prompt", "graph"].includes(r?.task_kind)
  );
}

/** A tool setup that keeps the source run's tool modes. */
function toolSetupOf(config) {
  const tools = config?.tools ?? [];
  if (!tools.length) return undefined;
  const modes = [...new Set(tools.map((t) => t.mode ?? "mock"))];
  return modes.length === 1
    ? { mode: modes[0], perTool: {} }
    : {
        mode: "per_tool",
        perTool: Object.fromEntries(tools.map((t) => [t.name, t.mode])),
      };
}

/** One card per saved variant: the same prompt/model can have different settings. */
function cardsFromSavedConfigs(detail, runs, { mode }) {
  const keepIds = mode === "edit";
  const newestRuns = [...runs].sort((a, b) => (b.ordinal ?? 0) - (a.ordinal ?? 0));
  const runRef = (id, kind) => {
    if (!keepIds) return {};
    const run = newestRuns.find(
      (r) => (r[`${kind}_config_id`] ?? r.native_config_id) === id,
    );
    return run?.run_id ? { runId: run.run_id } : {};
  };
  const prompts = detail.prompt_configs.map((config) => {
    const id = keepIds ? config.id : getRandomId();
    const params = config.model_params ?? {};
    return {
      _itemId: id,
      experimentType: "llm",
      targetType: "prompt",
      promptId: config.prompt_id ?? null,
      promptVersion: config.prompt_version ?? null,
      // The form's check takes a text label (schema.js versionLabel).
      versionLabel: String(config.prompt_version_name ?? ""),
      name: config.prompt_name ?? config.name,
      model: [{
        id,
        value: config.model,
        providers: params.providers ?? config.providers ?? "",
        logoUrl: params.logoUrl ?? config.logo_url ?? "",
        ...(keepIds && { promptConfigId: config.id }),
        ...runRef(config.id, "prompt"),
      }],
      modelParams: { [escapeModelKey(config.model)]: formModelParams(params) },
      configuration: config.configuration ?? { toolChoice: "auto", tools: [] },
      outputFormat: config.output_format ?? "string",
      isDraft: false,
      variables: [],
      variableMapping: {},
    };
  });
  const agents = detail.agent_configs.map((config) => ({
    _itemId: keepIds ? config.id : getRandomId(),
    experimentType: "llm",
    targetType: "agent",
    agentId: config.agent_id ?? null,
    agentVersion: config.agent_version ?? null,
    // The server names a graph version by its number (2); the form's
    // check refused a number, so Run on a copied agent run sent nothing.
    versionLabel: String(config.agent_version_name ?? ""),
    name: config.agent_name ?? config.name,
    ...(keepIds && { promptConfigId: config.id }),
    ...runRef(config.id, "agent"),
    outputFormat: "string",
    isDraft: false,
    variables: [],
    variableMapping: {},
  }));
  return [...prompts, ...agents];
}

/**
 * Run cards of an experiment (configs unchanged), R1 config merged in.
 * Start from and Duplicate & edit copy them ("duplicate"); Edit keeps the
 * run ids ("edit"), so every door shows a run as the same R4 card.
 */
export function cardsFromExperiment(
  detail,
  configsById = {},
  { mode = "duplicate" } = {},
) {
  // the interface contract: a config's earlier runs (#2A when #2A·2 exists) are kept results,
  // not cards: every door starts from the newest run of each config.
  const runs = (detail?.runs ?? [])
    .filter((r) => r.latest_of_config !== false)
    .map((r) => ({
      ...r,
      config: r.config ?? configsById[r.run_id]?.config ?? null,
    }));
  const values = valuesFromExperiment({ ...detail, runs }, { mode });
  if (hasSavedExperimentConfigs(detail)) {
    // Saved V2 configs, not the bounded/sanitized results catalog, define the
    // editable variants. Preserve non-native runs using the existing policy.
    const otherValues = valuesFromExperiment(
      {
        ...detail,
        runs: runs.filter((r) => !["prompt", "graph", "agent"].includes(r.task_kind)),
      },
      { mode },
    );
    return {
      ...values,
      promptConfig: [
        ...cardsFromSavedConfigs(detail, runs, { mode }),
        ...otherValues.promptConfig,
      ],
      notCopied: otherValues.notCopied,
      notCopiedLabels: otherValues.notCopiedLabels,
    };
  }
  const promptRuns = runs.filter((r) => r.target_type === "prompt");
  return {
    ...values,
    promptConfig: values.promptConfig.map((card) => {
      if (card.targetType !== "prompt") return card;
      const src = promptRuns.find(
        (r) => r.target?.prompt_version_id === card.promptVersion,
      );
      const setup = toolSetupOf(src?.config);
      return setup ? { ...card, toolSetup: setup } : card;
    }),
  };
}

/**
 * `Start from ▾ #1 Run sources bake-off (5 runs)` (R2-SPEC §5.3.1): copies
 * an earlier experiment's run cards with their configs unchanged. Also
 * opened by the one-shot `newExperiment=bakeoff-v2` param (latest
 * experiment). Switching the source asks before replacing the cards.
 */
const StartFromExperiment = ({ control, setValue, getValues }) => {
  const { datasetId, startFromPreset, versions } = useExperimentForm();
  const [, patchLayout] = useLayoutPrefs("newExperiment");
  const queryClient = useQueryClient();
  const held =
    Boolean(datasetId) &&
    absentPathGuardOn() &&
    Boolean(absentTemplateOf(endpoints.develop.datasetRuns.list(datasetId)));
  const r1 = useDatasetRuns(datasetId, {
    include: START_FROM_INCLUDE,
    enabled: !held,
    ...experimentReadPolicy,
  });
  const runs = useMemo(() => r1.data?.results ?? [], [r1.data]);
  const experiments = useMemo(() => experimentsOf(runs), [runs]);
  const configsById = useMemo(
    () => Object.fromEntries(runs.map((r) => [r.run_id, r])),
    [runs],
  );
  const startFrom = useWatch({ control, name: "startFrom" });
  const [anchor, setAnchor] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [copyError, setCopyError] = useState(null);
  const chosen = experiments.find((e) => e.id === startFrom?.experimentId);
  const presetReadFailed =
    startFromPreset === START_FROM_PRESET &&
    (held || (r1.isError && !r1.isFetching) || Boolean(copyError));

  useEffect(() => {
    // One-shot reads run inside the closed More settings section. Reveal
    // recovery through layout state, never by changing the user's draft.
    if (presetReadFailed) patchLayout({ sections: { "newexp.more": true } });
  }, [presetReadFailed, patchLayout]);

  const apply = async (exp, { preset } = {}) => {
    setConfirm(null);
    setCopyError(null);
    if (!exp) {
      setValue("startFrom", null, { shouldDirty: true });
      setValue("promptConfig", [], { shouldDirty: true });
      setValue("notCopied", 0);
      setValue("notCopiedLabels", []);
      return;
    }
    setBusy(true);
    try {
      const res = await queryClient.fetchQuery({
        queryKey: ["experiment", exp.id],
        queryFn: () =>
          axios.get(endpoints.develop.experiment.getExperimentDetails(exp.id)),
      });
      const detail = res?.data?.result;
      const values = cardsFromExperiment(detail, configsById);
      // Set before the cards land (their arrival remounts this section). A
      // failed read leaves the draft as it was, so a remount retries.
      if (startFromPreset === START_FROM_PRESET) setValue(PRESET_DONE, true);
      setValue("promptConfig", values.promptConfig, { shouldDirty: true });
      // dataset integration FE-0: the runs a copy skipped (not offered here).
      setValue("notCopied", values.notCopied ?? 0);
      setValue("notCopiedLabels", values.notCopiedLabels ?? []);
      setValue("trials", values.trials ?? exp.trials, { shouldDirty: true });
      if (
        !(getValues("userEvalMetrics") ?? []).length &&
        values.userEvalMetrics?.length
      )
        setValue("userEvalMetrics", values.userEvalMetrics, {
          shouldDirty: true,
        });
      setValue(
        "startFrom",
        {
          experimentId: exp.id,
          label: sourceLabelOf(exp),
          versionNumber: exp.versionNumber,
          rows: exp.rows,
          // v2.1 g4 (action:start-from): the copied cards run as they are on
          // the one run engine (no designed story replays over them), and
          // every row runs (carry-over is not offered: more:carry-over).
          preset: preset ?? null,
          carryOver: false,
          versionMoved:
            Number(exp.versionNumber) <
            Math.max(0, ...(versions ?? []).map((v) => v.number)),
        },
        { shouldDirty: true },
      );
    } catch {
      // Transport failures need local recovery even without error.result.
      // Keep the source for an explicit retry; no draft fields changed on GET failure.
      setCopyError({ exp, preset });
    } finally {
      setBusy(false);
    }
  };

  const choose = (exp) => {
    setAnchor(null);
    if (exp?.id === startFrom?.experimentId) return;
    const cards = getValues("promptConfig") ?? [];
    if (cards.length) setConfirm({ exp, count: cards.length });
    else apply(exp);
  };

  // One-shot `newExperiment=bakeoff-v2` (Start from last experiment): the
  // newest experiment with a run Start from copies (v2.1 g4,
  // more:start-from:preset-link: never one whose runs all ran outside
  // Future AGI, which left an empty wizard), copied as it is (no preset).
  const presetDone = useRef(false);
  const copyFrom = experiments.find((e) => e.runs.some(copyableRun));
  useEffect(() => {
    if (presetDone.current || startFromPreset !== START_FROM_PRESET) return;
    // Once per wizard, not per mount: never copy over the user's runs.
    if (getValues(PRESET_DONE)) return;
    if (!copyFrom || r1.isPending || r1.isError) return;
    // A failed one-shot stays retryable through copyError, not an effect loop.
    presetDone.current = true;
    apply(copyFrom);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startFromPreset, copyFrom?.id, r1.isPending, r1.isError]);

  // A one-shot with nothing to copy (no earlier experiment, or none with a
  // run Start from copies) opens More settings and says so, instead of a
  // blank wizard with no word.
  const presetNothingToCopy =
    startFromPreset === START_FROM_PRESET &&
    r1.isSuccess &&
    !copyFrom &&
    !startFrom?.experimentId &&
    !busy &&
    !copyError;
  useEffect(() => {
    // Once per wizard: a remount does not reopen More settings after the
    // user collapsed it (the line below still shows).
    if (!presetNothingToCopy || getValues(PRESET_DONE)) return;
    setValue(PRESET_DONE, true);
    patchLayout({ sections: { "newexp.more": true } });
  }, [presetNothingToCopy, patchLayout, getValues, setValue]);

  const labelOf = (e) =>
    `${[e.ordinal != null ? `#${e.ordinal}` : null, e.name].filter(Boolean).join(" ")} (${e.runs.length} ${e.runs.length === 1 ? "run" : "runs"})`;

  return (
    <Stack
      direction="row"
      alignItems="center"
      flexWrap="wrap"
      gap={1}
      data-testid="start-from"
      aria-busy={busy || r1.isFetching}
      sx={{ minWidth: 0 }}
    >
      <Typography typography="s2" color="text.secondary" noWrap>
        Start from
      </Typography>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        disabled={busy || !experiments.length}
        endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
        onClick={(e) => setAnchor(e.currentTarget)}
        aria-label="Start from experiment"
        aria-haspopup="menu"
        sx={{
          typography: "s2",
          borderColor: "divider",
          minWidth: 0,
          maxWidth: 380,
          justifyContent: "space-between",
          "& .MuiButton-endIcon": { ml: 0.5 },
        }}
      >
        <Box
          component="span"
          sx={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {chosen ? labelOf(chosen) : "Blank"}
        </Box>
      </Button>
      {chosen ? (
        <Stack direction="row" gap={0.5} sx={{ flexShrink: 0 }}>
          {/* Results catalogue runs have no letter (the badge needs one). */}
          {chosen.runs
            .filter((r) => r.letter)
            .map((r) => (
              <RunLetterBadge
                key={r.run_id}
                letter={r.letter}
                ordinal={chosen.ordinal}
              />
            ))}
        </Stack>
      ) : null}
      {held ? (
        <Typography
          role="status"
          typography="s2"
          color="text.secondary"
          data-testid="start-from-not-in-release"
        >
          {NOT_IN_RELEASE}
        </Typography>
      ) : r1.isError ? (
        <Alert
          severity="error"
          sx={{ width: "100%" }}
          action={
            <Button
              color="inherit"
              size="small"
              disabled={r1.isFetching}
              onClick={() => r1.refetch({ throwOnError: false })}
            >
              Retry experiments
            </Button>
          }
        >
          Could not load previous experiments. Your draft is unchanged. Retry to
          choose a starting experiment.
        </Alert>
      ) : !experiments.length ? (
        <Typography role="status" typography="s2" color="text.secondary">
          {r1.isPending
            ? "Loading experiments..."
            : "No previous experiments to start from."}
        </Typography>
      ) : presetNothingToCopy ? (
        <Typography
          role="status"
          typography="s2"
          color="text.secondary"
          data-testid="start-from-nothing-to-copy"
        >
          {NOTHING_TO_COPY}
        </Typography>
      ) : null}
      {busy ? (
        <Typography role="status" typography="s2" color="text.secondary">
          Copying experiment...
        </Typography>
      ) : null}
      {copyError ? (
        <Alert
          severity="error"
          sx={{ width: "100%" }}
          action={
            <Button
              color="inherit"
              size="small"
              disabled={busy}
              onClick={() => apply(copyError.exp, { preset: copyError.preset })}
            >
              Retry copy
            </Button>
          }
        >
          {`Could not load the run cards for ${sourceLabelOf(copyError.exp)}. Your draft is unchanged. Retry to copy this experiment.`}
        </Alert>
      ) : null}
      <Menu
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        slotProps={{ paper: { sx: { minWidth: 320, maxWidth: 420, mt: 0.5 } } }}
        MenuListProps={{ dense: true }}
      >
        <MenuItem selected={!chosen} onClick={() => choose(null)}>
          <ListItemText
            primary="Blank"
            secondary="Add runs yourself"
            primaryTypographyProps={{ typography: "s1" }}
            secondaryTypographyProps={{ typography: "s3" }}
          />
        </MenuItem>
        <Divider />
        {experiments.map((e) => (
          <MenuItem
            key={e.id}
            selected={e.id === chosen?.id}
            onClick={() => choose(e)}
          >
            <ListItemText
              primary={labelOf(e)}
              secondary={[
                e.versionNumber ? `v${e.versionNumber}` : null,
                e.rows ? `${e.rows.toLocaleString("en-US")} rows` : null,
                `${e.trials} ${e.trials === 1 ? "trial" : "trials"}`,
                e.runs.map((r) => r.label).join(" "),
              ]
                .filter(Boolean)
                .join(" · ")}
              primaryTypographyProps={{ typography: "s1", noWrap: true }}
              secondaryTypographyProps={{ typography: "s3", noWrap: true }}
            />
          </MenuItem>
        ))}
      </Menu>
      <ConfirmDialog
        open={Boolean(confirm)}
        onClose={() => setConfirm(null)}
        title={`Replace the ${confirm?.count ?? 0} run ${confirm?.count === 1 ? "card" : "cards"}?`}
        content={
          confirm?.exp
            ? `The run cards of ${sourceLabelOf(confirm.exp)} replace the ones you have now.`
            : "The run cards are removed. You can add runs yourself."
        }
        action={
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={() => apply(confirm?.exp ?? null)}
          >
            Replace
          </Button>
        }
      />
    </Stack>
  );
};

StartFromExperiment.propTypes = {
  control: PropTypes.object,
  setValue: PropTypes.func,
  getValues: PropTypes.func,
};

/**
 * Review step: `Carry over 24 unchanged rows from #1 (same configs, same
 * row text) · run 6 new rows × 5 runs × 3 trials = 90 attempts · ≈ $6.21
 * in Future AGI · …` with a toggle (on by default; off = run every row).
 */
export const CarryOverToggle = (props) =>
  // v2.1 g4 (more:carry-over): HIDDEN. The one run engine reruns every row
  // of a created experiment; carrying rows over is not offered until it
  // copies the earlier tries for real.
  CARRY_OVER_OFFERED ? <CarryOverToggleBody {...props} /> : null;

const CARRY_OVER_OFFERED = false;

const CarryOverToggleBody = ({ control, setValue }) => {
  const { datasetId, versions, exampleCount } = useExperimentForm();
  const values = useWatch({ control });
  const r1 = useRunConfigs(datasetId);
  const sf = values.startFrom;
  const latest = Math.max(0, ...(versions ?? []).map((v) => v.number));
  const version =
    (versions ?? []).find((v) => v.id === values.datasetVersionId)?.number ??
    latest;
  const history = useMemo(() => historyFromRuns(r1.data?.results), [r1.data]);
  const line = carryOverLine({
    source: sf,
    versionNumber: version,
    rows: exampleCount,
    promptConfig: values.promptConfig,
    trials: values.trials,
    history,
  });
  // Keep `versionMoved` in step with the chosen version (the create body
  // sends carry_over_from_experiment_id only then).
  const moved = Boolean(line);
  useEffect(() => {
    if (!sf?.experimentId || Boolean(sf.versionMoved) === moved) return;
    setValue("startFrom", { ...sf, versionMoved: moved });
  }, [sf, moved, setValue]);
  const frame = {
    direction: "row",
    gap: 1,
    alignItems: "flex-start",
    "data-testid": "carry-over",
    sx: {
      p: 1.25,
      border: "1px solid",
      borderColor: "divider",
      borderRadius: 0.5,
      bgcolor: "background.neutral",
    },
  };
  // options-truth L3 G3 (more:carry-over): a Start from on the same dataset
  // version has nothing to carry; say so instead of hiding the toggle.
  if (!line) {
    if (!sf?.experimentId) return null;
    return (
      <Stack {...frame}>
        <Switch
          size="small"
          checked={false}
          disabled
          inputProps={{ "aria-label": "Carry over unchanged rows" }}
          sx={{ mt: -0.25 }}
        />
        <Typography typography="s2" color="text.secondary">
          {`Dataset unchanged since ${sf.label ?? "the source experiment"}${
            sf.versionNumber ? ` (v${sf.versionNumber})` : ""
          }: nothing to carry`}
        </Typography>
      </Stack>
    );
  }
  const on = sf?.carryOver !== false;
  return (
    <Stack {...frame}>
      <Switch
        size="small"
        checked={on}
        onChange={(e) =>
          setValue(
            "startFrom",
            { ...sf, carryOver: e.target.checked },
            { shouldDirty: true },
          )
        }
        inputProps={{ "aria-label": "Carry over unchanged rows" }}
        sx={{ mt: -0.25 }}
      />
      <Typography
        typography="s2"
        color={on ? "text.primary" : "text.secondary"}
        sx={{ fontVariantNumeric: "tabular-nums" }}
      >
        {on
          ? line.text
          : `Carry over is off: all ${Number(exampleCount ?? 0).toLocaleString("en-US")} rows run again.`}
      </Typography>
    </Stack>
  );
};

CarryOverToggleBody.propTypes = {
  control: PropTypes.object.isRequired,
  setValue: PropTypes.func.isRequired,
};

export default StartFromExperiment;
