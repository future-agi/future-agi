// Level 3 · section 1 for an Agent harness (R4 lane Rb MUST 1; options-truth
// L1 G3 A/B, C4). Slot created by lane Ra (frozen props, I1b).
//
// Shows first: harness + version, model (+ `Model for helper calls` where
// the harness has one), MCP servers (the task suite's Environment first,
// `+ Add MCP server`; an info line for harnesses without MCP), permissions
// (how the level is enforced under the toggle), what each try records,
// then the two ways to run it (`Run on your machine` | `Live connection`).
// `More settings` is one collapsed section (version, built-in tools,
// instructions, budget, collect, command preview). Sandbox and CI modes swap
// the paths for their config. Every change reaches the draft as the C4
// run config (`slot.harness` = harnessConfigOf(recipe)); sandbox and CI
// settings land in `slot.harness.sandbox` and `slot.ci`.
// `compact` (Ra's Rerun dialog): the editable fields and what changed.
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  Checkbox,
  Chip,
  FormControlLabel,
  MenuItem,
  Select,
  Stack,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useSearchParams } from "react-router-dom";
import { catalogEntry } from "./catalog";
import { AmberLine } from "./parts";
import SandboxConfig from "./SandboxConfig";
import CiConfig from "./CiConfig";
import SecretsRow from "./SecretsRow";
import RecipeRow, { segmentedSx } from "./recipe/RecipeRow";
import McpServers from "./recipe/McpServers";
import MoreSettings from "./recipe/MoreSettings";
import HarnessPaths from "./recipe/HarnessPaths";
import useScriptIds from "./recipe/useScriptIds";
import {
  API_KEY_NOTE,
  CUSTOM_COMMAND_HELP,
  CUSTOM_TRACE_FILE,
  MCP_CONFIG_WORDS,
  RECORD_SENTENCES,
  RECORD_WORDS,
  recipeSummary,
  PERMISSION_LABELS,
  PERMISSION_ORDER,
  changedFields,
  defaultRecipe,
  harnessConfigOf,
  modelOptions,
  noMcpLine,
  permissionFlag,
  permissionHint,
  permissionTooltip,
  profileOf,
  recipeFromConfig,
  recordKeyOf,
  wideningNotice,
  wideningReasonText,
  withEnvironmentMcp,
} from "./recipe/recipeModel";

/** What each try records: the chip and its sentence read one key. */
function CaptureChip({ recordKey }) {
  const c = {
    key: recordKey,
    label: RECORD_WORDS[recordKey],
    detail: RECORD_SENTENCES[recordKey],
  };
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      flexWrap="wrap"
      sx={{ minWidth: 0 }}
    >
      <CustomTooltip show title={c.detail} size="small" placement="top">
        <Chip
          size="small"
          variant="outlined"
          color={c.key === "transcript" ? "primary" : "default"}
          icon={
            <Iconify
              icon={
                c.key === "transcript"
                  ? "mdi:format-list-checks"
                  : "mdi:swap-horizontal"
              }
              width={14}
            />
          }
          label={c.label}
          aria-label={`What we record: ${c.label}`}
          data-testid="capture-chip"
          data-record={c.key}
          sx={{ typography: "s2" }}
        />
      </CustomTooltip>
    </Stack>
  );
}
CaptureChip.propTypes = { recordKey: PropTypes.string.isRequired };

