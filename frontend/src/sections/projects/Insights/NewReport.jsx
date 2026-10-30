import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
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
import { UNITS, unitWord } from "src/sections/projects/observePopulation";
import ScopeFilterBar from "src/sections/projects/ScopeFilterBar";
import {
  VIEWER_CANT_RUN,
  useCanStartRuns,
} from "src/sections/projects/runPermission";
import {
  CUSTOM,
  defaultScope,
  scopeForUnit,
  scopeFromSeed,
} from "src/sections/projects/observeScope";
import {
  draftConfig,
  actionCreditsBlocked,
  previewConfig,
  rememberPopulation,
  startRun,
  useActionCredits,
  useExampleRows,
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
  DEFAULT_SAMPLE_SIZE,
  buildConfig,
  buildRunPayload,
  cleanAttributes,
  countedScope,
  effectiveSample,
  fixedScope,
  reanchorScope,
  sampleSentence,
  sampleSizeOptions,
  spreadPick,
  storedScope,
  toPopulation,
} from "./insightsScope";
import NewReportTune from "./NewReportTune";
import { CreditLimit, ErrorText } from "./parts";
import { creditWords } from "src/sections/projects/LLMTracing/InstantEval/page/askFilter";
import PreviewExamples from "./PreviewExamples";
import ActionCreditNote from "./ActionCreditNote";

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
  const unit = UNITS[seed?.unit] ? seed.unit : lens.unit || "sessions";
  return {
    lens: lensKey,
    question: seed?.question || lens.question,
    unit,
    // The view's date and filters, else the past week.
    scope: seed ? scopeFromSeed(seed, unit) : defaultScope(unit),
    scopeNotice: null,
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

// Another unit keeps the date; filters it can't read are dropped, and the
// form says so.
function withUnit(draft, unit) {
  if (unit === draft.unit) return draft;
  const moved = scopeForUnit(draft.scope, unit);
  return { ...draft, unit, scope: moved.scope, scopeNotice: moved.notice };
}

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
  const canRun = useCanStartRuns();
  const draftCredits = useActionCredits(projectId, "draft");
  const previewCredits = useActionCredits(projectId, "preview");
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
      current.unit === "sessions" ? withUnit(current, "traces") : current,
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
    let unit = null;
    if (!touched.unit && !fixed) {
      if (next.unit) unit = next.unit;
      else if (previous.unit) unit = defaultUnit;
    }
    setDraft((current) => {
      const patched = { ...current, ...patch };
      return unit ? withUnit(patched, unit) : patched;
    });
  };

  // ── Which data ──────────────────────────────────────────────────────────
  // Counted on the server the way the run reads it: no cap in the browser.
  // The draft and preview read a few of the newest rows.
  const countScope = useMemo(() => countedScope(draft.scope), [draft.scope]);
  const settledScope = useDebouncedValue(countScope, 350);
  const count = usePopulationCount({
    unit: draft.unit,
    projectId,
    scope: settledScope,
    sampleSize: draft.sampleSize,
    enabled: !fixed,
  });
  // Rows handed over have nothing to count: the server only prices them.
  const handedCost = usePopulationCount({
    unit: draft.unit,
    projectId,
    populationSize: fixedPopulation.length,
    sampleSize: draft.sampleSize,
    enabled: fixed,
  });
  const serverExamples = count.data?.sampleIds;
  const examples = useExampleRows({
    unit: draft.unit,
    projectId,
    filters: settledScope.filters,
    search: settledScope.search,
    enabled: !fixed && Boolean(count.data) && !serverExamples,
  });
  // A new scope is being counted; a Users count still climbing ("N+") is
  // read again on its own and needn't hold anything up.
  const counting =
    !fixed &&
    (count.isLoading || count.isPlaceholderData || settledScope !== countScope);
  // A handed-over population's cost for a new sample size, likewise.
  const costing =
    fixed && (handedCost.isLoading || handedCost.isPlaceholderData);
  const stillCounting =
    !fixed && draft.unit === "users" && Boolean(count.data?.totalIsLowerBound);
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
          items: serverExamples
            ? serverExamples.map((id) => ({ id }))
            : examples.data?.items || [],
        }
      : null;
  const items = population?.items || [];
  const total = population?.total;
  const currentScopeText = fixed
    ? seed?.label ||
      `${formatCount(fixedPopulation.length)} ${unitWord(draft.unit, fixedPopulation.length)}`
    : draft.scope.label;
  const namePlaceholder = autoName(draft, currentScopeText);

  // ── Draft with AI and preview ───────────────────────────────────────────
  const drafting = useCancellable(
    "The draft could not be written.",
    "Couldn't draft right now. Try again in a moment.",
  );
  const previewing = useCancellable(
    "The preview could not be made.",
    "Couldn't preview right now. Try again in a moment.",
  );
  const starting = useCancellable(
    "The report could not be started.",
    "Couldn't start right now. Try again in a moment.",
  );

  const draftWithAI = () => {
    if (actionCreditsBlocked(draftCredits)) return;
    return drafting.run(async (signal) => {
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
      queryClient.invalidateQueries({
        queryKey: ["insights", "action-credits"],
      });
      return data;
    });
  };

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
    if (actionCreditsBlocked(previewCredits)) return;
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
      queryClient.invalidateQueries({
        queryKey: ["insights", "action-credits"],
      });
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
  // What the run costs, and whether the credits left pay for it.
  const credits =
    (fixed ? handedCost.data?.credits : count.data?.credits) || null;
  const creditLimited = Boolean(credits?.limited);
  const creditBlocker = creditLimited
    ? credits.message || "The credits left this month don't cover this report."
    : "";
  const blocker = questionMissing
    ? "Write the question this report should answer."
    : attributeProblem
      ? "Fix the attribute keys first."
      : noData
        ? `There are no ${UNITS[draft.unit].plural} in this scope.`
        : creditBlocker;

  const runReport = () =>
    starting.run(async () => {
      let scope;
      let rows;
      if (fixed) {
        scope = fixedScope(
          seed?.label ||
            `${formatCount(fixedPopulation.length)} ${unitWord(draft.unit, fixedPopulation.length)}${seed?.source ? ` from ${seed.source}` : ""}`,
        );
        rows = fixedPopulation;
        if (!rows.length) {
          const error = new Error(
            `There are no ${UNITS[draft.unit].plural} in this scope.`,
          );
          error.statusCode = 400;
          throw error;
        }
      } else {
        // A preset's window moves to the moment the run starts; the server
        // reads the rows the filters match.
        scope = storedScope(reanchorScope(draft.scope));
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
    handedOver: fixed,
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
                    setDraft((current) => withUnit(current, value));
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
            <Box>
              <ScopeFilterBar
                projectId={projectId}
                unit={draft.unit}
                scope={draft.scope}
                onChange={(scope) => update({ scope, scopeNotice: null })}
                disabled={busy}
              />
              <Typography
                variant="caption"
                color="text.secondary"
                component="p"
                role={draft.scopeNotice ? "status" : undefined}
                sx={{ mt: 0.75 }}
              >
                {draft.scopeNotice ||
                  (draft.scope.dateOption !== CUSTOM
                    ? "Runs again re-anchor this window to the time they start."
                    : "Runs again read this same range.")}
              </Typography>
            </Box>
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
                  The scope and report cost could not be checked.
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
                  {(counting || stillCounting) && (
                    <CircularProgress
                      size={12}
                      thickness={5}
                      aria-label={`Counting the ${UNITS[draft.unit].plural}`}
                    />
                  )}
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
              {sampleSizeOptions(draft.sampleSize).map((size) => (
                <MenuItem key={size} value={size}>
                  {formatCount(size)}
                </MenuItem>
              ))}
            </TextField>
            {sentence && (
              <Typography variant="body2" color="text.secondary">
                {sentence}
                {estimate ? ` · ${estimate}` : ""}
                {credits?.estimate > 0
                  ? ` · ${creditWords(credits.estimate)}`
                  : ""}
              </Typography>
            )}
          </Stack>
          {((fixed ? handedCost.isError : count.isError) ||
            credits?.unavailable) && (
            <Alert
              severity="error"
              action={
                <Button
                  color="inherit"
                  onClick={() => (fixed ? handedCost : count).refetch()}
                >
                  Retry cost check
                </Button>
              }
            >
              {credits?.unavailable
                ? credits.message
                : "The report cost could not be checked. Check it before running."}
            </Alert>
          )}
          {creditLimited && !credits.unavailable && (
            <CreditLimit
              credits={credits}
              onSample={(size) => update({ sampleSize: size })}
            />
          )}
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
              <Stack spacing={0.5} alignItems="flex-end">
                <ActionCreditNote query={draftCredits} label="Draft" />
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
                      disabled={
                        !canRun ||
                        busy ||
                        !items.length ||
                        actionCreditsBlocked(draftCredits)
                      }
                    >
                      Draft with AI
                    </Button>
                  </span>
                </Tooltip>
              </Stack>
            )
          }
          draftNote={
            draftState.status === "error" ? (
              <Alert severity="error" sx={{ mt: 1.5 }} onClose={drafting.reset}>
                <ErrorText
                  message={draftState.error}
                  billing={draftState.billing}
                />
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
            <Box component="span" sx={{ display: "block" }}>
              <ActionCreditNote query={previewCredits} label="Preview" />
            </Box>
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
                !canRun ||
                busy ||
                !items.length ||
                questionMissing ||
                attributeProblem ||
                actionCreditsBlocked(previewCredits)
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
            <ErrorText
              message={previewState.error}
              billing={previewState.billing}
            />
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
              />
            ) : starting.state.status === "error" ? (
              <Typography variant="body2" color="error.main">
                <ErrorText
                  message={starting.state.error}
                  billing={starting.state.billing}
                />
              </Typography>
            ) : (
              // Wraps rather than cuts off: a refusal is read whole, with
              // its way to billing.
              <Typography variant="body2" color="text.secondary">
                {!canRun ? (
                  VIEWER_CANT_RUN
                ) : blocker ? (
                  <ErrorText
                    message={blocker}
                    billing={blocker === creditBlocker && !credits.unavailable}
                  />
                ) : (
                  [
                    sentence,
                    estimate,
                    credits?.estimate > 0 && creditWords(credits.estimate),
                    "it keeps running if you leave",
                  ]
                    .filter(Boolean)
                    .join(" · ")
                )}
              </Typography>
            )}
          </Box>
          <Button color="inherit" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={runReport}
            // While a new scope or sample size is costed, the shown cost is
            // the previous one: Run waits for the server's figure.
            disabled={
              !canRun ||
              Boolean(blocker) ||
              busy ||
              counting ||
              costing ||
              (fixed ? handedCost.isError : count.isError)
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
