// options-truth L1 G6: parts of Rerun with changes. `WherePicker` (the
// current place, a connected machine, the Future AGI sandbox, CI; offline
// machines greyed with their true reason and a one-click switch to the
// sandbox), `PromptChanges` (model, temperature, max tokens of a prompt or
// agent run, which declares no parameters) and `WaitingPanel` (a created
// rerun waiting for the CI job, your terminal or the machine owner, with
// its command and the S2 stand-in).
import React from "react";
import { runModelOf } from "./rerunWhere";
import PropTypes from "prop-types";
import {
  STAND_IN_EVENTS,
  standInTip,
} from "src/sections/common/Connect/connectCopy";
import {
  Box,
  Button,
  Chip,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import CopyButton from "src/sections/common/ux/CopyButton";
import { VOCAB } from "src/sections/common/vocab";
import RunConnectionStrip from "src/sections/common/Connect/RunConnectionStrip";
import { useRunActivity } from "src/api/develop/r4";
import { DECLARED_MODELS } from "./declaredUses";
import { serveBlockOf } from "./rerunWhere";
import {
  HTTP_METHODS,
  SIDE_EFFECTS,
  a2aSkillOptions,
  sourceFieldsOf,
  sourceKindOfConfig,
} from "./sourceChangesModel";
import { mediaModelsOf, mediaTypeOfConfig } from "./mediaChangesModel";

/** `2 h` / `3 d` / `5 min` since an ISO time (pure). */
export function sinceText(iso, now = Date.now()) {
  const t = Date.parse(iso ?? "");
  if (!Number.isFinite(t)) return null;
  const min = Math.max(1, Math.round((now - t) / 60000));
  if (min < 60) return `${min} min`;
  const h = Math.round(min / 60);
  return h < 48 ? `${h} h` : `${Math.round(h / 24)} d`;
}

/** Why a machine can't take the run now, or null (pure). */
export function machineBlock(m, now = Date.now()) {
  if (!m) return null;
  if (m.status && m.status !== "online") {
    const since = sinceText(m.last_seen_at, now);
    return `${m.name} is offline${since ? ` since ${since}` : ""}.`;
  }
  if (m.may_use === false)
    return `Only ${m.owner?.name ?? "its owner"} can run on ${m.name}.`;
  return null;
}

/**
 * The Where options of a rerun (pure): `[{value, label, block}]`. A machine
 * is open only when one of its agents serves this run (L3 G9 fix 4); the
 * others stay listed, disabled with the reason
 * (`voice-worker@acme-gpu-01 serves acme-voice-v2, not CrewAI support crew`).
 */
export function whereOptions(
  config = {},
  machines = [],
  now = Date.now(),
  run = null,
) {
  const ex = config.execution ?? {};
  const here = ex.label ?? (ex.mode === "ci" ? "CI" : "Future AGI");
  const out = [{ value: "same", label: `Where it ran: ${here}`, block: null }];
  const self = run ?? { config };
  machines.forEach((m) => {
    if (m.id === ex.machine_id) return;
    out.push({
      value: `local:${m.id}`,
      label: `This machine · ${m.name}`,
      block: serveBlockOf(m, self) ?? machineBlock(m, now),
    });
  });
  if (ex.mode !== "sandbox")
    out.push({ value: "sandbox", label: "Future AGI sandbox", block: null });
  if (ex.mode !== "ci")
    out.push({ value: "ci", label: "CI · fi-experiment.yml", block: null });
  return out;
}

/** The POST `execution` of a Where value (pure). */
export function executionOfPick(value) {
  if (!value || value === "same") return { mode: "same" };
  if (value.startsWith("local:"))
    return { mode: "local", machine_id: value.slice(6) };
  return { mode: value };
}

export function WherePicker({ value, options, onChange, currentBlock }) {
  const picked = options.find((o) => o.value === value);
  const block = value === "same" ? currentBlock : picked?.block;
  return (
    <Stack spacing={0.75} data-testid="rerun-where">
      <Stack direction="row" alignItems="center" gap={1}>
        <Iconify
          icon="mdi:map-marker-outline"
          width={16}
          sx={{ color: "text.secondary" }}
        />
        <Typography typography="s2" color="text.secondary">
          Where it runs
        </Typography>
        <TextField
          select
          size="small"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          inputProps={{ "aria-label": "Where it runs" }}
          sx={{ minWidth: 260 }}
        >
          {options.map((o) => (
            <MenuItem
              key={o.value}
              value={o.value}
              disabled={Boolean(o.block)}
              title={o.block ?? undefined}
            >
              <Stack>
                <Typography typography="s2">{o.label}</Typography>
                {o.block ? (
                  <Typography typography="s3" color="text.secondary">
                    {o.block}
                  </Typography>
                ) : null}
              </Stack>
            </MenuItem>
          ))}
        </TextField>
      </Stack>
      {block ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          flexWrap="wrap"
          data-testid="rerun-where-blocked"
        >
          <Typography typography="s2" color="warning.dark">
            {block}
          </Typography>
          <Button size="small" onClick={() => onChange("sandbox")}>
            Run it in the Future AGI sandbox
          </Button>
        </Stack>
      ) : null}
    </Stack>
  );
}