export default function HarnessRecipe({
  entry: entryProp,
  mode,
  value,
  onChange,
  dataset,
  ids = {},
  compact,
  limits,
  confirmMachine,
}) {
  const entry = entryProp ?? catalogEntry("claude_code");
  const environment = dataset?.environment ?? null;
  const base = useMemo(
    () =>
      compact
        ? recipeFromConfig(
            value && Object.keys(value).length ? value : null,
            entry,
          )
        : defaultRecipe(entry, environment),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [entry?.id, compact, environment?.image],
  );
  const [params] = useSearchParams();
  const urlAgent = params.get("agent");
  // A draft that already carries this harness's recipe reopens with it.
  // L3 G6: the Environment's MCP server is merged in even when the
  // environment loads after an edit (an edit never drops acme-support).
  const initial = useMemo(
    () => {
      const own =
        !compact &&
        value &&
        Object.keys(value).length &&
        (value.id ?? value.harness) === entry.id
          ? recipeFromConfig(value, entry)
          : base;
      return compact ? own : withEnvironmentMcp(own, environment);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [base, environment?.mcp?.name],
  );
  const [recipe, setRecipe] = useState(initial);
  // `form=connect`, or a saved worker picked in the catalog (`agent=`),
  // opens the Live connection path.
  const [form, setFormState] = useState(() =>
    params.get("form") === "connect" || (urlAgent && mode === "local")
      ? "connect"
      : "upload",
  );
  // C3/C4: where-it-runs settings and the live connection ride on the slot.
  const [sandbox, setSandbox] = useState(value?.sandbox ?? null);
  // CI's trigger and gate (C3 `ci`): kept here so the editor shows them and
  // the slot carries them flat (`slot.ci.trigger`, `slot.ci.threshold`).
  const [ci, setCi] = useState(value?.ci ?? null);
  const [connection, setConnection] = useState(null);
  const emit = (next, extra = {}) => {
    const conn = "connection" in extra ? extra.connection : connection;
    const sb = "sandbox" in extra ? extra.sandbox : sandbox;
    const live = (extra.form ?? form) === "connect" && mode === "local";
    onChange?.({
      harness: harnessConfigOf(next, entry, {
        mode,
        form: extra.form ?? form,
        sandbox: mode === "sandbox" ? sb : null,
        connection: live ? conn : null,
      }),
      execution:
        live && conn
          ? {
              mode: "connect",
              machine_id: conn.machine_id,
              agent_id: conn.agent_id,
              label: conn.machine_name,
            }
          : null,
    });
  };
  useEffect(() => {
    setRecipe(initial);
    // IT-R1 G1 P6/P8: the draft always carries the recipe it shows (card
    // summary, Review caps and Start it read it), not only after an edit.
    if (!compact) emit(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial]);
  const update = (next) => {
    setRecipe(next);
    if (!compact) emit(next);
    else onChange?.({ harness: next });
  };
  // L3 G6: one time limit. The Advanced row writes slot.harness.timeout_s;
  // the recipe (More settings, the script's TIMEOUT_S, every command) takes it.
  const slotTimeout = Number(value?.timeout_s) || null;
  useEffect(() => {
    if (compact || !slotTimeout) return;
    if (Number(recipe.budget?.timeout_s) === slotTimeout) return;
    update({ ...recipe, budget: { ...recipe.budget, timeout_s: slotTimeout } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slotTimeout]);
  const setForm = (f) => {
    setFormState(f);
    if (!compact) emit(recipe, { form: f });
  };
  const p = profileOf(entry);
  // options-truth L7b G4 (mock:connect-endpoints): connecting a machine is
  // absent, so the recipe reads no connected workers (no GET
  // connect/machines/ from the New experiment drawer).
  const worker = null;
  // A saved worker picked in the catalog (`agent=`) lands connected.
  const savedWorker =
    urlAgent && worker?.agent?.id === urlAgent ? worker : null;
  const onConnected = (agent, machine) => {
    const conn = agent
      ? {
          agent_id: agent.id,
          machine_id: machine?.id ?? agent.machine_id ?? null,
          machine_name: machine?.name ?? null,
          agent_name: agent.name ?? null,
        }
      : null;
    setConnection(conn);
    emit(recipe, { connection: conn });
  };
  // L2 G9: in the Rerun dialog the confirm applies only on the machine the
  // rerun runs on (the dialog's pick), never on a guessed worker.
  // L3 G6: only a slot bound to that machine (Live connection) asks its
  // owner to confirm; an upload, CI or sandbox run neither shows nor waits.
  const boundMachine =
    mode === "local" && form === "connect" ? worker?.machine ?? null : null;
  const notice = wideningNotice(
    recipe,
    base,
    compact ? confirmMachine ?? null : boundMachine,
  );
  const recordKey = recordKeyOf(entry, { mode, form });
  const scriptIds = useScriptIds(
    dataset
      ? { ...dataset, inputColumn: dataset.inputColumn ?? "instruction" }
      : null,
    ids,
    { enabled: !compact },
  );
  const flag = permissionFlag(entry.id, recipe.permissions);
  // The recipe's model and helper: the one key row in CI and sandbox mode.
  const keyModels = [
    recipe.model,
    p.subCall ? recipe.subCallModel : null,
  ].filter(Boolean);

  const modelRow = (
    <RecipeRow
      label="Model"
      hint="Every model call goes through the Future AGI gateway"
    >
      <Select
        size="small"
        value={recipe.model}
        onChange={(e) => update({ ...recipe, model: e.target.value })}
        sx={{ height: 30, typography: "s2", minWidth: 180 }}
        inputProps={{ "aria-label": "Model", "data-testid": "harness-model" }}
      >
        {modelOptions(recipe, entry).map((m) => (
          <MenuItem key={m} value={m} sx={{ typography: "s2" }}>
            {m}
          </MenuItem>
        ))}
      </Select>
      {p.subCall ? (
        recipe.subCallModel ? (
          <Stack direction="row" alignItems="center" gap={0.5}>
            <CustomTooltip
              show
              title={`Sets ${p.subCall.env}=${recipe.subCallModel} for each try`}
              size="small"
              placement="top"
            >
              <Typography typography="s2" color="text.secondary">
                Helper calls
              </Typography>
            </CustomTooltip>
            <Select
              size="small"
              value={recipe.subCallModel}
              onChange={(e) =>
                update({ ...recipe, subCallModel: e.target.value })
              }
              sx={{ height: 30, typography: "s2", minWidth: 160 }}
              inputProps={{
                "aria-label": "Model for helper calls",
                "data-testid": "subcall-model",
              }}
            >
              {p.models.map((m) => (
                <MenuItem key={m} value={m} sx={{ typography: "s2" }}>
                  {m}
                </MenuItem>
              ))}
            </Select>
            <Button
              size="small"
              onClick={() => update({ ...recipe, subCallModel: null })}
              sx={{ minWidth: 0, px: 0.5, typography: "s3" }}
              aria-label="Remove the helper model"
            >
              <Iconify icon="mdi:close" width={14} />
            </Button>
          </Stack>
        ) : (
          <Button
            size="small"
            startIcon={<Iconify icon="mdi:plus" width={14} />}
            onClick={() => update({ ...recipe, subCallModel: p.subCall.model })}
            sx={{ typography: "s2", minWidth: 0, px: 0.75 }}
            data-testid="add-subcall"
          >
            Model for helper calls
          </Button>
        )
      ) : null}
    </RecipeRow>
  );

  const permissionsRow = (
    <RecipeRow
      label="Permissions"
      hint="What the harness may do in its folder without asking"
    >
      <ToggleButtonGroup
        size="small"
        exclusive
        value={recipe.permissions}
        onChange={(_, v) => v && update({ ...recipe, permissions: v })}
        aria-label="Permissions"
        sx={segmentedSx}
      >
        {PERMISSION_ORDER.map((id) => (
          <ToggleButton key={id} value={id} data-testid={`perm-${id}`}>
            <CustomTooltip
              show
              title={permissionTooltip(entry.id, id)}
              size="small"
              placement="top"
            >
              <span>{PERMISSION_LABELS[id]}</span>
            </CustomTooltip>
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
      <Typography
        typography="s3"
        color="text.secondary"
        noWrap
        data-testid="perm-flag"
        sx={{ fontFamily: "monospace", minWidth: 0 }}
      >
        {flag.flag}
      </Typography>
      <Typography
        typography="s3"
        color="text.secondary"
        data-testid="perm-hint"
        sx={{ flexBasis: "100%" }}
      >
        {permissionHint(entry.id, recipe.permissions)}
      </Typography>
    </RecipeRow>
  );

  const mcpRow = p.mcp ? (
    <RecipeRow
      label="MCP servers"
      hint={`Model Context Protocol servers the harness may call · ${MCP_CONFIG_WORDS[p.mcp]}`}
    >
      <McpServers
        servers={recipe.mcp_servers}
        onChange={(s) => update({ ...recipe, mcp_servers: s })}
      />
    </RecipeRow>
  ) : (
    <RecipeRow label="MCP servers" hint="This harness reads no MCP servers">
      <Typography
        typography="s2"
        color="text.secondary"
        data-testid="no-mcp-line"
      >
        {noMcpLine(entry)}
      </Typography>
    </RecipeRow>
  );

  // L3 G6 (R2 card): task suites play a simulated customer from each
  // task's goal; off → no customer turns (config.harness.user_sim = null).
  const userSimOn = !("user_sim" in recipe) || Boolean(recipe.user_sim);
  const userSimRow = environment ? (
    <RecipeRow
      label="Simulated customer"
      hint="An LLM plays the customer from each task's goal, so multi-turn tasks can finish"
    >
      <Switch
        size="small"
        checked={userSimOn}
        onChange={(e) =>
          update({
            ...recipe,
            user_sim: e.target.checked
              ? { model: "gpt-4o-mini", max_turns: 8 }
              : null,
          })
        }
        inputProps={{
          "aria-label": "Simulated customer",
          "data-testid": "harness-user-sim",
        }}
      />
      <Typography
        typography="s2"
        color={userSimOn ? "text.primary" : "text.disabled"}
      >
        {userSimOn
          ? `${recipe.user_sim?.model ?? "gpt-4o-mini"} plays the customer · up to ${recipe.user_sim?.max_turns ?? 8} customer turns`
          : "Off: the harness gets the task alone, no customer replies"}
      </Typography>
    </RecipeRow>
  ) : null;

  const widening = notice.line ? (
    <AmberLine icon="mdi:shield-account-outline">
      <span data-testid="widening-notice">{notice.line}</span>
      {` ${wideningReasonText(notice.reasons)}.`}
    </AmberLine>
  ) : null;

  if (compact) {
    const changes = changedFields(recipe, base);
    return (
      <Stack spacing={0.75} data-testid="harness-recipe" data-compact="1">
        {modelRow}
        {permissionsRow}
        {mcpRow}
        {userSimRow}
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="recipe-changes"
        >
          {changes.length
            ? `Changed: ${changes.map((c) => `${c.field} ${c.from} → ${c.to}`).join(" · ")}`
            : "No recipe changes yet"}
        </Typography>
        {widening}
      </Stack>
    );
  }

  return (
    <Stack spacing={0.75} data-testid="harness-recipe" data-entry={entry.id}>
      {/* IT-R1 G1 P7: what this run is, always in view while you scroll. */}
      <Typography
        typography="s2"
        fontWeight="fontWeightMedium"
        data-testid="recipe-summary"
        sx={{
          position: "sticky",
          top: 0,
          zIndex: 2,
          bgcolor: "background.paper",
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          mb: 0.5,
        }}
      >
        {recipeSummary(recipe)}
      </Typography>
      <RecipeRow
        label="Harness"
        hint="The CLI agent and the version your machine runs (change the version in More settings)"
      >
        <Typography
          typography="s2"
          fontWeight="fontWeightMedium"
          noWrap
          data-testid="harness-line"
        >
          {recipe.version ? `${entry.name} · ${recipe.version}` : entry.name}
        </Typography>
      </RecipeRow>
      {entry.id === "custom_command" ? (
        <RecipeRow label="Command" hint={CUSTOM_COMMAND_HELP}>
          <TextField
            size="small"
            value={recipe.command ?? ""}
            onChange={(e) => update({ ...recipe, command: e.target.value })}
            placeholder="your-agent --task {task}"
            helperText={`${CUSTOM_COMMAND_HELP} · {task} is replaced with the row's task`}
            inputProps={{
              "aria-label": "Command",
              style: { fontFamily: "monospace" },
            }}
            sx={{
              flex: 1,
              minWidth: 260,
              "& input": { py: 0.5, typography: "s2" },
            }}
          />
        </RecipeRow>
      ) : null}
      {entry.id === "custom_command" ? (
        <RecipeRow
          label="Trace file"
          hint="A command that writes its steps to $FI_TRACE_FILE (ATIF, a mini-SWE-agent trajectory or OpenAI chat messages) gets one span per model turn and tool call; otherwise each try is one span with the end of its output"
        >
          <FormControlLabel
            control={
              <Checkbox
                size="small"
                checked={Boolean(recipe.trace_file)}
                onChange={(e) =>
                  update({ ...recipe, trace_file: e.target.checked })
                }
                inputProps={{ "aria-label": "Writes a trace file" }}
              />
            }
            label={
              <Typography typography="s2">
                {`It writes its steps to $FI_TRACE_FILE (${CUSTOM_TRACE_FILE})`}
              </Typography>
            }
            data-testid="custom-trace-file"
          />
        </RecipeRow>
      ) : null}
      {modelRow}
      {mcpRow}
      {userSimRow}
      {permissionsRow}
      {widening}
      <RecipeRow
        label="What we record"
        hint="What each try records from the harness's own log, the same on your machine, in CI and in the sandbox"
      >
        <CaptureChip recordKey={recordKey} />
        {/* IT-R2 G1 P8: say what the chip means, inline (same key). */}
        <Typography
          typography="s3"
          color="text.secondary"
          data-testid="capture-sentence"
        >
          {RECORD_SENTENCES[recordKey]}
        </Typography>
      </RecipeRow>
      <Typography
        typography="s3"
        color="text.secondary"
        sx={{ pl: 15.5 }}
        data-testid="api-key-note"
      >
        {API_KEY_NOTE}
      </Typography>
      <MoreSettings recipe={recipe} onChange={update} scopeId={entry.id} />
      {/* One key row: the recipe's own here in sandbox mode (the sandbox
          editor's is hidden); CI's editor shows it with the recipe's models. */}
      {mode === "sandbox" ? (
        <SecretsRow mode={mode} models={keyModels} />
      ) : null}
      {mode === "sandbox" ? (
        <SandboxConfig
          entry={entry}
          mode={mode}
          value={sandbox ? { sandbox } : {}}
          onChange={(next) => {
            // SandboxConfig emits `{sandbox: {image|repo, branch, entry,
            // setup, runtime, network, resources}}`: the run reads it at
            // config.harness.sandbox (options-truth L2 G6).
            const sb = next?.sandbox ?? next ?? null;
            setSandbox(sb);
            emit(recipe, { sandbox: sb });
          }}
          dataset={dataset}
          ids={ids}
          models={keyModels}
          showSecrets={false}
        />
      ) : mode === "ci" ? (
        <CiConfig
          entry={entry}
          mode={mode}
          value={ci ?? {}}
          onChange={(next) => {
            const c = next?.ci ?? next ?? null;
            setCi(c);
            onChange?.({ ci: c });
          }}
          limits={limits}
          dataset={dataset}
          ids={ids}
          models={keyModels}
          recipe={recipe}
        />
      ) : (
        <HarnessPaths
          entry={entry}
          form={form}
          onForm={setForm}
          // the drawer's flags ride on the Run it command (harnessTryCommand)
          ids={{ ...scriptIds, run: ids?.run }}
          recipe={recipe}
          dataset={dataset}
          connectedAgentId={
            form === "connect" ? savedWorker?.agent?.id : undefined
          }
          onConnected={onConnected}
        />
      )}
    </Stack>
  );
}

HarnessRecipe.propTypes = {
  entry: PropTypes.object,
  mode: PropTypes.string,
  value: PropTypes.object,
  onChange: PropTypes.func,
  dataset: PropTypes.object,
  ids: PropTypes.shape({
    datasetId: PropTypes.string,
    runLabel: PropTypes.string,
    token: PropTypes.string,
  }),
  onEstimate: PropTypes.func,
  onTryResult: PropTypes.func,
  /** Additive (Ra): the Rerun dialog renders the recipe compactly. */
  compact: PropTypes.bool,
  /** Compact only: the connected machine the rerun runs on, or null. */
  confirmMachine: PropTypes.object,
  /** The run's tries and concurrency (the CI file prints them). */
  limits: PropTypes.object,
};
