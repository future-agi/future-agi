import PropTypes from "prop-types";
import { useEffect, useId, useRef, useState } from "react";
import {
  Alert,
  Button,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useController, useWatch } from "react-hook-form";
import { ClosableDialog } from "src/components/custom-dialog";
import SamplingPolicyEditor from "./SamplingPolicyEditor";
import { RowLimitField, SamplingRateField } from "./task_sampling_fields";
import {
  SAMPLING_WINDOW_MAX_DAYS,
  advancedSamplingSummary,
  fitSamplingWindow,
  isSimpleSamplingPolicy,
  isValidSamplingRate,
  pickLevels,
  pickNames,
  resetHistoricalSamplingCounts,
  rowNames,
  samplingWindowInvalid,
  simpleSamplingPolicy,
  simpleSelectionText,
} from "./simpleSamplingPolicy";

export function TaskSamplingSummary({ control }) {
  const [policy, rate, limit, runType, rowType] = useWatch({
    control,
    name: [
      "samplingPolicy",
      "samplingRate",
      "spansLimit",
      "runType",
      "rowType",
    ],
  });
  // The field already shows the error for an invalid percentage; echoing the
  // value here ("About 0%") would read as a real selection.
  if (!policy && !isValidSamplingRate(rate)) return null;
  // A cleared custom limit is missing, not zero; an out-of-range one is not a
  // selection either (the API accepts whole numbers from 1 to 1,000,000).
  const limitNumber = Number(limit);
  const limitText =
    limit === "" || limit == null
      ? " The processing limit is not set yet."
      : Number.isInteger(limitNumber) &&
          limitNumber >= 1 &&
          limitNumber <= 1000000
        ? ` Stop after processing ${limitNumber.toLocaleString()} matching rows, not after selecting that many evaluations.`
        : " The processing limit must be a whole number from 1 to 1,000,000.";
  return (
    <Typography
      variant="s1"
      component="div"
      aria-label="Data selection summary"
      sx={{ overflowWrap: "anywhere" }}
    >
      {policy
        ? isSimpleSamplingPolicy(policy)
          ? simpleSelectionText(policy, rowType)
          : advancedSamplingSummary(policy)
        : `${Number(rate) === 100 ? "All" : `About ${rate}% of`} filter-matching ${rowNames(rowType)}.${runType === "historical" ? limitText : ""}`}
    </Typography>
  );
}
TaskSamplingSummary.propTypes = { control: PropTypes.object.isRequired };

