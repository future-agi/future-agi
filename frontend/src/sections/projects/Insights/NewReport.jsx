import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  IconButton,
  LinearProgress,
  MenuItem,
  Paper,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import {
  MAX_POPULATION,
  UNITS,
  unitWord,
} from "src/sections/projects/observePopulation";
import {
  collectForScope,
  draftConfig,
  previewConfig,
  rememberPopulation,
  startRun,
  useHasSessions,
  usePopulationCount,
} from "./insightsApi";
import { estimateMinutes, formatCount, formatTotal } from "./insightsFormat";
import {
  useCancellable,
  useDebouncedValue,
  useElapsedSeconds,
} from "./insightsHooks";
import {
  LENSES,
  UNIT_HELP,
  UNIT_ORDER,
  attributeError,
  lensFor,
  unitTitle,
} from "./insightsLenses";
import {
  DATE_PRESETS,
  DEFAULT_SAMPLE_SIZE,
  SAMPLE_SIZES,
  buildConfig,
  buildRunPayload,
  buildScope,
  cleanAttributes,
  describeFilter,
  effectiveSample,
  fixedScope,
  formatWindow,
  presetOption,
  presetWindow,
  sampleSentence,
  scopeLabel,
  splitFilters,
  spreadPick,
  toPopulation,
  withCollectedNote,
} from "./insightsScope";
import NewReportTune from "./NewReportTune";
import PreviewExamples from "./PreviewExamples";

const DRAFT_EXAMPLES = 5;
const PREVIEW_EXAMPLES = 3;
const SMALL_POPULATION = 20;

function initialDraft(seed, initialLens) {
  const lensKey = seed?.question
    ? "custom"
    : LENSES.some((lens) => lens.key === initialLens)
      ? initialLens
      : "usage";
  const lens = lensFor(lensKey);
  const { window, rest } = splitFilters(seed?.filters || []);
  return {
    lens: lensKey,
    question: seed?.question || lens.question,
    unit: UNITS[seed?.unit] ? seed.unit : lens.unit || "sessions",
    preset: window ? null : "7d",
    window: window || presetWindow("7d"),
    customWindow: window,
    filters: rest,
    sampleSize: DEFAULT_SAMPLE_SIZE,
    name: "",
    agentDescription: "",
    instructions: "",
    attributes: [],
  };
}

const normalizeAttributes = (attributes = []) =>
  attributes
    .filter((a) => a && typeof a.key === "string")
    .slice(0, 8)
    .map((a) => ({
      key: a.key.slice(0, 60),
      type: ["boolean", "number", "string"].includes(a.type)
        ? a.type
        : "boolean",
      description: (a.description || "").slice(0, 300),
      filter_by: a.type === "boolean" && Boolean(a.filter_by),
    }));

function autoName(draft, scopeText) {
  const lens = lensFor(draft.lens);
  const head =
    draft.lens === "custom"
      ? draft.question.trim().slice(0, 80) || "Custom question"
      : lens.title;
  return `${head} · ${unitTitle(draft.unit)} · ${scopeText}`.slice(0, 255);
}

function Step({ number, title, subtitle, children, action }) {
  return (
    <Paper variant="outlined" sx={{ p: 2.5, borderRadius: 1.5 }}>
      <Stack direction="row" spacing={1.5} alignItems="flex-start">
        <Box
          aria-hidden
          sx={{
            width: 24,
            height: 24,
            borderRadius: "50%",
            bgcolor: "background.neutral",
            border: "1px solid",
            borderColor: "divider",
            display: "grid",
            placeItems: "center",
            fontSize: 12,
            fontWeight: 600,
            flexShrink: 0,
            mt: "1px",
          }}
        >
          {number}
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack
            direction="row"
            alignItems="flex-start"
            justifyContent="space-between"
            spacing={2}
            sx={{ mb: children ? 2 : 0 }}
          >
            <Box>
              <Typography variant="subtitle1" component="h2">
                {title}
              </Typography>
              {subtitle && (
                <Typography variant="body2" color="text.secondary">
                  {subtitle}
                </Typography>
              )}
            </Box>
            {action}
          </Stack>
          {children}
        </Box>
      </Stack>
    </Paper>
  );
}

