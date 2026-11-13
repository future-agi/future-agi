import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Radio,
  RadioGroup,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { useController, useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import DateTimeRangePicker from "src/sections/projects/DateTimeRangePicker";
import { formatTimeWindow } from "src/sections/projects/timeWindowPresets";
import {
  isSimpleSamplingPolicy,
  pickNames,
  resetHistoricalSamplingCounts,
  rowNames,
  samplingWindowInvalid,
} from "./simpleSamplingPolicy";

// Retain the Task editor's existing scheduling cards and date picker. The row
// percentage and processing limit now live only in Data to evaluate.
export default function TaskSchedulingSection({ control, isEdit = false }) {
  const theme = useTheme();
  const { field: runType } = useController({ control, name: "runType" });
  const { field: policyField } = useController({
    control,
    name: "samplingPolicy",
  });
  const [pendingLive, setPendingLive] = useState(false);
  const [pendingHistorical, setPendingHistorical] = useState(null);
  const changeSchedule = (next) => {
    if (next === "historical" && policyField.value?.mode === "live") {
      if (isSimpleSamplingPolicy(policyField.value))
        setPendingHistorical(
          resetHistoricalSamplingCounts(policyField.value, {
            rowType,
            startDate: start.value,
            endDate: end.value,
          }),
        );
      return;
    }
    if (
      next === "continuous" &&
      runType.value === "historical" &&
      policyField.value?.mode === "closed" &&
      isSimpleSamplingPolicy(policyField.value)
    )
      setPendingLive(true);
    else runType.onChange(next);
  };
  const { field: start } = useController({ control, name: "startDate" });
  const { field: end } = useController({ control, name: "endDate" });
  const { field: preset } = useController({ control, name: "datePreset" });
  const [policy, rowType] = useWatch({
    control,
    name: ["samplingPolicy", "rowType"],
  });
  const rows = rowNames(rowType);
  const modes = [
    {
      value: "historical",
      icon: "solar:history-linear",
      title: "Historical data",
      description: `Evaluate existing matching ${rows} from a past time window`,
    },
    {
      value: "continuous",
      icon: "solar:play-circle-linear",
      title: "New incoming data",
      description:
        policy?.mode === "closed"
          ? "Continue evaluating late rows from the selected groups"
          : `Evaluate matching ${rows} as they arrive`,
    },
  ];
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <RadioGroup
        row
        aria-label="Execution schedule"
        value={runType.value}
        onChange={(event) => changeSchedule(event.target.value)}
        sx={{ gap: 1 }}
      >
        {modes.map((mode) => {
          const active = runType.value === mode.value;
          const disabled =
            mode.value === "historical" &&
            policy?.mode === "live" &&
            !isSimpleSamplingPolicy(policy);
          return (
            <Box
              key={mode.value}
              component="label"
              sx={{
                flex: 1,
                p: 1.5,
                borderRadius: "8px",
                border: "1px solid",
                borderColor: active ? "primary.main" : "divider",
                bgcolor: active
                  ? alpha(theme.palette.primary.main, 0.08)
                  : theme.palette.mode === "dark"
                    ? "rgba(255,255,255,0.02)"
                    : "rgba(0,0,0,0.01)",
                cursor: disabled ? "not-allowed" : "pointer",
                display: "flex",
                flexDirection: "column",
                gap: 0.5,
                minWidth: 0,
                "&:hover": {
                  borderColor: active ? "primary.main" : "text.disabled",
                  bgcolor: active
                    ? alpha(theme.palette.primary.main, 0.1)
                    : "action.hover",
                },
              }}
            >
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
                <Iconify
                  icon={mode.icon}
                  width={16}
                  sx={{ color: active ? "primary.main" : "text.secondary" }}
                />
                <Typography
                  variant="body2"
                  sx={{
                    fontSize: "12px",
                    fontWeight: 600,
                    color: active ? "primary.main" : "text.primary",
                  }}
                >
                  {mode.title}
                </Typography>
                <Radio
                  value={mode.value}
                  size="small"
                  disabled={disabled}
                  inputProps={{ "aria-label": mode.title }}
                  sx={{ ml: "auto", p: 0 }}
                />
              </Box>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontSize: "11px", lineHeight: 1.4 }}
              >
                {mode.description}
              </Typography>
            </Box>
          );
        })}
      </RadioGroup>
      {policy?.mode === "closed" && runType.value === "continuous" && (
        <Typography variant="caption" color="text.secondary">
          Continue evaluating late rows from picked{" "}
          {pickNames(policy.sampling_unit)}. New groups are not admitted after
          membership freezes at first execution.
        </Typography>
      )}
      {policy?.mode === "live" && (
        <Typography variant="caption" color="text.secondary">
          New incoming data uses percentages, not fixed counts.
        </Typography>
      )}
      {runType.value === "historical" &&
        (policy ? (
          <Typography variant="caption" color="text.secondary">
            The fixed UTC window is set in Data to evaluate.
          </Typography>
        ) : (
          <Box>
            <Typography
              variant="caption"
              sx={{
                fontSize: "12px",
                fontWeight: 600,
                display: "block",
                mb: 0.75,
              }}
            >
              Time window{" "}
              {start.value && end.value && (
                <Box
                  component="span"
                  sx={{ fontSize: "12px", fontWeight: 500, ml: 0.5 }}
                >{`(${formatTimeWindow(start.value, end.value, { isCustom: preset.value === "Custom" })})`}</Box>
              )}
            </Typography>
            <DateTimeRangePicker
              setParentDateFilter={(dates) => {
                if (dates?.length === 2) {
                  start.onChange(dates[0]);
                  end.onChange(dates[1]);
                }
              }}
              dateOption={preset.value}
              setDateOption={preset.onChange}
              dateFilter={[start.value, end.value]}
              isEdit={isEdit}
            />
          </Box>
        ))}
      <Dialog
        open={pendingLive}
        onClose={() => setPendingLive(false)}
        maxWidth="sm"
        fullWidth
        aria-labelledby="incoming-share-title"
      >
        <DialogTitle id="incoming-share-title">
          Use percentages for new incoming data?
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            Fixed counts do not apply to new incoming data. Use 10% for each
            group, then adjust the shares. Cancel keeps your counts and
            Historical data schedule.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPendingLive(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={() => {
              const current = policyField.value;
              if (isSimpleSamplingPolicy(current)) {
                const convert = (rule) => {
                  const next = { ...rule };
                  delete next.target_n;
                  return { ...next, rate: 10 };
                };
                policyField.onChange({
                  ...current,
                  mode: "live",
                  window: { start: current.window.start, end: null },
                  default: convert(current.default),
                  strata: current.strata.map(convert),
                });
                runType.onChange("continuous");
              }
              setPendingLive(false);
            }}
          >
            Use percentages
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog
        open={Boolean(pendingHistorical)}
        onClose={() => setPendingHistorical(null)}
        maxWidth="sm"
        fullWidth
        aria-labelledby="historical-count-title"
      >
        <DialogTitle id="historical-count-title">
          Use counts for historical data?
        </DialogTitle>
        <DialogContent>
          <Box
            sx={{ display: "flex", flexDirection: "column", gap: 1.5, pt: 1 }}
          >
            <Typography variant="body2">
              Reset each group and All other{" "}
              {pickNames(pendingHistorical?.sampling_unit)} to 100, bounded by
              what exists. Adjust the counts after switching. Choose a past
              window of at most 31 days. Cancel keeps your shares and New
              incoming data schedule.
            </Typography>
            {["start", "end"].map((bound) => (
              <TextField
                key={bound}
                fullWidth
                type="datetime-local"
                size="small"
                label={`New UTC window ${bound}`}
                InputLabelProps={{ shrink: true }}
                value={(pendingHistorical?.window[bound] || "").slice(0, 16)}
                onChange={(event) => {
                  const date = new Date(`${event.target.value}Z`);
                  setPendingHistorical((current) => ({
                    ...current,
                    window: {
                      ...current.window,
                      [bound]: Number.isFinite(+date) ? date.toISOString() : "",
                    },
                  }));
                }}
              />
            ))}
            {pendingHistorical && samplingWindowInvalid(pendingHistorical) && (
              <Alert severity="warning">
                Choose a valid past window, longer than zero and no more than 31
                days.
              </Alert>
            )}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPendingHistorical(null)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={
              !pendingHistorical || samplingWindowInvalid(pendingHistorical)
            }
            onClick={() => {
              policyField.onChange(pendingHistorical);
              runType.onChange("historical");
              setPendingHistorical(null);
            }}
          >
            Use historical counts
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
TaskSchedulingSection.propTypes = {
  control: PropTypes.object.isRequired,
  isEdit: PropTypes.bool,
};
