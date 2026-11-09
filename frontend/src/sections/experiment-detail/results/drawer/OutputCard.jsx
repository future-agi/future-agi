import React from "react";
import CellHistory from "./CellHistory";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import RunChip from "src/sections/common/Runs/RunChip";
import { useRunWithConfig } from "../RunSourceBlock";
import { promptDoorState } from "src/sections/workbench/createPrompt/workbenchPrefill";
import ValueViewer from "src/sections/common/ValueViewer/ValueViewer";
import MediaValue from "src/sections/common/Media/MediaValue";
import { callOf, mediaRefOf } from "src/sections/common/Media/mediaRef";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import {
  formatDelta,
  formatValue,
} from "src/sections/common/Metrics/metricFormat";
import { TrialStepper } from "../cells/OutputCell";
import { notInRunLabel } from "../cells/cellCommon";
import {
  agentLinkOf,
  promptLinkOf,
} from "src/sections/common/RunSettings/targetLinks";

const MS = {
  key: "ops:latency",
  output_type: "ms",
  kind: "system",
  higher_is_better: false,
};
const USD = {
  key: "ops:cost",
  output_type: "usd",
  kind: "system",
  higher_is_better: false,
};
const COUNT = {
  key: "ops:tokens",
  output_type: "count",
  kind: "system",
  higher_is_better: false,
};

const tone = (value, base) => {
  if (!Number.isFinite(value) || !Number.isFinite(base) || base === 0)
    return "text.secondary";
  const rel = (value - base) / Math.abs(base);
  if (rel <= -0.1) return "green.600";
  if (rel >= 0.1) return "red.600";
  return "text.secondary";
};

const Metric = ({ label, def, value, base }) => (
  <Stack direction="row" alignItems="baseline" gap={0.5}>
    <Typography typography="s2" color="text.secondary">
      {label}
    </Typography>
    <Typography typography="s2" sx={{ fontVariantNumeric: "tabular-nums" }}>
      {formatValue(def, value)}
    </Typography>
    {Number.isFinite(base) && Number.isFinite(value) && value !== base && (
      <Typography
        typography="s2"
        sx={{ color: tone(value, base), fontVariantNumeric: "tabular-nums" }}
      >
        {`(${formatDelta(def, value - base, { base })})`}
      </Typography>
    )}
  </Stack>
);
Metric.propTypes = {
  label: PropTypes.string,
  def: PropTypes.object,
  value: PropTypes.number,
  base: PropTypes.number,
};

/**
 * The Agent Playground on the graph version the run ran: the one link rule
 * (RunSettings/targetLinks.agentLinkOf, M9), given the run's config. HO S8:
 * the card passes the run's R1 config (useRunWithConfig), so a Test a fix
 * child opens the version it saved, not its parent's.
 */
export function agentPlaygroundHref(target, config) {
  return agentLinkOf(target, { config });
}

/**
 * Variables an agent run sends for a row: the row's own values, plus each
 * graph variable the run maps to a column (`variable_mapping`).
 */
export function agentPrefillFor(row, run) {
  return mappedVariablesFor(
    row,
    run?.config?.variable_mapping ?? run?.variable_mapping,
  );
}

/** The row's variables, plus each variable the run maps to a column. */
function mappedVariablesFor(row, mapping) {
  const out = prefillVariablesFor(row);
  Object.entries(mapping ?? {}).forEach(([variable, column]) => {
    const value = row?.inputs?.[column] ?? row?.metadata?.[column];
    if (typeof value === "string") out[variable] = value;
  });
  return out;
}

/**
 * The router state `Open in Prompts with this row ↗` hands the workbench
 * (workbench/createPrompt/workbenchPrefill.js): the row's variables (mapped
 * through the run's `variable_mapping` when it has one) and the model and
 * params the run ran (its R1 config, else its target).
 */
export function promptPrefillState(row, run, config) {
  const cfg = config ?? run?.config ?? {};
  return promptDoorState(
    run,
    cfg,
    mappedVariablesFor(row, cfg.variable_mapping ?? run?.variable_mapping),
  );
}

