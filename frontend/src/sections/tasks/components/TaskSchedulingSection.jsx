import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  IconButton,
  Slider,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { useController, useWatch } from "react-hook-form";
import { differenceInDays, differenceInMonths, parseISO } from "date-fns";
import Iconify from "src/components/iconify";
import DateTimeRangePicker from "src/sections/projects/DateTimeRangePicker";

// Map a [startDate, endDate] range to one of DateTimeRangePicker's preset
// option keys, so the picker visually reflects a draft-prefilled time
// window instead of always showing the "12M" default.
function detectDateOption(start, end) {
  if (!start || !end) return "12M";
  try {
    const s = typeof start === "string" ? parseISO(start) : start;
    const e = typeof end === "string" ? parseISO(end) : end;
    if (isNaN(s.getTime()) || isNaN(e.getTime())) return "12M";
    const days = differenceInDays(e, s);
    const months = differenceInMonths(e, s);
    if (days >= 6 && days <= 8) return "7D";
    if (days >= 29 && days <= 31) return "30D";
    if (months >= 2.8 && months <= 3.2) return "3M";
    if (months >= 5.5 && months <= 6.5) return "6M";
    if (months >= 11 && months <= 13) return "12M";
    return "Custom";
  } catch {
    return "12M";
  }
}

// ───────────────────────────────────────────────────────────────
// Run mode option cards (historical / continuous)
// ───────────────────────────────────────────────────────────────
const RUN_MODES = [
  {
    value: "historical",
    icon: "solar:history-linear",
    title: "Historical data",
    description: "Evaluate existing traces from a past time window",
  },
  {
    value: "continuous",
    icon: "solar:play-circle-linear",
    title: "New incoming data",
    description: "Evaluate every new trace as it arrives",
  },
];