Step.propTypes = {
  number: PropTypes.number.isRequired,
  title: PropTypes.node.isRequired,
  subtitle: PropTypes.node,
  children: PropTypes.node,
  action: PropTypes.node,
};

function BusyLine({ label, startedAt, onCancel }) {
  const seconds = useElapsedSeconds(startedAt, true);
  return (
    <Stack direction="row" spacing={1.25} alignItems="center">
      <CircularProgress size={14} thickness={5} />
      <Typography variant="body2" color="text.secondary">
        {label} {seconds > 0 ? `${seconds} s` : ""}
      </Typography>
      {onCancel && (
        <Button size="small" color="inherit" onClick={onCancel}>
          Cancel
        </Button>
      )}
    </Stack>
  );
}

BusyLine.propTypes = {
  label: PropTypes.string.isRequired,
  startedAt: PropTypes.number,
  onCancel: PropTypes.func,
};

function LensPicker({ value, onChange, disabled }) {
  return (
    <Box
      role="radiogroup"
      aria-label="What to learn"
      sx={{
        display: "grid",
        gap: 1.25,
        gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))",
      }}
    >
      {LENSES.map((lens) => {
        const active = lens.key === value;
        return (
          <Box
            key={lens.key}
            component="button"
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            onClick={() => onChange(lens.key)}
            sx={{
              p: 1.5,
              textAlign: "left",
              font: "inherit",
              color: "text.primary",
              bgcolor: active ? "action.selected" : "background.paper",
              border: "1px solid",
              borderColor: active ? "primary.main" : "divider",
              borderRadius: 1.5,
              cursor: disabled ? "default" : "pointer",
              display: "flex",
              flexDirection: "column",
              gap: 0.5,
              "&:hover": disabled
                ? undefined
                : { borderColor: active ? "primary.main" : "text.disabled" },
              "&:focus-visible": {
                outline: "2px solid",
                outlineColor: "primary.main",
                outlineOffset: 2,
              },
            }}
          >
            <Stack direction="row" spacing={0.75} alignItems="center">
              <Iconify icon={lens.icon} width={16} />
              <Typography variant="subtitle2">{lens.title}</Typography>
            </Stack>
            <Typography variant="caption" color="text.secondary">
              {lens.description}
            </Typography>
          </Box>
        );
      })}
    </Box>
  );
}

LensPicker.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

