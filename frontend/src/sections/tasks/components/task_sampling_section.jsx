import PropTypes from "prop-types";
import { useEffect, useId, useRef, useState } from "react";
import {
  Alert,
  Button,
  Dialog,
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
import SamplingPolicyEditor from "./SamplingPolicyEditor";
import { RowLimitField, SamplingRateField } from "./task_sampling_fields";
import {
  advancedSamplingSummary,
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
      variant="body2"
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
    const originalWindow = {
      ...next,
      window: { start: getValues("startDate"), end: getValues("endDate") },
    };
    if (next.mode === "closed" && samplingWindowInvalid(originalWindow))
      setPending({ kind: "window", policy: next });
    else {
      field.onChange(next);
      setNotice("");
    }
  };
  const cancelPending = () => setPending(null);
  const pendingTitle =
    pending?.kind === "replace"
      ? "Replace advanced sampling settings?"
      : pending?.kind === "restore"
        ? "Restore group picking?"
        : pending?.kind === "historical"
          ? "Use counts for historical data?"
          : "Choose a fixed sample window";
  return (
    <Stack spacing={1.5} sx={{ minWidth: 0 }}>
      <Typography variant="caption" color="text.secondary">
        Filters define which data matches. Choose how much of it to evaluate.
      </Typography>
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
      </RadioGroup>
      {notice && (
        <Typography role="status" variant="body2">
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
          {switchBlocked && (
            <Typography
              id={switchHelpId}
              variant="caption"
              color="text.secondary"
            >
              Enter a valid percentage to switch to group picking.
            </Typography>
          )}
          {runType === "historical" && <RowLimitField control={control} />}
          <TaskSamplingSummary control={control} />
        </>
      )}
      <Dialog
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
              <Typography variant="body2">
                This replaces all advanced settings with the simple choices
                shown here. Cancel keeps the entire saved policy unchanged.
              </Typography>
            ) : pending?.kind === "restore" ? (
              <Typography variant="body2">
                Your retained selection uses new incoming data. Restoring it
                also switches the schedule to New incoming data; every retained
                setting is preserved.
                {isSimpleSamplingPolicy(pending.policy) &&
                  " Or use historical counts and choose a past window instead."}
              </Typography>
            ) : pending?.kind === "historical" ? (
              <Typography variant="body2">
                Reset each group and All other{" "}
                {pickNames(pending.policy.sampling_unit)} to 100, bounded by
                what exists. Adjust the counts after switching. Choose a past
                window of at most 31 days. Cancel keeps your row percentage and
                retained shares unchanged.
              </Typography>
            ) : (
              <Typography variant="body2">
                Pick a past window of at most 31 days. Your percentage
                selection’s time range is retained, not truncated.
              </Typography>
            )}
            {pending?.kind !== "restore" &&
              pending?.policy.mode === "closed" && (
                <>
                  <Typography variant="caption" color="text.secondary">
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
      </Dialog>
      <Dialog
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
          <Typography variant="body2">
            This replaces group picking with your retained{" "}
            {getValues("samplingRate")}% and historical processing limit. Your
            group settings stay available if you switch back before leaving.
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
            Use row percentage
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
TaskSamplingSection.propTypes = {
  control: PropTypes.object.isRequired,
  getValues: PropTypes.func.isRequired,
};
