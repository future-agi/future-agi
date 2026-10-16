import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  MenuItem,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { UNITS, unitWord } from "src/sections/projects/observePopulation";
import {
  insightsKeys,
  collectForScope,
  patchConfig,
  recallPopulation,
  rememberPopulation,
  startRun,
  usePopulationCount,
} from "./insightsApi";
import {
  estimateMinutes,
  formatCount,
  formatDay,
  formatTotal,
} from "./insightsFormat";
import { useCancellable, useDebouncedValue } from "./insightsHooks";
import {
  DATE_PRESETS,
  DEFAULT_SAMPLE_SIZE,
  SAMPLE_SIZES,
  buildRerunPayload,
  buildScope,
  effectiveSample,
  formatWindow,
  isFixedScope,
  presetOption,
  presetWindow,
  reanchorScope,
  sampleSentence,
  splitFilters,
  withCollectedNote,
} from "./insightsScope";

const NO_FILTERS = [];

const lastScope = (config) =>
  config?.scope && Object.keys(config.scope).length
    ? config.scope
    : config?.latest_run?.scope || {};

/**
 * Run a saved report again: same scope (a relative window moves to now) or
 * another window with the same filters, and optionally forget the saved
 * categories so this run discovers new ones.
 */
function RerunForm({
  projectId,
  config,
  initialRediscover,
  onClose,
  onStarted,
}) {
  const queryClient = useQueryClient();
  const unit = config.unit;
  const plural = (UNITS[unit] || UNITS.traces).plural;
  const base = useMemo(() => lastScope(config), [config]);
  const fixed = isFixedScope(base);
  const remembered = useMemo(
    () => (fixed ? recallPopulation(config.id) : null),
    [fixed, config.id],
  );
  const { window: baseWindow, rest } = useMemo(
    () => splitFilters(base.filters || []),
    [base],
  );
  const sameAvailable = !fixed || Boolean(remembered);
  const [choice, setChoice] = useState(sameAvailable ? "same" : "7d");
  const [sampleSize, setSampleSize] = useState(
    config.sample_size || DEFAULT_SAMPLE_SIZE,
  );
  const [rediscover, setRediscover] = useState(Boolean(initialRediscover));
  // The window is fixed when the choice is made, so the count is stable.
  const [anchor] = useState(() => new Date());

  const scope = useMemo(() => {
    if (choice === "same") return fixed ? base : reanchorScope(base, anchor);
    return buildScope({
      preset: choice,
      window: presetWindow(choice, anchor),
      filters: rest,
    });
  }, [choice, fixed, base, rest, anchor]);

  const filters = useDebouncedValue(scope.filters || NO_FILTERS, 250);
  const count = usePopulationCount({
    unit,
    projectId,
    filters,
    enabled: !(choice === "same" && fixed),
  });
  const total =
    choice === "same" && fixed ? remembered?.length : count.data?.total;
  const isLowerBound =
    choice === "same" && fixed ? false : count.data?.totalIsLowerBound;

  const sameLabel = fixed
    ? remembered
      ? `The same ${formatCount(remembered.length)} ${unitWord(unit, remembered.length)} as last run`
      : `The same ${plural} as last run (not available: they were handed over in another browser session)`
    : presetOption(base.preset)
      ? `Same as last run: ${presetOption(base.preset).label}, ending now`
      : baseWindow
        ? `Same window as last run: ${formatWindow(baseWindow)}`
        : `Same as last run: ${base.label || `all ${plural}`}`;

  const starting = useCancellable("The report could not be started.");
  const run = () =>
    starting.run(async (signal) => {
      if (rediscover) await patchConfig(config.id, { reset_taxonomy: true });
      let runScope = scope;
      let population;
      if (choice === "same" && fixed) {
        population = remembered;
      } else {
        const collected = await collectForScope({
          unit,
          projectId,
          scope: reanchorScope(scope),
          signal,
        });
        runScope = withCollectedNote(reanchorScope(scope), collected);
        population = collected.items;
      }
      if (!population?.length) {
        const error = new Error(`There are no ${plural} in this scope.`);
        error.statusCode = 400;
        throw error;
      }
      const created = await startRun(
        buildRerunPayload({
          projectId,
          configId: config.id,
          scope: runScope,
          population,
          sampleSize,
        }),
      );
      if (isFixedScope(runScope)) rememberPopulation(config.id, population);
      queryClient.invalidateQueries({
        queryKey: insightsKeys.config(config.id),
      });
      queryClient.invalidateQueries({ queryKey: ["insights", "configs"] });
      onStarted(created.config_id, created.run_id);
      return created;
    });

  const busy = starting.state.status === "busy";
  const sentence = sampleSentence({
    unit,
    total,
    isLowerBound,
    sampleSize,
  });

  return (
    <>
      <DialogTitle sx={{ pb: 1 }}>Run “{config.name}” again</DialogTitle>
      <DialogContent>
        <Stack spacing={2.5}>
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
              Which data
            </Typography>
            <RadioGroup
              value={choice}
              onChange={(event) => setChoice(event.target.value)}
            >
              <FormControlLabel
                value="same"
                disabled={!sameAvailable || busy}
                control={<Radio size="small" />}
                label={<Typography variant="body2">{sameLabel}</Typography>}
              />
              {DATE_PRESETS.map((option) => (
                <FormControlLabel
                  key={option.value}
                  value={option.value}
                  disabled={busy}
                  control={<Radio size="small" />}
                  label={
                    <Typography variant="body2">
                      {option.label}
                      {rest.length
                        ? ` · the last run's ${rest.length} filter${rest.length === 1 ? "" : "s"}`
                        : ""}
                    </Typography>
                  }
                />
              ))}
            </RadioGroup>
          </Box>
          <Stack direction="row" spacing={2} alignItems="center">
            <TextField
              select
              size="small"
              label="Sample size"
              value={sampleSize}
              onChange={(event) => setSampleSize(Number(event.target.value))}
              disabled={busy}
              sx={{ width: 140 }}
            >
              {SAMPLE_SIZES.map((size) => (
                <MenuItem key={size} value={size}>
                  {formatCount(size)}
                </MenuItem>
              ))}
            </TextField>
            <Typography variant="body2" color="text.secondary">
              {count.isFetching && !(choice === "same" && fixed) ? (
                <Stack
                  component="span"
                  direction="row"
                  spacing={1}
                  alignItems="center"
                >
                  <CircularProgress size={12} />
                  <span>Counting…</span>
                </Stack>
              ) : Number.isFinite(total) ? (
                `${formatTotal(total, isLowerBound)} ${unitWord(unit, total)} match. ${sentence}${
                  total
                    ? ` · ${estimateMinutes(effectiveSample(total, sampleSize))}`
                    : ""
                }`
              ) : (
                ""
              )}
            </Typography>
          </Stack>
          {config.has_taxonomy && (
            <Box>
              <FormControlLabel
                control={
                  <Checkbox
                    size="small"
                    checked={rediscover}
                    onChange={(event) => setRediscover(event.target.checked)}
                    disabled={busy}
                  />
                }
                label={
                  <Typography variant="body2">Rediscover categories</Typography>
                }
              />
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", ml: 3.75 }}
              >
                {rediscover
                  ? "This run finds categories afresh and the report keeps them from now on. Earlier runs keep theirs, so their shares stop being comparable with later runs."
                  : `This run sorts into the report's saved categories${
                      config.taxonomy?.frozen_at
                        ? ` from ${formatDay(config.taxonomy.frozen_at)}`
                        : ""
                    }, so its shares compare with earlier runs. Anything that fits none of them shows up as new.`}
              </Typography>
            </Box>
          )}
          {starting.state.status === "error" && (
            <Alert severity="error">{starting.state.error}</Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2.5 }}>
        <Button color="inherit" onClick={busy ? starting.cancel : onClose}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={run}
          disabled={busy || (Number.isFinite(total) && total === 0)}
          startIcon={
            busy ? <CircularProgress size={14} color="inherit" /> : null
          }
        >
          {busy ? "Starting…" : "Run again"}
        </Button>
      </DialogActions>
    </>
  );
}

RerunForm.propTypes = {
  projectId: PropTypes.string.isRequired,
  config: PropTypes.object.isRequired,
  initialRediscover: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  onStarted: PropTypes.func.isRequired,
};

export default function RerunDialog({ open, config, onClose, ...rest }) {
  return (
    <Dialog
      open={open && Boolean(config)}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
    >
      {config && (
        <RerunForm
          key={config.id}
          config={config}
          onClose={onClose}
          {...rest}
        />
      )}
    </Dialog>
  );
}

RerunDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  config: PropTypes.object,
  onClose: PropTypes.func.isRequired,
};