export default function NewReport({
  projectId,
  seed,
  initialLens,
  configs,
  onCancel,
  onStarted,
}) {
  const queryClient = useQueryClient();
  const fixedPopulation = useMemo(
    () =>
      Array.isArray(seed?.population) ? toPopulation(seed.population) : [],
    [seed],
  );
  const fixed = fixedPopulation.length > 0;
  const [draft, setDraft] = useState(() => initialDraft(seed, initialLens));
  const [touched, setTouched] = useState(() => ({
    unit: Boolean(UNITS[seed?.unit]),
    question: Boolean(seed?.question),
    agent: false,
  }));
  const [tuneOpen, setTuneOpen] = useState(false);
  const [previewOffset, setPreviewOffset] = useState(0);
  const [previewedConfig, setPreviewedConfig] = useState("");
  const [startPhase, setStartPhase] = useState("");
  const update = (patch) => setDraft((current) => ({ ...current, ...patch }));

  // Default unit: sessions when the project has them, else traces.
  const hasSessions = useHasSessions(
    projectId,
    !touched.unit && !lensFor(draft.lens).unit,
  );
  const defaultUnit = hasSessions.data === false ? "traces" : "sessions";
  useEffect(() => {
    if (touched.unit || fixed || hasSessions.data !== false) return;
    setDraft((current) =>
      current.unit === "sessions" ? { ...current, unit: "traces" } : current,
    );
  }, [hasSessions.data, touched.unit, fixed]);

  // The agent rarely changes between reports; start from the last one's.
  const priorAgent = useMemo(
    () => (configs || []).find((c) => c.agent_description?.trim()),
    [configs],
  );
  useEffect(() => {
    if (touched.agent || !priorAgent) return;
    setDraft((current) =>
      current.agentDescription
        ? current
        : { ...current, agentDescription: priorAgent.agent_description },
    );
  }, [priorAgent, touched.agent]);

  const setLens = (key) => {
    const next = lensFor(key);
    const previous = lensFor(draft.lens);
    const patch = { lens: key };
    if (!touched.question) patch.question = next.question;
    if (!touched.unit && !fixed) {
      if (next.unit) patch.unit = next.unit;
      else if (previous.unit) patch.unit = defaultUnit;
    }
    update(patch);
  };

  // ── Which data ──────────────────────────────────────────────────────────
  const scopeFilters = useMemo(
    () =>
      buildScope({
        preset: draft.preset,
        window: draft.window,
        filters: draft.filters,
      }).filters,
    [draft.preset, draft.window, draft.filters],
  );
  const debouncedFilters = useDebouncedValue(scopeFilters, 350);
  const count = usePopulationCount({
    unit: draft.unit,
    projectId,
    filters: debouncedFilters,
    enabled: !fixed,
  });
  const counting =
    !fixed && (count.isFetching || debouncedFilters !== scopeFilters);
  const population = fixed
    ? {
        total: fixedPopulation.length,
        isLowerBound: false,
        items: fixedPopulation,
      }
    : count.data && !count.isError
      ? {
          total: count.data.total,
          isLowerBound: count.data.totalIsLowerBound,
          items: count.data.items,
        }
      : null;
  const items = population?.items || [];
  const total = population?.total;
  const currentScopeText = fixed
    ? seed?.label ||
      `${formatCount(fixedPopulation.length)} ${unitWord(draft.unit, fixedPopulation.length)}`
    : scopeLabel({
        preset: draft.preset,
        window: draft.window,
        filters: draft.filters,
      });
  const namePlaceholder = autoName(draft, currentScopeText);

  const selectWindow = (value) => {
    if (value === "custom" && draft.customWindow)
      update({ preset: null, window: draft.customWindow });
    else if (presetOption(value))
      update({ preset: value, window: presetWindow(value) });
  };

  // ── Draft with AI and preview ───────────────────────────────────────────
  const drafting = useCancellable("The draft could not be written.");
  const previewing = useCancellable("The preview could not be made.");
  const starting = useCancellable("The report could not be started.");

  const draftWithAI = () =>
    drafting.run(async (signal) => {
      const data = await draftConfig(
        {
          project_id: projectId,
          unit: draft.unit,
          lens: draft.lens,
          goal: draft.question.trim().slice(0, 500),
          agent_description: draft.agentDescription.trim().slice(0, 500),
          sample_ids: items.slice(0, DRAFT_EXAMPLES).map((item) => item.id),
        },
        signal,
      );
      setDraft((current) => ({
        ...current,
        name: data?.name || current.name,
        question: data?.question || current.question,
        agentDescription: data?.agent_description ?? current.agentDescription,
        instructions: data?.instructions ?? current.instructions,
        attributes: data?.attributes
          ? normalizeAttributes(data.attributes)
          : current.attributes,
      }));
      setTouched((current) => ({ ...current, question: true, agent: true }));
      setTuneOpen(true);
      return data;
    });

  const configForPreview = useMemo(
    () =>
      buildConfig({
        ...draft,
        name: draft.name.trim() || namePlaceholder,
        sampleSize: undefined,
      }),
    [draft, namePlaceholder],
  );
  const configKey = JSON.stringify(configForPreview);

  const preview = (offset) => {
    setPreviewOffset(offset);
    setPreviewedConfig(configKey);
    return previewing.run(async (signal) => {
      const data = await previewConfig(
        {
          project_id: projectId,
          config: configForPreview,
          unit_ids: spreadPick(items, PREVIEW_EXAMPLES, offset).map(
            (item) => item.id,
          ),
        },
        signal,
      );
      return data?.units || [];
    });
  };

  // ── Run ─────────────────────────────────────────────────────────────────
  const questionMissing = !draft.question.trim();
  const attributeProblem = draft.attributes.some((attribute) =>
    attributeError(attribute, draft.attributes),
  );
  const noData = !fixed && !counting && population && total === 0;
  const busy =
    starting.state.status === "busy" ||
    drafting.state.status === "busy" ||
    previewing.state.status === "busy";
  const blocker = questionMissing
    ? "Write the question this report should answer."
    : attributeProblem
      ? "Fix the attribute keys first."
      : noData
        ? `There are no ${UNITS[draft.unit].plural} in this scope.`
        : "";

  const runReport = () =>
    starting.run(async (signal) => {
      let scope;
      let rows;
      if (fixed) {
        scope = fixedScope(
          seed?.label ||
            `${formatCount(fixedPopulation.length)} ${unitWord(draft.unit, fixedPopulation.length)}${seed?.source ? ` from ${seed.source}` : ""}`,
        );
        rows = fixedPopulation;
      } else {
        // Re-anchor a relative window to the moment the run starts.
        const window = draft.preset ? presetWindow(draft.preset) : draft.window;
        scope = buildScope({
          preset: draft.preset,
          window,
          filters: draft.filters,
        });
        setStartPhase(`Collecting ${UNITS[draft.unit].plural}…`);
        const collected = await collectForScope({
          unit: draft.unit,
          projectId,
          scope,
          signal,
        });
        rows = collected.items;
        scope = withCollectedNote(scope, collected);
      }
      if (!rows.length) {
        const error = new Error(
          `There are no ${UNITS[draft.unit].plural} in this scope.`,
        );
        error.statusCode = 400;
        throw error;
      }
      setStartPhase("Starting the report…");
      const created = await startRun(
        buildRunPayload({
          projectId,
          draft: { ...draft, name: draft.name.trim() || namePlaceholder },
          scope,
          population: rows,
        }),
      );
      if (fixed) rememberPopulation(created.config_id, toPopulation(rows));
      queryClient.invalidateQueries({ queryKey: ["insights", "configs"] });
      onStarted(created.config_id, created.run_id);
      return created;
    });

  const sentence = sampleSentence({
    unit: draft.unit,
    total,
    isLowerBound: population?.isLowerBound,
    sampleSize: draft.sampleSize,
    collectLimit: fixed ? undefined : MAX_POPULATION,
  });
  const estimate = Number.isFinite(total)
    ? estimateMinutes(effectiveSample(total, draft.sampleSize))
    : "";

  const draftState = drafting.state;
  const previewState = previewing.state;
  const previewStale =
    previewState.status === "done" && previewedConfig !== configKey;

  return (
    <Stack spacing={2} sx={{ maxWidth: 880, mx: "auto" }}>
      <Stack direction="row" alignItems="flex-start" spacing={2}>
        <Box sx={{ flex: 1 }}>
          <Typography variant="m2" component="h1" fontWeight={600}>
            New report
          </Typography>
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ fontSize: 13 }}
          >
            Pick what to learn and which traffic to read. A report samples it,
            summarises every item, groups them and measures each group.
          </Typography>
        </Box>
        <IconButton
          aria-label="Close new report"
          onClick={onCancel}
          size="small"
        >
          <Iconify icon="mdi:close" width={18} />
        </IconButton>
      </Stack>

      {seed?.source && (
        <Alert
          severity="info"
          icon={<Iconify icon="mdi:link-variant" width={20} />}
        >
          From {seed.source}
          {seed.label ? ` · ${seed.label}` : ""}
        </Alert>
      )}

      <Step number={1} title="What do you want to learn?">
        <Stack spacing={2}>
          <LensPicker value={draft.lens} onChange={setLens} disabled={busy} />
          <TextField
            label="Question"
            placeholder="What do you want this report to answer?"
            value={draft.question}
            onChange={(event) => {
              update({ question: event.target.value.slice(0, 500) });
              setTouched((current) => ({ ...current, question: true }));
            }}
            disabled={busy}
            required={draft.lens === "custom"}
            autoFocus={draft.lens === "custom" && !draft.question}
            InputLabelProps={{ shrink: true }}
            helperText={`The report's heading and what its summary answers · ${draft.question.length}/500`}
          />
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 0.75 }}>
              Read
            </Typography>
            <Stack direction="row" spacing={2} alignItems="center">
              <Tooltip
                describeChild
                arrow
                title={
                  fixed
                    ? `Fixed by the ${UNITS[draft.unit].plural} handed over`
                    : ""
                }
              >
                <ToggleButtonGroup
                  exclusive
                  size="small"
                  value={draft.unit}
                  onChange={(_, value) => {
                    if (!value) return;
                    update({ unit: value });
                    setTouched((current) => ({ ...current, unit: true }));
                  }}
                  aria-label="What each item is"
                  disabled={fixed || busy}
                >
                  {UNIT_ORDER.map((unit) => (
                    <ToggleButton key={unit} value={unit} sx={{ px: 2 }}>
                      {UNITS[unit].title}
                    </ToggleButton>
                  ))}
                </ToggleButtonGroup>
              </Tooltip>
              <Typography variant="body2" color="text.secondary">
                {UNIT_HELP[draft.unit]}
              </Typography>
            </Stack>
          </Box>
        </Stack>
      </Step>

      <Step number={2} title="Which data">
        <Stack spacing={2}>
          {fixed ? (
            <Paper
              variant="outlined"
              sx={{
                p: 1.5,
                borderRadius: 1,
                bgcolor: "background.neutral",
                display: "flex",
                alignItems: "center",
                gap: 1.25,
              }}
            >
              <Iconify icon="mdi:format-list-checks" width={18} />
              <Typography variant="body2">
                <strong>
                  {formatCount(fixedPopulation.length)}{" "}
                  {unitWord(draft.unit, fixedPopulation.length)}
                </strong>
                {seed?.source ? ` from ${seed.source}` : ""}
                {seed?.label && seed.label !== seed?.source
                  ? ` · ${seed.label}`
                  : ""}
              </Typography>
            </Paper>
          ) : (
            <>
              <Stack
                direction="row"
                spacing={2}
                alignItems="center"
                flexWrap="wrap"
                useFlexGap
              >
                <ToggleButtonGroup
                  exclusive
                  size="small"
                  value={draft.preset || "custom"}
                  onChange={(_, value) => value && selectWindow(value)}
                  aria-label="Time window"
                  disabled={busy}
                >
                  {DATE_PRESETS.map((option) => (
                    <ToggleButton
                      key={option.value}
                      value={option.value}
                      sx={{ px: 1.75 }}
                    >
                      {option.label}
                    </ToggleButton>
                  ))}
                  {draft.customWindow && (
                    <ToggleButton value="custom" sx={{ px: 1.75 }}>
                      {formatWindow(draft.customWindow)}
                    </ToggleButton>
                  )}
                </ToggleButtonGroup>
                {draft.preset && (
                  <Typography variant="caption" color="text.secondary">
                    Runs again re-anchor this window to the time they start.
                  </Typography>
                )}
              </Stack>
              {draft.filters.length > 0 && (
                <Box>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: "block", mb: 0.75 }}
                  >
                    Filters{seed?.label ? ` from ${seed.label}` : ""}
                  </Typography>
                  <Stack
                    direction="row"
                    spacing={0.75}
                    useFlexGap
                    flexWrap="wrap"
                  >
                    {draft.filters.map((filter, index) => (
                      <Chip
                        key={`${filter.column_id}-${index}`}
                        size="small"
                        label={describeFilter(filter)}
                        onDelete={
                          busy
                            ? undefined
                            : () =>
                                update({
                                  filters: draft.filters.filter(
                                    (_, i) => i !== index,
                                  ),
                                })
                        }
                        sx={{ maxWidth: 420 }}
                      />
                    ))}
                  </Stack>
                </Box>
              )}
            </>
          )}

          <Stack
            direction="row"
            spacing={3}
            alignItems="center"
            flexWrap="wrap"
            useFlexGap
          >
            <Box sx={{ minWidth: 200 }}>
              {counting && !population ? (
                <Stack direction="row" spacing={1} alignItems="center">
                  <CircularProgress size={14} thickness={5} />
                  <Typography variant="body2" color="text.secondary">
                    Counting…
                  </Typography>
                </Stack>
              ) : count.isError && !fixed ? (
                <Typography variant="body2" color="text.secondary">
                  Couldn&apos;t count this scope. The report still collects it
                  when it runs.
                </Typography>
              ) : population ? (
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography
                    variant="subtitle1"
                    sx={{
                      fontVariantNumeric: "tabular-nums",
                      opacity: counting ? 0.5 : 1,
                    }}
                  >
                    {formatTotal(total, population.isLowerBound)}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {unitWord(draft.unit, total)}{" "}
                    {fixed ? "handed over" : "match"}
                  </Typography>
                  {counting && <CircularProgress size={12} thickness={5} />}
                </Stack>
              ) : null}
            </Box>
            <TextField
              select
              size="small"
              label="Sample size"
              value={draft.sampleSize}
              onChange={(event) =>
                update({ sampleSize: Number(event.target.value) })
              }
              disabled={busy}
              sx={{ width: 140 }}
            >
              {SAMPLE_SIZES.map((size) => (
                <MenuItem key={size} value={size}>
                  {formatCount(size)}
                </MenuItem>
              ))}
            </TextField>
            {sentence && (
              <Typography variant="body2" color="text.secondary">
                {sentence}
                {estimate ? ` · ${estimate}` : ""}
              </Typography>
            )}
          </Stack>
          {Number.isFinite(total) && total > 0 && total < SMALL_POPULATION && (
            <Typography variant="caption" color="text.secondary">
              Fewer than {SMALL_POPULATION} {UNITS[draft.unit].plural} give
              rough categories and wide error bars. Widen the window if you can.
            </Typography>
          )}
        </Stack>
      </Step>

      <Paper variant="outlined" sx={{ p: 2, borderRadius: 1.5 }}>
        <NewReportTune
          open={tuneOpen}
          onToggle={() => setTuneOpen((value) => !value)}
          draft={draft}
          onChange={(patch) => {
            update(patch);
            if ("agentDescription" in patch)
              setTouched((current) => ({ ...current, agent: true }));
          }}
          namePlaceholder={namePlaceholder}
          disabled={busy}
          draftAction={
            draftState.status === "busy" ? (
              <BusyLine
                label={`Reading ${Math.min(DRAFT_EXAMPLES, items.length)} examples and drafting…`}
                startedAt={draftState.startedAt}
                onCancel={drafting.cancel}
              />
            ) : (
              <Tooltip
                describeChild
                arrow
                title={
                  items.length
                    ? `Reads ${Math.min(DRAFT_EXAMPLES, items.length)} real ${UNITS[draft.unit].plural} and drafts the name, question, agent description, instructions and attributes. Takes 10–40 seconds.`
                    : "Needs at least one item in the scope"
                }
              >
                <span>
                  <Button
                    size="small"
                    variant="outlined"
                    startIcon={
                      <Iconify icon="mdi:creation-outline" width={16} />
                    }
                    onClick={draftWithAI}
                    disabled={busy || !items.length}
                  >
                    Draft with AI
                  </Button>
                </span>
              </Tooltip>
            )
          }
          draftNote={
            draftState.status === "error" ? (
              <Alert severity="error" sx={{ mt: 1.5 }} onClose={drafting.reset}>
                {draftState.error}
              </Alert>
            ) : draftState.status === "done" ? (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 1 }}
              >
                Drafted from {draftState.data?.examples_read || DRAFT_EXAMPLES}{" "}
                real examples. Review the fields before running.
              </Typography>
            ) : !tuneOpen &&
              draft.agentDescription &&
              !touched.agent &&
              priorAgent ? (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 0.5, ml: 4 }}
              >
                Uses the agent description from “{priorAgent.name}”.
              </Typography>
            ) : null
          }
        />
      </Paper>

      <Paper variant="outlined" sx={{ p: 2, borderRadius: 1.5 }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ mb: previewState.status === "idle" ? 0 : 2 }}
        >
          <Typography variant="subtitle2" sx={{ pl: 0.5 }}>
            Preview
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Optional · see what the report reads from {PREVIEW_EXAMPLES} real{" "}
            {UNITS[draft.unit].plural} before spending a full run
          </Typography>
          <Box sx={{ flex: 1 }} />
          {previewState.status === "busy" ? (
            <BusyLine
              label={`Reading ${PREVIEW_EXAMPLES} examples…`}
              startedAt={previewState.startedAt}
              onCancel={previewing.cancel}
            />
          ) : (
            <Button
              size="small"
              variant="outlined"
              startIcon={<Iconify icon="mdi:eye-outline" width={16} />}
              onClick={() =>
                preview(
                  previewState.status === "done" && !previewStale
                    ? previewOffset + 1
                    : previewOffset,
                )
              }
              disabled={
                busy || !items.length || questionMissing || attributeProblem
              }
            >
              {previewState.status === "done" && !previewStale
                ? `Try ${PREVIEW_EXAMPLES} others`
                : `Preview on ${PREVIEW_EXAMPLES} examples`}
            </Button>
          )}
        </Stack>
        {previewState.status === "error" && (
          <Alert severity="error" onClose={previewing.reset}>
            {previewState.error}
          </Alert>
        )}
        {previewState.status === "busy" && (
          <LinearProgress sx={{ borderRadius: 1 }} />
        )}
        {previewState.status === "done" && (
          <Stack spacing={1}>
            {previewStale && (
              <Typography variant="caption" color="text.secondary">
                The settings changed since this preview. Preview again to see
                their effect.
              </Typography>
            )}
            {previewState.data?.length ? (
              <PreviewExamples
                units={previewState.data}
                lens={draft.lens}
                attributes={cleanAttributes(draft.attributes)}
              />
            ) : (
              <Typography variant="body2" color="text.secondary">
                Nothing readable in these examples. Try others.
              </Typography>
            )}
          </Stack>
        )}
      </Paper>

      <Paper
        elevation={0}
        sx={{
          position: "sticky",
          bottom: 0,
          zIndex: 2,
          pl: 2,
          // Clear of the app's floating assistant buttons.
          pr: 9,
          py: 1.25,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          bgcolor: "background.paper",
          boxShadow: (theme) => theme.customShadows?.dropdown,
          mb: 2,
        }}
      >
        <Stack direction="row" alignItems="center" spacing={2}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            {starting.state.status === "busy" ? (
              <BusyLine
                label={startPhase || "Starting…"}
                startedAt={starting.state.startedAt}
                onCancel={
                  startPhase.startsWith("Collecting")
                    ? starting.cancel
                    : undefined
                }
              />
            ) : starting.state.status === "error" ? (
              <Typography variant="body2" color="error.main">
                {starting.state.error}
              </Typography>
            ) : (
              <Typography variant="body2" color="text.secondary" noWrap>
                {blocker ||
                  [sentence, estimate, "it keeps running if you leave"]
                    .filter(Boolean)
                    .join(" · ")}
              </Typography>
            )}
          </Box>
          <Button color="inherit" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={runReport}
            disabled={
              Boolean(blocker) || busy || (!fixed && !population && counting)
            }
            startIcon={<Iconify icon="mdi:play" width={16} />}
          >
            Run report
          </Button>
        </Stack>
      </Paper>
    </Stack>
  );
}

NewReport.propTypes = {
  projectId: PropTypes.string.isRequired,
  seed: PropTypes.object,
  initialLens: PropTypes.string,
  configs: PropTypes.array,
  onCancel: PropTypes.func.isRequired,
  onStarted: PropTypes.func.isRequired,
};