const RunModeCard = ({ mode, isActive, onClick }) => {
  const theme = useTheme();
  return (
    <Box
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onClick();
      }}
      sx={{
        flex: 1,
        p: 1.5,
        borderRadius: "8px",
        border: "1px solid",
        borderColor: isActive ? "primary.main" : "divider",
        bgcolor: isActive
          ? alpha(theme.palette.primary.main, 0.08)
          : theme.palette.mode === "dark"
            ? "rgba(255,255,255,0.02)"
            : "rgba(0,0,0,0.01)",
        cursor: "pointer",
        transition: "all 0.15s",
        display: "flex",
        flexDirection: "column",
        gap: 0.5,
        minWidth: 0,
        "&:hover": {
          borderColor: isActive ? "primary.main" : "text.disabled",
          bgcolor: isActive
            ? alpha(theme.palette.primary.main, 0.1)
            : "action.hover",
        },
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
        <Iconify
          icon={mode.icon}
          width={16}
          sx={{ color: isActive ? "primary.main" : "text.secondary" }}
        />
        <Typography
          variant="body2"
          sx={{
            fontSize: "13px",
            fontWeight: 600,
            color: isActive ? "primary.main" : "text.primary",
          }}
        >
          {mode.title}
        </Typography>
        {isActive && (
          <Iconify
            icon="solar:check-circle-bold"
            width={14}
            sx={{ color: "primary.main", ml: "auto" }}
          />
        )}
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
};

RunModeCard.propTypes = {
  mode: PropTypes.object.isRequired,
  isActive: PropTypes.bool.isRequired,
  onClick: PropTypes.func.isRequired,
};

// ───────────────────────────────────────────────────────────────
// Preset chip row (shared by row-limit + sampling)
// ───────────────────────────────────────────────────────────────
const PresetChip = ({ label, isActive, onClick }) => (
  <Box
    onClick={onClick}
    role="button"
    tabIndex={0}
    onKeyDown={(e) => {
      if (e.key === "Enter" || e.key === " ") onClick();
    }}
    sx={(theme) => ({
      px: 1.25,
      py: 0.5,
      borderRadius: "6px",
      border: "1px solid",
      borderColor: isActive ? "primary.main" : "divider",
      bgcolor: isActive
        ? alpha(theme.palette.primary.main, 0.08)
        : "transparent",
      color: isActive ? "primary.main" : "text.secondary",
      fontSize: "12px",
      fontWeight: isActive ? 600 : 500,
      cursor: "pointer",
      userSelect: "none",
      transition: "all 0.15s",
      "&:hover": {
        borderColor: isActive ? "primary.main" : "text.disabled",
        bgcolor: isActive
          ? alpha(theme.palette.primary.main, 0.1)
          : "action.hover",
      },
    })}
  >
    {label}
  </Box>
);

PresetChip.propTypes = {
  label: PropTypes.string.isRequired,
  isActive: PropTypes.bool.isRequired,
  onClick: PropTypes.func.isRequired,
};

// ───────────────────────────────────────────────────────────────
// Row-limit field (preset chips + optional custom)
// ───────────────────────────────────────────────────────────────
const SPANS_PRESETS = [
  { label: "100", value: 100 },
  { label: "1K", value: 1000 },
  { label: "10K", value: 10000 },
  { label: "100K", value: 100000 },
];

const RowLimitField = ({ control }) => {
  const {
    field: { value, onChange },
    fieldState: { error },
  } = useController({ control, name: "spansLimit" });
  const numValue = Number(value) || 0;
  const matchesPreset = SPANS_PRESETS.find((p) => p.value === numValue);
  const [showCustom, setShowCustom] = useState(!matchesPreset && numValue > 0);

  const handleSelectPreset = (presetValue) => {
    onChange(presetValue);
    setShowCustom(false);
  };

  const handleSelectCustom = () => {
    setShowCustom(true);
  };

  return (
    <Box>
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mb: 0.75 }}>
        <Typography
          variant="caption"
          sx={{ fontSize: "12px", fontWeight: 600 }}
        >
          Row limit
        </Typography>
        <Typography
          component="span"
          sx={{ color: "error.main", fontSize: "12px" }}
        >
          *
        </Typography>
      </Box>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ fontSize: "11px", display: "block", mb: 1 }}
      >
        Stop after processing this many matching rows
      </Typography>

      <Box sx={{ display: "flex", gap: 0.75, flexWrap: "wrap" }}>
        {SPANS_PRESETS.map((preset) => (
          <PresetChip
            key={preset.value}
            label={preset.label}
            isActive={!showCustom && numValue === preset.value}
            onClick={() => handleSelectPreset(preset.value)}
          />
        ))}
        <PresetChip
          label="Custom"
          isActive={showCustom}
          onClick={handleSelectCustom}
        />
      </Box>

      {showCustom && (
        <Box sx={{ mt: 1, position: "relative", maxWidth: 200 }}>
          <TextField
            type="number"
            size="small"
            placeholder="e.g. 5000"
            value={value ?? ""}
            onChange={(e) => onChange(e.target.value)}
            fullWidth
            inputProps={{ min: 1, max: 1000000, step: 1 }}
            error={!!error}
            helperText={error?.message}
            sx={{
              "& .MuiInputBase-root": { paddingRight: "36px", height: 36 },
              "& input": { fontSize: "12px" },
            }}
          />
          <Box
            sx={{
              position: "absolute",
              right: 4,
              top: error ? "30%" : "50%",
              transform: "translateY(-50%)",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <IconButton
              size="small"
              onClick={() => onChange((Number(value) || 0) + 100)}
              sx={{ p: 0, width: 16, height: 12 }}
            >
              <Iconify icon="heroicons:chevron-up-20-solid" width={14} />
            </IconButton>
            <IconButton
              size="small"
              onClick={() => onChange(Math.max(1, (Number(value) || 0) - 100))}
              sx={{ p: 0, width: 16, height: 12 }}
            >
              <Iconify icon="heroicons:chevron-down-20-solid" width={14} />
            </IconButton>
          </Box>
        </Box>
      )}

      {error && !showCustom && (
        <Typography
          variant="caption"
          color="error.main"
          sx={{ fontSize: "11px", display: "block", mt: 0.5 }}
        >
          {error.message}
        </Typography>
      )}
    </Box>
  );
};

RowLimitField.propTypes = {
  control: PropTypes.object.isRequired,
};

// ───────────────────────────────────────────────────────────────
// Sampling rate field (preset chips + slider)
// ───────────────────────────────────────────────────────────────
const SAMPLING_PRESETS = [
  { label: "10%", value: 10 },
  { label: "25%", value: 25 },
  { label: "50%", value: 50 },
  { label: "100%", value: 100 },
];

const samplingHelperText = (v) => {
  if (v >= 100) return "Evaluate every matching row";
  if (v >= 50) return `Evaluate about every other matching row (${v}%)`;
  if (v > 0) {
    const nth = Math.round(100 / v);
    return `Evaluate roughly 1 in every ${nth} matching rows`;
  }
  return "";
};

