import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
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
import RunLetterBadge from "./RunLetterBadge";
import { useExperimentForm } from "./ExperimentFormContext";
import { valuesFromExperiment } from "./experimentFormValues";
import { carryOverLine, historyFromRuns } from "./costEstimateModel";

export const START_FROM_PRESET = "bakeoff-v2";

/** Listed experiments on the dataset, newest first, from R1 runs. */
export function experimentsOf(runs) {
  const by = new Map();
  (runs ?? [])
    .filter((r) => r?.listed !== false && r?.experiment_id)
    .forEach((r) => {
      const e = by.get(r.experiment_id) ?? {
        id: r.experiment_id,
        ordinal: r.experiment_ordinal,
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
      runs: e.runs.sort((a, b) =>
        String(a.letter ?? "").localeCompare(String(b.letter ?? "")),
      ),
      rows: Math.max(0, ...e.runs.map((r) => Number(r.examples) || 0)),
      trials: Math.max(1, ...e.runs.map((r) => Number(r.trials) || 1)),
    }))
    .sort((a, b) => (b.ordinal ?? 0) - (a.ordinal ?? 0));
}

/**
 * A run Start from copies as a card (a prompt, an agent graph or an
 * endpoint run); code, harness, voice, upload and trace runs are not
 * (experimentFormValues.valuesFromExperiment counts them as not copied).
 */
export function copyableRun(r) {
  const kind = r?.config?.kind ?? r?.kind ?? null;
  return (
    ["prompt", "agent"].includes(r?.target_type) ||
    ["prompt", "agent", "http", "a2a", "mcp_tool"].includes(kind)
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
  const queryClient = useQueryClient();
  const r1 = useRunConfigs(datasetId);
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
  const chosen = experiments.find((e) => e.id === startFrom?.experimentId);

  const apply = async (exp, { preset } = {}) => {
    setConfirm(null);
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
          label: `#${exp.ordinal}`,
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
    if (!copyFrom) return;
    presetDone.current = true;
    apply(copyFrom);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startFromPreset, copyFrom?.id]);

  if (!experiments.length) return null;

  const labelOf = (e) =>
    `#${e.ordinal} ${e.name} (${e.runs.length} ${e.runs.length === 1 ? "run" : "runs"})`;

  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      data-testid="start-from"
      sx={{ minWidth: 0 }}
    >
      <Typography typography="s2" color="text.secondary" noWrap>
        Start from
      </Typography>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        disabled={busy}
        endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
        onClick={(e) => setAnchor(e.currentTarget)}
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
          {chosen.runs.map((r) => (
            <RunLetterBadge
              key={r.run_id}
              letter={r.letter}
              ordinal={chosen.ordinal}
            />
          ))}
        </Stack>
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
                `${e.rows.toLocaleString("en-US")} rows`,
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
            ? `The run cards of #${confirm.exp.ordinal} replace the ones you have now.`
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