export default function TaskSamplingSection({ control, getValues }) {
  const { field } = useController({
    control,
    name: "samplingPolicy",
    defaultValue: null,
  });
  const { field: runTypeField } = useController({ control, name: "runType" });
  const [rowType, samplingRate] = useWatch({
    control,
    name: ["rowType", "samplingRate"],
  });
  const rowRateValid = isValidSamplingRate(samplingRate);
  // Why "Pick whole groups" is unavailable; the field itself shows the error.
  const switchHelpId = useId();
  const runType = runTypeField.value;
  const previousRow = useRef(rowType);
  const previousPolicy = useRef(null);
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(null);
  const [remove, setRemove] = useState(false);
  const policy = field.value;
  const simple = policy && isSimpleSamplingPolicy(policy);
  const switchBlocked = !policy && !rowRateValid;

  useEffect(() => {
    if (previousRow.current !== rowType) {
      if (
        simple &&
        !pickLevels(rowType).includes(policy.sampling_unit) &&
        pickLevels(rowType).length
      ) {
        const unit = pickLevels(rowType)[0];
        field.onChange({ ...policy, sampling_unit: unit });
        setNotice(
          `Pick changed to individual ${rowNames(rowType)} because the previous level was finer than the rows now being evaluated.`,
        );
      } else setNotice("");
      previousRow.current = rowType;
    }
  }, [field, policy, rowType, simple]);

  const enable = () => {
    if (!pickLevels(rowType).length || !rowRateValid) return;
    if (previousPolicy.current) {
      let retained = previousPolicy.current;
      let retainedNotice = "";
      if (
        isSimpleSamplingPolicy(retained) &&
        !pickLevels(rowType).includes(retained.sampling_unit)
      ) {
        retained = { ...retained, sampling_unit: pickLevels(rowType)[0] };
        retainedNotice = `Pick changed to individual ${rowNames(rowType)} because the retained level was finer than the rows now being evaluated.`;
      }
      // Restoring cannot install a live policy under a historical schedule.
      if (retained.mode === "live" && runType !== "continuous")
        setPending({
          kind: "restore",
          policy: retained,
          notice: retainedNotice,
        });
      else {
        field.onChange(retained);
        setNotice(retainedNotice);
      }
      return;
    }
    const next = simpleSamplingPolicy(getValues());
    // Use the task's own time window. Confirm only when it has to change:
    // longer than 31 days (keep its latest part) or nothing of it is past.
    const fit = fitSamplingWindow(getValues("startDate"), getValues("endDate"));
    if (next.mode === "closed" && (!fit || fit.shortened))
      setPending({ kind: "window", policy: next, shortened: Boolean(fit) });
    else {
      field.onChange(next);
      setNotice("");
    }
  };
  const cancelPending = () => setPending(null);
  // Names the mode the radio names, with the numbers it will use.
  const percentageChoice = () => {
    const rate = getValues("samplingRate");
    const limit = Number(getValues("spansLimit"));
    const rows = rowNames(rowType);
    const amount = !isValidSamplingRate(rate)
      ? `a percentage of matching ${rows}`
      : Number(rate) === 100
        ? `all matching ${rows}`
        : `${rate}% of matching ${rows}`;
    const limitText =
      runType === "historical" &&
      Number.isInteger(limit) &&
      limit >= 1 &&
      limit <= 1000000
        ? `, with your processing limit of ${limit.toLocaleString()} rows`
        : "";
    return `Switch to ${amount}${limitText}? Your group settings stay available if you switch back before leaving.`;
  };
  const pendingTitle =
    pending?.kind === "replace"
      ? "Replace advanced sampling settings?"
      : pending?.kind === "restore"
        ? "Restore group picking?"
        : pending?.kind === "historical"
          ? "Use counts for historical data?"
          : pending?.shortened
            ? `Use the last ${SAMPLING_WINDOW_MAX_DAYS} days of your time window?`
            : "Choose a fixed sample window";
  return (
    <Stack spacing={1.5} sx={{ minWidth: 0 }}>
      <RadioGroup
        aria-label="Data selection method"
        value={policy ? "units" : "rows"}
        onChange={(event) =>
          event.target.value === "units" ? enable() : setRemove(true)
        }
      >
        <FormControlLabel
          value="rows"
          control={
            <Radio
              size="small"
              sx={{ "&.Mui-checked": { color: "text.primary" } }}
            />
          }
          label={`A percentage of matching ${rowNames(rowType)}`}
        />
        <FormControlLabel
          value="units"
          disabled={!pickLevels(rowType).length || switchBlocked}
          control={
            <Radio
              size="small"
              sx={{ "&.Mui-checked": { color: "text.primary" } }}
              inputProps={
                switchBlocked ? { "aria-describedby": switchHelpId } : undefined
              }
            />
          }
          label={`Pick whole groups of ${rowNames(rowType)}`}
        />
        {switchBlocked && (
          // Directly under the option it explains, aligned with its label;
          // outside the label so the option's name stays the same.
          <Typography
            id={switchHelpId}
            variant="s3"
            component="div"
            color="text.secondary"
            sx={{ pl: 3, mt: -0.75 }}
          >
            Enter a valid percentage to switch to group picking.
          </Typography>
        )}
      </RadioGroup>
      {notice && (
        <Typography role="status" variant="s1" component="div">
          {notice}
        </Typography>
      )}
      {policy ? (
        <>
          <SamplingPolicyEditor control={control} getValues={getValues} />
          {!simple && (
            <Button
              size="small"
              variant="outlined"
              sx={{ alignSelf: "flex-start" }}
              disabled={!pickLevels(rowType).length}
              onClick={() =>
                setPending({
                  kind: "replace",
                  policy: simpleSamplingPolicy(getValues()),
                })
              }
            >
              Replace with simple group picking
            </Button>
          )}
        </>
      ) : (
        <>
          <SamplingRateField control={control} />
          {runType === "historical" && <RowLimitField control={control} />}
          <TaskSamplingSummary control={control} />
        </>
      )}
      <ClosableDialog
        open={Boolean(pending)}
        onClose={cancelPending}
        maxWidth="sm"
        fullWidth
        aria-labelledby="sample-choice-title"
      >
        <DialogTitle id="sample-choice-title">{pendingTitle}</DialogTitle>
        <DialogContent>
          <Stack spacing={1.5} sx={{ pt: 1 }}>
            {pending?.kind === "replace" ? (
              <Typography variant="s1" component="div">
                This replaces all advanced settings with the simple choices
                shown here. Cancel keeps the entire saved policy unchanged.
              </Typography>
            ) : pending?.kind === "restore" ? (
              <Typography variant="s1" component="div">
                Your retained selection uses new incoming data. Restoring it
                also switches the schedule to New incoming data; every retained
                setting is preserved.
                {isSimpleSamplingPolicy(pending.policy) &&
                  " Or use historical counts and choose a past window instead."}
              </Typography>
            ) : pending?.kind === "historical" ? (
              <Typography variant="s1" component="div">
                Reset each group and All other{" "}
                {pickNames(pending.policy.sampling_unit)} to 100, bounded by
                what exists. Adjust the counts after switching. Choose a past
                window of at most 31 days. Cancel keeps your row percentage and
                retained shares unchanged.
              </Typography>
            ) : pending?.shortened ? (
              <Typography variant="s1" component="div">
                Group picking covers at most {SAMPLING_WINDOW_MAX_DAYS} days, so
                it uses the most recent {SAMPLING_WINDOW_MAX_DAYS} days of your
                time window. Change the dates below to pick from another part of
                it. The time window for percentages is kept for when you switch
                back.
              </Typography>
            ) : (
              <Typography variant="s1" component="div">
                Your time window has nothing in the past to pick from. Pick a
                past window of at most {SAMPLING_WINDOW_MAX_DAYS} days. The time
                window for percentages is kept for when you switch back.
              </Typography>
            )}
            {pending?.kind !== "restore" &&
              pending?.policy.mode === "closed" && (
                <>
                  <Typography
                    variant="s3"
                    component="div"
                    color="text.secondary"
                  >
                    Start with 100 {pickNames(pending.policy.sampling_unit)},
                    bounded by what exists.
                  </Typography>
                  {["start", "end"].map((bound) => (
                    <TextField
                      key={bound}
                      fullWidth
                      type="datetime-local"
                      size="small"
                      label={`New UTC window ${bound}`}
                      InputLabelProps={{ shrink: true }}
                      value={(pending.policy.window[bound] || "").slice(0, 16)}
                      onChange={(event) => {
                        const date = new Date(`${event.target.value}Z`);
                        setPending({
                          ...pending,
                          policy: {
                            ...pending.policy,
                            window: {
                              ...pending.policy.window,
                              [bound]: Number.isFinite(+date)
                                ? date.toISOString()
                                : "",
                            },
                          },
                        });
                      }}
                    />
                  ))}
                </>
              )}
            {pending && samplingWindowInvalid(pending.policy) && (
              <Alert severity="warning">
                Choose a valid past window, longer than zero and no more than 31
                days.
              </Alert>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={cancelPending}>Cancel</Button>
          {pending?.kind === "restore" &&
            isSimpleSamplingPolicy(pending.policy) && (
              <Button
                variant="outlined"
                onClick={() =>
                  setPending({
                    ...pending,
                    kind: "historical",
                    policy: resetHistoricalSamplingCounts(
                      pending.policy,
                      getValues(),
                    ),
                  })
                }
              >
                Use historical counts
              </Button>
            )}
          <Button
            variant="contained"
            disabled={!pending || samplingWindowInvalid(pending.policy)}
            onClick={() => {
              if (
                pending.kind === "restore" &&
                pending.policy.mode === "live"
              ) {
                runTypeField.onChange("continuous");
              }
              field.onChange(pending.policy);
              setNotice(pending.notice || "");
              setPending(null);
            }}
          >
            {pending?.kind === "replace"
              ? "Replace settings"
              : pending?.kind === "restore"
                ? "Restore group picking"
                : pending?.kind === "historical"
                  ? "Use historical counts"
                  : "Use group picking"}
          </Button>
        </DialogActions>
      </ClosableDialog>
      <ClosableDialog
        open={remove}
        onClose={() => setRemove(false)}
        maxWidth="sm"
        fullWidth
        aria-labelledby="row-sampling-title"
      >
        <DialogTitle id="row-sampling-title">
          Use a percentage instead?
        </DialogTitle>
        <DialogContent>
          <Typography variant="s1" component="div">
            {remove && percentageChoice()}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRemove(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={() => {
              previousPolicy.current = policy;
              field.onChange(null);
              setRemove(false);
              setNotice("");
            }}
          >
            Use a percentage
          </Button>
        </DialogActions>
      </ClosableDialog>
    </Stack>
  );
}
TaskSamplingSection.propTypes = {
  control: PropTypes.object.isRequired,
  getValues: PropTypes.func.isRequired,
};
