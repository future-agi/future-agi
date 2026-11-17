import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Collapse,
  InputAdornment,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import { useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import { useGetDatasetColumns } from "src/api/develop/develop-detail";
import RunCardShell from "./RunCardShell";
import TryOnOneRow from "./TryOnOneRow";
import { useExperimentForm } from "./ExperimentFormContext";
import { DEFAULT_HARNESS, newRunCard } from "./experimentFormValues";
import { PROMPT_CONFIG_TYPE } from "./constants";
import { InfoTip, MONO } from "./RunFromCodeParts";
import {
  CUSTOM_HARNESS_HELP,
  HARNESSES,
  HOW_IT_RUNS,
  SEED_INSTRUCTIONS,
  harnessOf,
  modelsFor,
  sandboxFrom,
  wordCount,
} from "./harnessDefaults";

const Field = ({ label, tip, children }) => (
  <Stack direction="row" alignItems="flex-start" gap={1} sx={{ minWidth: 0 }}>
    <Stack
      direction="row"
      alignItems="center"
      gap={0.5}
      sx={{ width: 150, flexShrink: 0, minHeight: 32 }}
    >
      <Typography typography="s2" color="text.secondary">
        {label}
      </Typography>
      {tip ? <InfoTip title={tip} /> : null}
    </Stack>
    <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
  </Stack>
);
Field.propTypes = {
  label: PropTypes.string,
  tip: PropTypes.string,
  children: PropTypes.node,
};

const Select = ({ value, options, onChange, label, width = 220 }) => (
  <Box sx={{ width, flexShrink: 0 }}>
    <FormSearchSelectFieldState
      size="small"
      fullWidth
      showClear={false}
      label={label}
      value={value ?? ""}
      options={options}
      onChange={(e) => onChange(e?.target?.value ?? e)}
      sx={{ bgcolor: "background.paper" }}
    />
  </Box>
);
Select.propTypes = {
  value: PropTypes.any,
  options: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  label: PropTypes.string,
  width: PropTypes.number,
};

const NumberField = ({ label, value, onChange, end, start, width = 150 }) => (
  <TextField
    size="small"
    type="number"
    label={label}
    value={value ?? ""}
    onChange={(e) =>
      onChange(e.target.value === "" ? null : Number(e.target.value))
    }
    inputProps={{ min: 0, step: start ? 0.05 : 1 }}
    InputProps={{
      ...(end && {
        endAdornment: <InputAdornment position="end">{end}</InputAdornment>,
      }),
      ...(start && {
        startAdornment: (
          <InputAdornment position="start">{start}</InputAdornment>
        ),
      }),
    }}
    sx={{ width, bgcolor: "background.paper" }}
  />
);
NumberField.propTypes = {
  label: PropTypes.string,
  value: PropTypes.number,
  onChange: PropTypes.func.isRequired,
  end: PropTypes.string,
  start: PropTypes.string,
  width: PropTypes.number,
};

/**
 * Agent harness run card (R2-SPEC §5.3.4, Preview): Claude Code, Codex CLI
 * and more, one sandbox per row and trial. Writes
 * `${fieldPrefix}.harness_config` (r2Shapes, manifest row 42c).
 *
 * Props (frozen, R2 manifest row 20; same as ExternalRunCard).
 */
const HarnessRunCard = ({
  index,
  control,
  setValue,
  fieldPrefix,
  letters = [],
  onRemove,
  showErrors = false,
  error,
  trials = 1,
}) => {
  const { datasetId } = useExperimentForm();
  const { kind, dataset } = useDatasetKind(datasetId);
  const env = sandboxFrom(dataset?.environment);
  const { data: columns = [] } = useGetDatasetColumns(datasetId, {
    enabled: Boolean(datasetId),
  });
  const h =
    useWatch({ control, name: `${fieldPrefix}.harness_config` }) ??
    DEFAULT_HARNESS;
  const list = useWatch({ control, name: "promptConfig" }) ?? [];
  const set = (key, value) =>
    setValue?.(`${fieldPrefix}.harness_config.${key}`, value, {
      shouldDirty: true,
    });
  // L3 G6 fix 8: the card and run read `<Harness> × <model>`; a name that
  // was the old automatic one follows a model or harness change (a name
  // the user typed is kept).
  const runName = useWatch({ control, name: `${fieldPrefix}.runName` });
  const retarget = (name, model) => {
    const was = h.model ? `${h.name} × ${h.model}` : h.name;
    if (!runName || runName === was)
      setValue?.(
        `${fieldPrefix}.runName`,
        model ? `${name} × ${model}` : name,
        { shouldDirty: true },
      );
  };
  // a copied run's model the list lacks stays selectable (never blank)
  const modelChoices = (name, model) => {
    const list = modelsFor(name);
    return model && !list.includes(model) ? [...list, model] : list;
  };
  const [showTools, setShowTools] = useState(false);
  const [showInstructions, setShowInstructions] = useState(false);
  const harness = harnessOf(h.name);
  const goalColumn = columns.find((c) => c.name === "customer_goal")?.name;

  // Seed what the dataset brings (sandbox, MCP tools, secrets, customer)
  // and the instructions file, once per card.
  const seeded = useRef(false);
  useEffect(() => {
    if (seeded.current || !dataset) return;
    seeded.current = true;
    if (!h.sandbox) set("sandbox", env.sandbox);
    if (!h.mcp_servers?.length && env.mcp)
      set("mcp_servers", [
        {
          name: env.mcp.name,
          tools: [...(env.mcp.tool_names ?? [])],
          tool_count: env.mcp.tools,
          disabled_tools: [],
        },
      ]);
    if (!h.secrets?.length && env.secrets.length) set("secrets", env.secrets);
    if (!h.instructions) set("instructions", SEED_INSTRUCTIONS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataset]);
  useEffect(() => {
    if (h.user_sim || !goalColumn) return;
    set("user_sim", {
      model: "gpt-4o-mini",
      from_column: goalColumn,
      max_turns: 8,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [goalColumn]);

  const mcp = h.mcp_servers?.[0] ?? null;
  const disabled = new Set(mcp?.disabled_tools ?? []);
  const toolNames = env.mcp?.tool_names ?? [];
  const enabledTools = toolNames.filter((t) => !disabled.has(t)).length;
  const toggleTool = (name) => {
    const next = new Set(disabled);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    set("mcp_servers", [
      {
        ...mcp,
        // the names the run may call (the engine filters by them) and their count
        tools: toolNames.filter((t) => !next.has(t)),
        tool_count: toolNames.length - next.size,
        disabled_tools: [...next],
      },
    ]);
  };
  const words = wordCount(h.instructions);
  const limits = h.limits ?? DEFAULT_HARNESS.limits;
  const setLimit = (key, v) => set("limits", { ...limits, [key]: v });

  const addAnother = () => {
    const models = modelsFor(h.name);
    const nextModel =
      models[(models.indexOf(h.model) + 1) % models.length] ?? h.model;
    const card = {
      ...newRunCard(PROMPT_CONFIG_TYPE.HARNESS),
      harness_config: { ...h, model: nextModel },
    };
    const at = Number.isFinite(index) ? index + 1 : list.length;
    setValue?.(
      "promptConfig",
      [...list.slice(0, at), card, ...list.slice(at)],
      { shouldDirty: true },
    );
  };

  return (
    <RunCardShell
      icon={
        <Iconify
          icon="mdi:console"
          width={16}
          sx={{ color: "text.secondary" }}
        />
      }
      letters={letters}
      title={`${h.name === "Custom command…" ? "Custom harness" : h.name}${h.model ? ` × ${h.model}` : ""}`}
      onRemove={onRemove}
      error={showErrors ? error : null}
    >
      <Stack spacing={1}>
        <Field
          label="Harness"
          tip={harness.custom ? CUSTOM_HARNESS_HELP : undefined}
        >
          <Stack direction="row" gap={1} flexWrap="wrap">
            <Select
              value={h.name}
              label="Harness"
              options={HARNESSES.map((x) => ({
                label: x.value,
                value: x.value,
              }))}
              onChange={(v) => {
                set("name", v);
                const model = modelsFor(v).includes(h.model)
                  ? h.model
                  : modelsFor(v)[0];
                if (model !== h.model) set("model", model);
                retarget(v, model);
              }}
            />
            <Select
              value={h.model}
              label="Model"
              options={modelChoices(h.name, h.model).map((m) => ({
                label: m,
                value: m,
              }))}
              onChange={(v) => {
                set("model", v);
                retarget(h.name, v);
              }}
            />
            {harness.custom ? (
              <TextField
                size="small"
                label="Command"
                placeholder="my-agent --prompt-file {task}"
                value={h.command ?? ""}
                onChange={(e) => set("command", e.target.value)}
                inputProps={{ style: { fontFamily: MONO, fontSize: 12 } }}
                sx={{ width: 300, bgcolor: "background.paper" }}
              />
            ) : null}
          </Stack>
        </Field>

        <Field
          label="Sandbox"
          tip={
            env.fromDataset
              ? "From this dataset's environment: the same image, start state and tools for every run."
              : undefined
          }
        >
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            sx={{ minHeight: 32 }}
          >
            <Typography typography="s2" sx={{ fontFamily: MONO }}>
              {env.sandbox}
            </Typography>
            {env.resources ? (
              <Typography typography="s2" color="text.secondary">
                · Resources {env.resources}
              </Typography>
            ) : (
              <Typography typography="s2" color="text.secondary">
                · no MCP tools
              </Typography>
            )}
          </Stack>
          {!env.fromDataset && kind !== "task_suite" ? (
            <Typography typography="s3" color="text.secondary">
              Task suites bring their own environment (Data › Environment).
            </Typography>
          ) : null}
        </Field>

        {env.mcp ? (
          <Field label="Tools">
            <Stack
              direction="row"
              alignItems="center"
              gap={1}
              sx={{ minHeight: 32 }}
            >
              <Typography typography="s2" sx={{ minWidth: 0 }}>
                MCP server {env.mcp.name} ({enabledTools}
                {enabledTools === toolNames.length
                  ? ""
                  : ` of ${toolNames.length}`}{" "}
                tools) · from this dataset&apos;s environment
              </Typography>
              <Button
                size="small"
                onClick={() => setShowTools((v) => !v)}
                endIcon={
                  <Iconify
                    icon={showTools ? "mdi:chevron-up" : "mdi:chevron-down"}
                    width={16}
                  />
                }
                sx={{ typography: "s2", flexShrink: 0, px: 1 }}
              >
                {showTools ? "Hide" : "Choose"}
              </Button>
            </Stack>
            <Collapse in={showTools} unmountOnExit>
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                  columnGap: 1,
                }}
              >
                {toolNames.map((t) => (
                  <Stack
                    key={t}
                    direction="row"
                    alignItems="center"
                    sx={{ minWidth: 0 }}
                  >
                    <Checkbox
                      size="small"
                      checked={!disabled.has(t)}
                      onChange={() => toggleTool(t)}
                      inputProps={{ "aria-label": t }}
                      sx={{ p: 0.5 }}
                    />
                    <Typography
                      typography="s3"
                      noWrap
                      sx={{ fontFamily: MONO }}
                    >
                      {t}
                    </Typography>
                  </Stack>
                ))}
              </Box>
            </Collapse>
          </Field>
        ) : null}

        {goalColumn ? (
          <Field
            label="Simulated customer"
            tip="An LLM plays the customer from each row's goal, so multi-turn tasks can finish."
          >
            <Stack
              direction="row"
              alignItems="center"
              gap={1}
              sx={{ minHeight: 32 }}
            >
              <Switch
                size="small"
                checked={Boolean(h.user_sim)}
                onChange={(e) =>
                  set(
                    "user_sim",
                    e.target.checked
                      ? {
                          model: "gpt-4o-mini",
                          from_column: goalColumn,
                          max_turns: 8,
                        }
                      : null,
                  )
                }
                inputProps={{ "aria-label": "Simulated customer" }}
              />
              <Typography
                typography="s2"
                color={h.user_sim ? "text.primary" : "text.disabled"}
              >
                {`gpt-4o-mini · plays "${goalColumn}" · max ${h.user_sim?.max_turns ?? 8} customer turns`}
              </Typography>
            </Stack>
          </Field>
        ) : null}

        <Field label="Limits">
          <Stack direction="row" gap={1} flexWrap="wrap">
            <NumberField
              label="Max turns"
              value={limits.max_turns}
              onChange={(v) => setLimit("max_turns", v)}
              width={120}
            />
            <NumberField
              label="Timeout per row"
              value={limits.timeout_s}
              end="s"
              onChange={(v) => setLimit("timeout_s", v)}
            />
            <NumberField
              label="Max cost per row"
              value={limits.max_cost_usd}
              start="$"
              onChange={(v) => setLimit("max_cost_usd", v)}
            />
          </Stack>
        </Field>

        <Field
          label="Instructions file"
          tip={`${harness.file} in the sandbox's working directory, read by the harness at start.`}
        >
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            sx={{ minHeight: 32 }}
          >
            <Typography typography="s2" sx={{ fontFamily: MONO }}>
              {harness.file}
            </Typography>
            <Typography typography="s2" color="text.secondary">
              · {words.toLocaleString("en-US")} {words === 1 ? "word" : "words"}
            </Typography>
            <Button
              size="small"
              onClick={() => setShowInstructions((v) => !v)}
              sx={{ typography: "s2", px: 1 }}
            >
              {showInstructions ? "Hide" : "Edit"}
            </Button>
          </Stack>
          <Collapse in={showInstructions} unmountOnExit>
            <TextField
              multiline
              minRows={6}
              maxRows={14}
              fullWidth
              value={h.instructions ?? ""}
              onChange={(e) => set("instructions", e.target.value)}
              inputProps={{
                "aria-label": harness.file,
                style: { fontFamily: MONO, fontSize: 12, lineHeight: 1.5 },
              }}
              sx={{ bgcolor: "background.paper" }}
            />
          </Collapse>
        </Field>

        {h.secrets?.length ? (
          <Field
            label="Secrets"
            tip="Names only. Values stay in your workspace secrets and are injected into each sandbox."
          >
            <Stack
              direction="row"
              gap={0.75}
              flexWrap="wrap"
              sx={{ minHeight: 32, alignItems: "center" }}
            >
              {h.secrets.map((s) => (
                <Chip
                  key={s}
                  size="small"
                  variant="outlined"
                  icon={<Iconify icon="mdi:lock-outline" width={14} />}
                  label={`${s} ••••••`}
                  sx={{ fontFamily: MONO, typography: "s3" }}
                />
              ))}
            </Stack>
          </Field>
        ) : null}

        <Field
          label="Trials"
          tip="Set for the whole experiment on Review & run."
        >
          <Typography typography="s2" sx={{ lineHeight: "32px" }}>
            {trials}
          </Typography>
        </Field>

        <Typography typography="s3" color="text.secondary">
          {HOW_IT_RUNS}
        </Typography>

        <Stack direction="row" alignItems="center" gap={1}>
          <TryOnOneRow
            datasetId={datasetId}
            kind="harness"
            config={{ kind: "harness", harness: h }}
            compact
          />
          <Box sx={{ flex: 1 }} />
          <Button
            size="small"
            startIcon={<Iconify icon="mdi:plus" width={16} />}
            onClick={addAnother}
            sx={{ typography: "s2" }}
          >
            Another model
          </Button>
        </Stack>
      </Stack>
    </RunCardShell>
  );
};

HarnessRunCard.propTypes = {
  index: PropTypes.number,
  control: PropTypes.object,
  setValue: PropTypes.func,
  fieldPrefix: PropTypes.string,
  onRemove: PropTypes.func,
  letters: PropTypes.arrayOf(PropTypes.string),
  showErrors: PropTypes.bool,
  error: PropTypes.string,
  trials: PropTypes.number,
};

/** Stepper issues (stepIssues.js): a model, and a command for custom. */
export const issues = (item) => {
  const h = item?.harness_config ?? {};
  const out = [];
  if (!h.model) out.push("Pick a model for the harness");
  if (h.name === "Custom command…" && !String(h.command ?? "").trim())
    out.push("Enter the harness command");
  return out;
};

export default HarnessRunCard;