WherePicker.propTypes = {
  value: PropTypes.string.isRequired,
  options: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  currentBlock: PropTypes.string,
};

/** Prompt / agent runs: model, temperature, max tokens (the dock's knobs). */
export { runModelOf };

/**
 * L7b G3: the fields of an endpoint / A2A / MCP run Rerun with changes can
 * change (sourceChangesModel): HTTP URL, method, body and output path; the
 * A2A skill; the MCP tool's side effects.
 */
export function SourceChanges({ config, value, onChange }) {
  const kind = sourceKindOfConfig(config);
  const base = sourceFieldsOf(config);
  if (!kind || !base) return null;
  const v = { ...base, ...value };
  const set = (field) => (e) => onChange({ ...value, [field]: e.target.value });
  if (kind === "http")
    return (
      <Stack spacing={1} data-testid="rerun-source-changes">
        <Stack direction="row" gap={1.5} flexWrap="wrap">
          <TextField
            select
            size="small"
            label="Method"
            value={v.method}
            onChange={set("method")}
            sx={{ width: 110 }}
          >
            {HTTP_METHODS.map((m) => (
              <MenuItem key={m} value={m}>
                {m}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            size="small"
            label="URL"
            value={v.url}
            onChange={set("url")}
            sx={{ flex: 1, minWidth: 260 }}
          />
          <TextField
            size="small"
            label="Output path"
            value={v.output_path}
            onChange={set("output_path")}
            sx={{ width: 160 }}
          />
        </Stack>
        <TextField
          size="small"
          label="Body"
          value={v.body_template}
          onChange={set("body_template")}
          multiline
          minRows={2}
          inputProps={{ style: { fontFamily: "monospace", fontSize: 12 } }}
        />
      </Stack>
    );
  if (kind === "a2a") {
    const skills = a2aSkillOptions(config);
    return (
      <Stack spacing={1} data-testid="rerun-source-changes">
        <TextField
          select
          size="small"
          label="Skill"
          value={v.skill_id}
          onChange={set("skill_id")}
          sx={{ minWidth: 240, alignSelf: "flex-start" }}
        >
          {skills.map((s) => (
            <MenuItem key={s.value} value={s.value}>
              {s.label}
            </MenuItem>
          ))}
        </TextField>
      </Stack>
    );
  }
  return (
    <Stack spacing={1} data-testid="rerun-source-changes">
      <TextField
        select
        size="small"
        label="Side effects"
        value={v.side_effects}
        onChange={set("side_effects")}
        sx={{ minWidth: 240, alignSelf: "flex-start" }}
      >
        {SIDE_EFFECTS.map((s) => (
          <MenuItem key={s.value} value={s.value}>
            {s.label}
          </MenuItem>
        ))}
      </TextField>
    </Stack>
  );
}

/**
 * L7b G3: a TTS / STT / image rerun changes only what that API takes: the
 * model, and the voice for TTS (mediaChangesModel).
 */
export function MediaChanges({ config, value, onChange }) {
  const type = mediaTypeOfConfig(config);
  if (!type) return null;
  const m = config.media ?? {};
  const models = mediaModelsOf(type, m.model);
  return (
    <Stack spacing={1} data-testid="rerun-media-changes">
      <Stack direction="row" gap={1.5} flexWrap="wrap">
        <TextField
          select
          size="small"
          label="Model"
          value={value.model ?? m.model ?? ""}
          onChange={(e) => onChange({ ...value, model: e.target.value })}
          sx={{ minWidth: 220 }}
        >
          {models.map((x) => (
            <MenuItem key={x} value={x}>
              {x}
            </MenuItem>
          ))}
        </TextField>
        {type === "tts" ? (
          <TextField
            size="small"
            label="Voice"
            value={value.voice ?? m.voice ?? ""}
            onChange={(e) => onChange({ ...value, voice: e.target.value })}
            sx={{ width: 160 }}
          />
        ) : null}
      </Stack>
    </Stack>
  );
}

MediaChanges.propTypes = {
  config: PropTypes.object,
  value: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
};

SourceChanges.propTypes = {
  config: PropTypes.object,
  value: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
};

export function PromptChanges({ config, value, onChange }) {
  const model = { ...(config?.model ?? {}), id: runModelOf(config) };
  const version = config?.prompt?.version ?? config?.agent?.version ?? null;
  const models = [...new Set([model.id, ...DECLARED_MODELS].filter(Boolean))];
  return (
    <Stack spacing={1} data-testid="rerun-prompt-changes">
      <Typography typography="s2" color="text.secondary">
        {`${config?.prompt ? "Prompt" : "Agent"} ${version ?? ""}`.trim()}
        {" · the same version runs with these settings"}
      </Typography>
      <Stack direction="row" gap={1.5} flexWrap="wrap">
        <TextField
          select
          size="small"
          label="Model"
          value={value.model ?? model.id ?? ""}
          onChange={(e) => onChange({ ...value, model: e.target.value })}
          sx={{ minWidth: 200 }}
        >
          {models.map((m) => (
            <MenuItem key={m} value={m}>
              {m}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          size="small"
          type="number"
          label="Temperature"
          value={value.temperature ?? model.params?.temperature ?? ""}
          onChange={(e) => onChange({ ...value, temperature: e.target.value })}
          // An agent whose nodes set no temperature runs at the model's
          // default: say so instead of an empty box.
          placeholder={
            model.params?.temperature == null ? "default" : undefined
          }
          InputLabelProps={{ shrink: true }}
          inputProps={{ min: 0, max: 2, step: 0.1 }}
          sx={{ width: 120 }}
        />
        <TextField
          size="small"
          type="number"
          label="Max tokens"
          value={value.maxTokens ?? model.params?.max_tokens ?? ""}
          onChange={(e) => onChange({ ...value, maxTokens: e.target.value })}
          placeholder={model.params?.max_tokens == null ? "default" : undefined}
          InputLabelProps={{ shrink: true }}
          inputProps={{ min: 1, step: 50 }}
          sx={{ width: 120 }}
        />
      </Stack>
    </Stack>
  );
}

PromptChanges.propTypes = {
  config: PropTypes.object,
  value: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
};

/** The I1 changes of PromptChanges vs the parent's config (pure). */
export function promptChangesOf(config = {}, v = {}) {
  const out = [];
  const m = config.model ?? {};
  if (v.model && v.model !== runModelOf(config))
    out.push({ axis: "model", field: "id", value: v.model });
  const t = v.temperature;
  if (t !== undefined && t !== "" && Number(t) !== m.params?.temperature)
    out.push({ axis: "params", field: "temperature", value: Number(t) });
  const k = v.maxTokens;
  if (k !== undefined && k !== "" && Number(k) !== m.params?.max_tokens)
    out.push({ axis: "params", field: "max_tokens", value: Number(k) });
  return out;
}

const BUDGET_FIELDS = [
  {
    key: "max_turns",
    label: "Max turns",
    end: "turns",
    min: 1,
    max: 200,
    step: 1,
  },
  {
    key: "max_cost_usd",
    label: "Max cost per try",
    end: "$",
    min: 0.01,
    max: 50,
    step: 0.01,
  },
  {
    key: "timeout_s",
    label: "Time limit",
    end: "s",
    min: 10,
    max: 7200,
    step: 10,
  },
];

/** A harness's per-try limits: `{max_turns, max_cost_usd, timeout_s}` (pure). */
export function budgetOf(harness = {}) {
  const b = harness?.budget ?? {};
  return {
    max_turns: b.max_turns ?? harness?.max_turns ?? null,
    max_cost_usd: b.max_cost_usd ?? harness?.max_cost_usd ?? null,
    timeout_s: b.timeout_s ?? harness?.timeout_s ?? null,
  };
}

/** `Max turns 40 → 12 · Time limit 600 s → 120 s` of the edited limits (pure). */
export function budgetChangeLine(before = {}, after = {}) {
  const unit = { max_turns: "", max_cost_usd: "$", timeout_s: " s" };
  return BUDGET_FIELDS.filter(
    (f) =>
      after[f.key] != null &&
      after[f.key] !== "" &&
      Number(after[f.key]) !== Number(before[f.key]),
  )
    .map((f) => {
      const fmt = (v) =>
        v == null
          ? "none"
          : f.key === "max_cost_usd"
            ? `$${v}`
            : `${v}${unit[f.key]}`;
      return `${f.label} ${fmt(before[f.key])} → ${fmt(Number(after[f.key]))}`;
    })
    .join(" · ");
}

/** The harness recipe's limits in the Rerun dialog (each try stops at the first). */
export function RecipeBudget({ before, value, onChange }) {
  const changed = budgetChangeLine(before, value);
  return (
    <Stack spacing={0.5} data-testid="rerun-recipe-budget">
      <Stack direction="row" gap={1.5} flexWrap="wrap" alignItems="center">
        {BUDGET_FIELDS.map((f) => (
          <TextField
            key={f.key}
            size="small"
            type="number"
            label={f.label}
            value={value[f.key] ?? ""}
            onChange={(e) =>
              onChange({
                ...value,
                [f.key]: e.target.value === "" ? null : Number(e.target.value),
              })
            }
            InputProps={{
              endAdornment: (
                <Typography typography="s3" color="text.secondary" noWrap>
                  {f.end}
                </Typography>
              ),
            }}
            inputProps={{
              min: f.min,
              max: f.max,
              step: f.step,
              "aria-label": f.label,
              "data-testid": `rerun-budget-${f.key}`,
            }}
            sx={{ width: 168 }}
          />
        ))}
      </Stack>
      <Typography typography="s3" color="text.secondary">
        {changed
          ? `Per try: ${changed}. A try stops at the first limit it hits.`
          : "Per try. A try stops at the first limit it hits."}
      </Typography>
    </Stack>
  );
}

RecipeBudget.propTypes = {
  before: PropTypes.object.isRequired,
  value: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
};

/** S2 stand-in: `Simulate <event>` + the `Simulated` tag. */
export function SimulateButton({ event, label, onClick, loading }) {
  return (
    <Stack direction="row" alignItems="center" gap={1}>
      <Button
        size="small"
        variant="outlined"
        onClick={onClick}
        disabled={loading}
        data-testid="rerun-simulate"
      >
        {label}
      </Button>
      <CustomTooltip show title={standInTip(event)} size="small">
        <Chip size="small" label={VOCAB.SIMULATED ?? "Simulated"} />
      </CustomTooltip>
    </Stack>
  );
}

SimulateButton.propTypes = {
  event: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  onClick: PropTypes.func.isRequired,
  loading: PropTypes.bool,
};

const WAIT = {
  waiting_ci: {
    event: STAND_IN_EVENTS.ci_job,
    label: "Simulate the CI job",
    lead: "Push or re-run the workflow with this command; the run fills as results upload.",
  },
  waiting_terminal: {
    event: STAND_IN_EVENTS.terminal,
    label: "Simulate your terminal",
    lead: "Run this in your terminal; the run fills as results upload.",
  },
};

/**
 * The heading of a rerun held for its machine owner, from the run's live
 * state (L3 G9 fix 1, pure): `#3H · Waiting for Alice Park to confirm on
 * alice@mbp-14` while held, `#3H · Running on alice@mbp-14` once they
 * confirm, `#3H · Finished on alice@mbp-14` when its tries are in.
 * `{title, lead, settled}`; `settled` = the run left the hold (show Open
 * compare).
 */
export function pendingHeadingOf(created, activity = null) {
  if (!created) return null;
  const state = activity?.state ?? created.state;
  const where =
    activity?.where ?? activity?.machine?.name ?? created.machine_name ?? null;
  const on = where ? ` on ${where}` : "";
  const label = created.label;
  if (!state || state === "waiting_local_confirm")
    return {
      title: `${label} · ${created.detail}`,
      lead: "The recipe widens what the agent may do on that machine, so its owner confirms first. It starts as soon as they do.",
      settled: false,
    };
  const left = activity?.left ?? null;
  const done = activity?.done ?? null;
  const TITLE = {
    running: `Running${on}`,
    scoring: `Scoring${on}`,
    done: `Finished${on}`,
    stopped: `Stopped${on}`,
    cancelled: `Cancelled${on}`,
    offline: `${where ?? "The machine"} is offline`,
  };
  const lead =
    state === "done"
      ? "Its owner confirmed and every try is in. Compare it with the run it came from."
      : state === "cancelled"
        ? "The rerun was cancelled before it started."
        : done != null && left != null
          ? `Its owner confirmed. ${fmtTries(done)} in, ${fmtTries(left)} to go.`
          : "Its owner confirmed; the tries fill as they finish.";
  return {
    title: `${label} · ${TITLE[state] ?? `Running${on}`}`,
    lead,
    settled: state !== "cancelled",
  };
}

const fmtTries = (n) => `${n} ${n === 1 ? "try" : "tries"}`;

// The strip's watch switch block (its Stack holding the Switch).
const NO_WATCH_SX = {
  "& .MuiStack-root:has(> .MuiSwitch-root)": { display: "none" },
};

/** A rerun held for the machine owner, following its live state. */
function PendingPanel({ created, onOpenCompare }) {
  const q = useRunActivity(created.run_id, {
    poll: 1000,
    enabled: Boolean(created.run_id),
  });
  const h = pendingHeadingOf(created, q.data ?? null);
  return (
    <Stack spacing={1} data-testid="rerun-pending">
      <Stack direction="row" alignItems="center" gap={1}>
        <Typography
          typography="s1"
          fontWeight="fontWeightSemiBold"
          sx={{ flex: 1, minWidth: 0 }}
          data-testid="rerun-pending-title"
        >
          {h.title}
        </Typography>
        {h.settled && onOpenCompare ? (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={onOpenCompare}
            data-testid="rerun-open-compare"
          >
            Open compare
          </Button>
        ) : null}
      </Stack>
      <Typography typography="s2" color="text.secondary">
        {h.lead}
      </Typography>
      <RunConnectionStrip
        run={{
          run_id: created.run_id,
          label: created.label,
          config: {
            execution: {
              mode: "local",
              machine_id: created.machine_id,
            },
          },
        }}
        // L3 G9 (fix 1): a run held for its owner is a harness recipe
        // change; watch mode reloads a code entry on file edits, which a
        // harness rerun has none of, so the strip shows no watch switch.
        sx={NO_WATCH_SX}
      />
    </Stack>
  );
}

PendingPanel.propTypes = {
  created: PropTypes.object.isRequired,
  onOpenCompare: PropTypes.func,
};

/** A created rerun that waits: its line, its command, the stand-in. */
export function WaitingPanel({
  created,
  command,
  onSimulate,
  simulating,
  onOpenCompare,
}) {
  if (!created) return null;
  if (created.state === "waiting_local_confirm")
    return <PendingPanel created={created} onOpenCompare={onOpenCompare} />;
  const w = WAIT[created.state];
  if (!w) return null;
  return (
    <Stack spacing={1} data-testid="rerun-waiting">
      <Typography typography="s1" fontWeight="fontWeightSemiBold">
        {`${created.label} · ${created.detail}`}
      </Typography>
      <Typography typography="s2" color="text.secondary">
        {w.lead}
      </Typography>
      {command ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          data-testid="rerun-command"
          sx={{
            px: 1.25,
            py: 0.75,
            borderRadius: 1,
            bgcolor: "background.neutral",
            fontFamily: "monospace",
            typography: "s2",
            minWidth: 0,
          }}
        >
          <Box
            component="code"
            sx={{
              flex: 1,
              minWidth: 0,
              overflowX: "auto",
              whiteSpace: "nowrap",
            }}
          >
            {command}
          </Box>
          <CopyButton
            value={command}
            tooltip="Copy the command"
            toast="Command copied"
          />
        </Stack>
      ) : null}
      <SimulateButton
        event={w.event}
        label={w.label}
        onClick={onSimulate}
        loading={simulating}
      />
    </Stack>
  );
}

WaitingPanel.propTypes = {
  created: PropTypes.object,
  command: PropTypes.string,
  onSimulate: PropTypes.func,
  simulating: PropTypes.bool,
  onOpenCompare: PropTypes.func,
};