/** The row's input when it has exactly one text input, else undefined. */
export function soleInputOf(row) {
  const texts = Object.values(row?.inputs ?? {}).filter(
    (v) => typeof v === "string",
  );
  return texts.length === 1 ? texts[0] : undefined;
}

/** Variables a prompt run sends: the row's inputs and string metadata. */
export function prefillVariablesFor(row) {
  const out = {};
  Object.entries(row?.metadata ?? {}).forEach(([k, v]) => {
    if (typeof v === "string") out[k] = v;
  });
  Object.entries(row?.inputs ?? {}).forEach(([k, v]) => {
    if (typeof v === "string") out[k] = v;
  });
  return out;
}

/**
 * One run's output on the open row (PRODUCT-SPEC §3.11 Compare tab): run
 * chip · `Trial ‹ 1/3 ›` · View trace; body formatted / raw or diffed;
 * footer latency · tokens · cost with deltas vs the baseline run, agent
 * steps; `Open in Prompts with this row ↗` for prompt runs.
 */

/**
 * `trial 3 of 3 · the failing one` (S7). `dots` = per-trial pass (true /
 * false) on the page's primary eval, when known. Pure.
 */
export function trialNote(k, n, dots) {
  const head = `trial ${k} of ${n}`;
  const mine = dots?.[k - 1];
  if (!Array.isArray(dots) || typeof mine !== "boolean") return head;
  const fails = dots.filter((d) => d === false).length;
  const passes = dots.filter((d) => d === true).length;
  if (mine === false && passes > 0)
    return `${head} · ${fails === 1 ? "the failing one" : "a failing one"}`;
  if (mine === true && fails > 0)
    return `${head} · ${passes === 1 ? "the passing one" : "a passing one"}`;
  return head;
}

