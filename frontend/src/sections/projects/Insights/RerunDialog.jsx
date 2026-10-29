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
import ScopeFilterBar from "src/sections/projects/ScopeFilterBar";
import { defaultScope } from "src/sections/projects/observeScope";
import {
  insightsKeys,
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
  DEFAULT_SAMPLE_SIZE,
  buildRerunPayload,
  countedScope,
  editableScope,
  effectiveSample,
  isFixedScope,
  lastScope,
  reanchorScope,
  sampleSentence,
  sampleSizeOptions,
  scopeDateLabel,
  storedScope,
} from "./insightsScope";
import { CreditLimit, ErrorText } from "./parts";
import { creditWords } from "src/sections/projects/LLMTracing/InstantEval/page/askFilter";

/**
 * Run a saved report again: the last run's date and filters (a preset window
 * moved to now), changed here if need be, or the same rows handed over; and
 * optionally forget the saved categories so this run discovers new ones.
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
  // Rows handed over can't be read again from filters: other data starts
  // from the past week.
  const [scope, setScope] = useState(() =>
    fixed ? defaultScope(unit) : editableScope(base, unit),
  );
  const [choice, setChoice] = useState(remembered ? "same" : "scope");
  const sameRows = fixed && choice === "same";
  const [sampleSize, setSampleSize] = useState(
    config.sample_size || DEFAULT_SAMPLE_SIZE,
  );
  const [rediscover, setRediscover] = useState(Boolean(initialRediscover));

  const countScope = useMemo(() => countedScope(scope), [scope]);
  const settledScope = useDebouncedValue(countScope, 250);
  const count = usePopulationCount({
    unit,
    projectId,
    scope: settledScope,
    sampleSize,
    enabled: !sameRows,
  });
  // The same rows again have nothing to count: the server only prices them.
  const sameCost = usePopulationCount({
    unit,
    projectId,
    populationSize: remembered?.length,
    sampleSize,
    enabled: sameRows,
  });
  const costing =
    sameRows && (sameCost.isLoading || sameCost.isPlaceholderData);
  const total = sameRows ? remembered?.length : count.data?.total;
  const isLowerBound = sameRows ? false : count.data?.totalIsLowerBound;
  // A Users count still climbing ("N+") shows as it goes.
  const counting =
    !sameRows &&
    (count.isLoading || count.isPlaceholderData || settledScope !== countScope);

  const sameLabel = remembered
    ? `The same ${formatCount(remembered.length)} ${unitWord(unit, remembered.length)} as last run`
    : `The same ${plural} as last run (not available: they were handed over in another browser session)`;

  const starting = useCancellable(
    "The report could not be started.",
    "Couldn't start right now. Try again in a moment.",
  );
  const run = () =>
    starting.run(async () => {
      // The server reads the rows a scope of filters matches, its preset
      // window moved to the moment the run starts.
      const runScope = sameRows ? base : storedScope(reanchorScope(scope));
      const population = sameRows ? remembered : undefined;
      const created = await startRun(
        buildRerunPayload({
          projectId,
          configId: config.id,
          scope: runScope,
          population,
          sampleSize,
          resetTaxonomy: rediscover,
        }),
      );
      if (sameRows) rememberPopulation(config.id, population);
      queryClient.invalidateQueries({
        queryKey: insightsKeys.config(config.id),
      });
      queryClient.invalidateQueries({ queryKey: ["insights", "configs"] });
      onStarted(created.config_id, created.run_id);
      return created;
    });

  const busy = starting.state.status === "busy";
  // What the run costs, and whether the credits left pay for it.
  const credits =
    (sameRows ? sameCost.data?.credits : count.data?.credits) || null;
  const creditLimited = Boolean(credits?.limited);
  const sentence = sampleSentence({
    unit,
    total,
    isLowerBound,
    sampleSize,
    handedOver: sameRows,
  });

  return (
    <>
      <DialogTitle sx={{ pb: 1 }}>Run “{config.name}” again</DialogTitle>
      <DialogContent>
        <Stack spacing={2.5}>
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 0.75 }}>
              Which data
            </Typography>
            {fixed && (
              <RadioGroup
                value={sameRows ? "same" : "scope"}
                onChange={(event) => setChoice(event.target.value)}
              >
                <FormControlLabel
                  value="same"
                  disabled={!remembered || busy}
                  control={<Radio size="small" />}
                  label={<Typography variant="body2">{sameLabel}</Typography>}
                />
                <FormControlLabel
                  value="scope"
                  disabled={busy}
                  control={<Radio size="small" />}
                  label={
                    <Typography variant="body2">
                      Other {plural}, by date and filters
                    </Typography>
                  }
                />
              </RadioGroup>
            )}
            {!sameRows && (
              <Box sx={{ mt: fixed ? 1 : 0 }}>
                <ScopeFilterBar
                  projectId={projectId}
                  unit={unit}
                  scope={scope}
                  onChange={setScope}
                  disabled={busy}
                  dateLabel={scopeDateLabel(scope)}
                />
                {!fixed && (
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    component="p"
                    sx={{ mt: 0.75 }}
                  >
                    The last run&apos;s date and filters; a preset window ends
                    now.
                  </Typography>
                )}
              </Box>
            )}
          </Box>
          <Stack direction="row" spacing={2} alignItems="center">
            <TextField
              select
              size="small"
              label="Sample size"
              value={sampleSize}
              onChange={(event) => setSampleSize(Number(event.target.value))}
              disabled={busy}
              // Beside a long estimate the row would squeeze it and cut its
              // label to "Sample siz".
              sx={{ width: 140, flexShrink: 0 }}
            >
              {sampleSizeOptions(sampleSize).map((size) => (
                <MenuItem key={size} value={size}>
                  {formatCount(size)}
                </MenuItem>
              ))}
            </TextField>
            <Typography variant="body2" color="text.secondary">
              {counting ? (
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
                }${credits?.estimate > 0 ? ` · ${creditWords(credits.estimate)}` : ""}`
              ) : (
                ""
              )}
            </Typography>
          </Stack>
          {((sameRows ? sameCost.isError : count.isError) ||
            credits?.unavailable) && (
            <Alert
              severity="error"
              action={
                <Button
                  color="inherit"
                  onClick={() => (sameRows ? sameCost : count).refetch()}
                >
                  Retry cost check
                </Button>
              }
            >
              {credits?.unavailable
                ? credits.message
                : "The report cost could not be checked. Check it before running again."}
            </Alert>
          )}
          {creditLimited && !credits.unavailable && (
            <CreditLimit credits={credits} onSample={setSampleSize} />
          )}
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
            <Alert severity="error">
              <ErrorText
                message={starting.state.error}
                billing={starting.state.billing}
              />
            </Alert>
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
          // A new sample size is costed first: the shown cost is the
          // previous one until the server answers.
          disabled={
            busy ||
            creditLimited ||
            counting ||
            costing ||
            (sameRows ? sameCost.isError : count.isError) ||
            (Number.isFinite(total) && total === 0)
          }
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