const SamplingRateField = ({ control }) => {
  const {
    field: { value, onChange },
    fieldState: { error },
  } = useController({ control, name: "samplingRate" });
  const numValue = Number(value) || 0;

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          mb: 0.5,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <Typography
            variant="caption"
            sx={{ fontSize: "12px", fontWeight: 600 }}
          >
            Sampling rate
          </Typography>
          <Typography
            component="span"
            sx={{ color: "error.main", fontSize: "12px" }}
          >
            *
          </Typography>
        </Box>
        <Typography
          variant="caption"
          sx={{ fontSize: "12px", fontWeight: 700, color: "primary.main" }}
        >
          {numValue}%
        </Typography>
      </Box>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ fontSize: "11px", display: "block", mb: 1 }}
      >
        What percentage of matching rows to actually evaluate — lower values run
        faster and use less budget
      </Typography>

      <Box sx={{ display: "flex", gap: 0.75, flexWrap: "wrap", mb: 1.5 }}>
        {SAMPLING_PRESETS.map((preset) => (
          <PresetChip
            key={preset.value}
            label={preset.label}
            isActive={numValue === preset.value}
            onClick={() => onChange(preset.value)}
          />
        ))}
      </Box>

      <Box sx={{ px: 0.5 }}>
        <Slider
          value={numValue}
          onChange={(_, v) => onChange(v)}
          min={1}
          max={100}
          step={1}
          valueLabelDisplay="auto"
          size="small"
          sx={{
            "& .MuiSlider-thumb": { width: 14, height: 14 },
            "& .MuiSlider-rail": { opacity: 0.3 },
          }}
        />
      </Box>

      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ fontSize: "11px", display: "block", mt: 0.25 }}
      >
        {samplingHelperText(numValue)}
      </Typography>

      {error && (
        <Typography
          variant="caption"
          color="error.main"
          sx={{ fontSize: "11px", display: "block", mt: 0.5 }}
        >
          {error.message}
        </Typography>
      )}
    </Box>
  );
};

SamplingRateField.propTypes = {
  control: PropTypes.object.isRequired,
};

// ───────────────────────────────────────────────────────────────
// Main section
// ───────────────────────────────────────────────────────────────
const TaskSchedulingSection = ({ control, isEdit = false }) => {
  const { field: runTypeField } = useController({
    control,
    name: "runType",
  });
  const { field: startDateField } = useController({
    control,
    name: "startDate",
  });
  const { field: endDateField } = useController({
    control,
    name: "endDate",
  });

  const runType = useWatch({ control, name: "runType" });
  const [dateOption, setDateOption] = useState(() =>
    detectDateOption(startDateField.value, endDateField.value),
  );

  const dateFilter = [startDateField.value, endDateField.value];
  const handleDateFilterChange = (next) => {
    if (next && next.length === 2) {
      startDateField.onChange(next[0]);
      endDateField.onChange(next[1]);
    }
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {/* ── Run mode cards ── */}
      <Box>
        <Typography
          variant="caption"
          sx={{ fontSize: "12px", fontWeight: 600, display: "block", mb: 0.75 }}
        >
          When to run
        </Typography>
        <Box sx={{ display: "flex", gap: 1 }}>
          {RUN_MODES.map((mode) => (
            <RunModeCard
              key={mode.value}
              mode={mode}
              isActive={runType === mode.value}
              onClick={() => runTypeField.onChange(mode.value)}
            />
          ))}
        </Box>
      </Box>

      {/* ── Historical-only fields ── */}
      {runType === "historical" && (
        <>
          <Box>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.5,
                mb: 0.75,
              }}
            >
              <Typography
                variant="caption"
                sx={{ fontSize: "12px", fontWeight: 600 }}
              >
                Time window
              </Typography>
            </Box>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ fontSize: "11px", display: "block", mb: 1 }}
            >
              Which time range of existing traces to run on
            </Typography>
            <DateTimeRangePicker
              setParentDateFilter={handleDateFilterChange}
              dateOption={dateOption}
              setDateOption={setDateOption}
              dateFilter={dateFilter}
              isEdit={isEdit}
            />
          </Box>

          <RowLimitField control={control} />
        </>
      )}

      {/* ── Sampling rate (both modes) ── */}
      <SamplingRateField control={control} />
    </Box>
  );
};

TaskSchedulingSection.propTypes = {
  control: PropTypes.object.isRequired,
  isEdit: PropTypes.bool,
};

export default TaskSchedulingSection;