const OutputCard = ({
  run,
  color,
  isBaseline,
  result,
  baseResult,
  trial,
  onTrialChange,
  diffFrom,
  mode,
  onViewTrace,
  row,
  focused,
  dots,
}) => {
  const navigate = useNavigate();
  // HO S8: the run's config (R1), so the agent link opens the version it ran.
  const { full: runWithConfig } = useRunWithConfig(run);
  const detail = result?.trials_detail ?? [];
  const t = detail[Math.min(trial, detail.length) - 1] ?? detail[0];
  const baseT = baseResult?.trials_detail?.[0];
  const text = t?.output ?? result?.output ?? "";
  const status = result?.status;
  const t_ = run.target ?? {};

  let body;
  if (!result || status === "missing")
    body = (
      <Typography typography="s2" color="text.disabled">
        {notInRunLabel(run)}
      </Typography>
    );
  else if (status === "queued")
    body = (
      <Typography typography="s2" color="text.disabled">
        Queued
      </Typography>
    );
  else if (t?.status === "error" || status === "error")
    body = (
      <Box
        sx={{
          bgcolor: "var(--score-red-light)",
          borderRadius: 0.5,
          px: 1,
          py: 0.75,
        }}
      >
        <Typography typography="s1" color="red.700" fontWeight={500}>
          {`Error · ${t?.error ?? result.error ?? "Run failed on this row"}`}
        </Typography>
      </Box>
    );
  // R4 lane T: media outputs (images, documents, audio, calls) render with
  // the one media renderer; text keeps the R3 views.
  else if (text && (callOf(text) || mediaRefOf(text)))
    body = <MediaValue value={text} size="card" />;
  else if (diffFrom != null && text)
    body = <TextDiff from={diffFrom} to={text} />;
  else
    body = <ValueViewer value={text} mode={mode} emptyLabel="Empty output" />;

  const steps = t?.steps ?? result?.steps;
  return (
    <Stack
      sx={{
        border: "1px solid",
        borderColor: focused ? "primary.light" : "divider",
        borderRadius: 1,
        minWidth: 0,
        flex: 1,
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          px: 1.25,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "background.neutral",
        }}
      >
        <Box sx={{ minWidth: 0, display: "flex" }}>
          <RunChip
            run={run}
            color={color}
            isBaseline={isBaseline}
            showStatus={false}
          />
        </Box>
        <Box sx={{ flex: 1 }} />
        <CellHistory trial={t} />
        {detail.length > 1 && (
          <Stack direction="row" alignItems="center" gap={0.5}>
            {/* IT-R5 G3 (P5): the stats strip names the trial (`trial 1 of
                3`); the stepper keeps its `Previous / Next trial` names. */}
            <TrialStepper
              trial={Math.min(trial, detail.length)}
              trials={detail.length}
              onChange={onTrialChange}
            />
          </Stack>
        )}
        {t?.trace_id && (
          <Button
            size="small"
            onClick={onViewTrace}
            startIcon={<Iconify icon="mdi:chart-timeline-variant" width={14} />}
            sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
          >
            View trace
          </Button>
        )}
      </Stack>
      <Box
        sx={{
          px: 1.25,
          py: 1,
          flex: 1,
          minHeight: 80,
          "& p": { typography: "s1" },
        }}
      >
        {body}
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        gap={1.5}
        sx={{
          px: 1.25,
          py: 0.75,
          borderTop: "1px solid",
          borderColor: "divider",
          flexWrap: "wrap",
          rowGap: 0.5,
        }}
      >
        {/* IT-R5 G3 (P5, S7): the strip names the trial its numbers come
            from (`trial 3 of 3 · the failing one`). */}
        {detail.length > 1 ? (
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="output-card-trial"
            sx={{ whiteSpace: "nowrap" }}
          >
            {trialNote(Math.min(trial, detail.length), detail.length, dots)}
          </Typography>
        ) : null}
        <Metric
          label="Latency"
          def={MS}
          value={t?.latency_ms}
          base={isBaseline ? undefined : baseT?.latency_ms}
        />
        <Metric
          label="Tokens"
          def={COUNT}
          value={t?.tokens}
          base={isBaseline ? undefined : baseT?.tokens}
        />
        <Metric
          label="Cost"
          def={USD}
          value={t?.cost}
          base={isBaseline ? undefined : baseT?.cost}
        />
        {steps && (
          <Typography
            typography="s2"
            color={steps.tool_errors ? "red.600" : "text.secondary"}
          >
            {`${steps.steps} steps · ${steps.tool_calls} tool calls${steps.tool_errors ? ` · ${steps.tool_errors} tool errors` : ""}`}
          </Typography>
        )}
        <Box sx={{ flex: 1 }} />
        {run.target_type === "prompt" && t_.prompt_id && (
          <Button
            size="small"
            endIcon={<Iconify icon="mdi:arrow-top-right" width={14} />}
            onClick={() =>
              navigate(
                promptLinkOf(t_, {
                  config: runWithConfig?.config ?? run?.config,
                }),
                {
                  state: promptPrefillState(
                    row,
                    run,
                    runWithConfig?.config ?? run?.config,
                  ),
                },
              )
            }
            sx={{ typography: "s2" }}
          >
            Open in Prompts with this row
          </Button>
        )}
        {run.target_type === "agent" && t_.graph_id && (
          <Button
            size="small"
            endIcon={<Iconify icon="mdi:arrow-top-right" width={14} />}
            onClick={() =>
              navigate(
                agentPlaygroundHref(t_, runWithConfig?.config ?? run?.config),
                {
                  state: {
                    prefillVariables: agentPrefillFor(row, run),
                    prefillInput: soleInputOf(row),
                  },
                },
              )
            }
            sx={{ typography: "s2" }}
          >
            Open in Agent Playground
          </Button>
        )}
      </Stack>
    </Stack>
  );
};

OutputCard.propTypes = {
  run: PropTypes.object.isRequired,
  color: PropTypes.object,
  isBaseline: PropTypes.bool,
  result: PropTypes.object,
  baseResult: PropTypes.object,
  trial: PropTypes.number,
  dots: PropTypes.arrayOf(PropTypes.bool),
  onTrialChange: PropTypes.func,
  diffFrom: PropTypes.string,
  mode: PropTypes.string,
  onViewTrace: PropTypes.func,
  row: PropTypes.object,
  focused: PropTypes.bool,
};

export default OutputCard;
