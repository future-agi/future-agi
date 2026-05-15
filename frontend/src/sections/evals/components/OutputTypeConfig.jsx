import {
  Box,
  Button,
  Checkbox,
  Chip,
  FormControlLabel,
  IconButton,
  MenuItem,
  Radio,
  RadioGroup,
  Select,
  Slider,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback } from "react";
import Iconify from "src/components/iconify";

const OutputTypeConfig = ({
  outputType,
  onOutputTypeChange,
  choiceScores,
  onChoiceScoresChange,
  passThreshold,
  onPassThresholdChange,
  multiChoice = false,
  onMultiChoiceChange,
  disabled = false,
  // When true, the pass_fail / scoring / deterministic category radio is
  // locked, but everything inside the chosen category (choice labels,
  // scores, pass threshold) remains editable. Used to prevent users from
  // switching the fundamental output shape while still letting them tune it.
  categoryLocked = false,
}) => {
  // Category radio is locked if either the whole component is disabled OR
  // categoryLocked is explicitly set.
  const radioDisabled = disabled || categoryLocked;
  // Add an empty row — user types the label inline
  const handleAddEmptyRow = useCallback(
    (defaultScore = 0.5) => {
      const key = `Choice ${Object.keys(choiceScores || {}).length + 1}`;
      onChoiceScoresChange({ ...(choiceScores || {}), [key]: defaultScore });
    },
    [choiceScores, onChoiceScoresChange],
  );

  const handleRemoveChoice = useCallback(
    (key) => {
      const next = { ...choiceScores };
      delete next[key];
      onChoiceScoresChange(next);
    },
    [choiceScores, onChoiceScoresChange],
  );

  // Rename a choice label (inline editing)
  const handleRenameChoice = useCallback(
    (oldKey, newKey) => {
      const trimmed = newKey.trim();
      if (!trimmed || (trimmed !== oldKey && trimmed in (choiceScores || {})))
        return;
      const entries = Object.entries(choiceScores || {});
      const updated = {};
      for (const [k, v] of entries) {
        updated[k === oldKey ? trimmed : k] = v;
      }
      onChoiceScoresChange(updated);
    },
    [choiceScores, onChoiceScoresChange],
  );

  const handleScoreChange = useCallback(
    (key, value) => {
      const num = parseFloat(value);
      if (!isNaN(num) && num >= 0 && num <= 1) {
        onChoiceScoresChange({ ...choiceScores, [key]: num });
      }
    },
    [choiceScores, onChoiceScoresChange],
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {/* Output Type Selection */}
      <Box>
        <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
          Output Type<span style={{ color: "#d32f2f" }}>*</span>
        </Typography>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ mb: 1, display: "block" }}
        >
          Select your preferred evaluation output format.
        </Typography>
        {radioDisabled && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.75,
              mb: 1,
            }}
          >
            <Iconify
              icon="solar:info-circle-bold"
              width={14}
              height={14}
              sx={{ color: "text.disabled", flexShrink: 0 }}
            />
            <Typography variant="caption" color="text.secondary">
              Output type is fixed for system-level evaluations and can&apos;t
              be changed.
            </Typography>
          </Box>
        )}
        <RadioGroup
          row
          value={outputType}
          onChange={(e) => {
            const val = e.target.value;
            onOutputTypeChange(val);
            // Auto-add a default row if switching to scoring/deterministic with no choices
            if (
              (val === "percentage" || val === "deterministic") &&
              Object.keys(choiceScores || {}).length === 0
            ) {
              onChoiceScoresChange({ "Choice 1": 0.5 });
            }
          }}
          sx={{px:0.25}}
        >
          <FormControlLabel
            value="pass_fail"
            control={<Radio size="small" disabled={radioDisabled} />}
            label="Pass/fail"
          />
          <FormControlLabel
            value="percentage"
            control={<Radio size="small" disabled={radioDisabled} />}
            label="Scoring"
          />
          <FormControlLabel
            value="deterministic"
            control={<Radio size="small" disabled={radioDisabled} />}
            label="Choices"
          />
        </RadioGroup>
      </Box>

      {/* ══════ Scoring mode ══════ */}
      {outputType === "percentage" && (
        <>
          {/* Score mapping */}
          <Box>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ mb: 1, display: "block" }}
            >
              Create a list of predefined categories. Each choice maps to a
              score between 0 and 1.
            </Typography>

            {Object.entries(choiceScores || {}).map(([label, score]) => (
              <Box
                key={label}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1.5,
                  py: 0.75,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <TextField
                  size="small"
                  defaultValue={label}
                  onBlur={(e) => handleRenameChoice(label, e.target.value)}
                  disabled={disabled}
                  placeholder="Choice name"
                  sx={{
                    flex: 1,
                    "& .MuiInputBase-root": { fontSize: "13px", height: 30 },
                  }}
                />
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ whiteSpace: "nowrap" }}
                >
                  Will be shown as
                </Typography>
                <Slider
                  size="small"
                  value={score}
                  onChange={(_, val) => handleScoreChange(label, val)}
                  min={0}
                  max={1}
                  step={0.1}
                  disabled={disabled}
                  valueLabelDisplay="auto"
                  valueLabelFormat={(v) => v.toFixed(1)}
                  sx={{ width: 80 }}
                />
                <Chip
                  label={score.toFixed(1)}
                  size="small"
                  color={
                    score >= 0.7
                      ? "success"
                      : score >= 0.3
                        ? "warning"
                        : "error"
                  }
                  sx={{
                    minWidth: 40,
                    fontSize: "12px",
                    fontWeight: 600,
                    height: 24,
                  }}
                />
                <IconButton
                  size="small"
                  onClick={() => handleRemoveChoice(label)}
                  disabled={disabled}
                >
                  <Iconify icon="solar:trash-bin-trash-bold" width={16} />
                </IconButton>
              </Box>
            ))}

            {/* Add Choice button */}
            <Button
              size="small"
              color="primary"
              startIcon={<Iconify icon="mingcute:add-line" width={14} />}
              onClick={() => handleAddEmptyRow(0.5)}
              disabled={disabled}
              sx={{
                textTransform: "none",
                fontSize: "12px",
                mt: 1,
                alignSelf: "flex-start",
              }}
            >
              Add Choice
            </Button>
          </Box>

          {/* Pass Threshold */}
          <Box>
            <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
              Pass Threshold
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ mb: 1, display: "block" }}
            >
              Set the minimum score required for an item to pass. Accepts values
              between 0 and 1.
            </Typography>
            <Box sx={{ display: "flex", alignItems: "center", gap: 2, px: 1 }}>
              <Typography variant="caption">0</Typography>
              <Slider
                value={passThreshold * 100}
                onChange={(_, val) => onPassThresholdChange(val / 100)}
                min={0}
                max={100}
                size="small"
                valueLabelDisplay="auto"
                valueLabelFormat={(v) => `${Math.round(v)}%`}
                disabled={disabled}
              />
              <Typography variant="caption">100%</Typography>
            </Box>
          </Box>
        </>
      )}

      {/* ══════ Choices mode — label + pass/fail/neutral ══════ */}
      {outputType === "deterministic" && (
        <Box>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ mb: 1, display: "block" }}
          >
            Create a list of predefined categories. Used when multi_choice is
            true. The output score will be in the range of 0-1
          </Typography>

          {/* Choice rows — label + pass/fail/neutral selector */}
          {Object.entries(choiceScores || {}).map(([label, category]) => {
            const catValue =
              typeof category === "number"
                ? category >= 0.7
                  ? "pass"
                  : category <= 0.3
                    ? "fail"
                    : "neutral"
                : category || "neutral";
            return (
              <Box
                key={label}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1.5,
                  py: 0.75,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <TextField
                  size="small"
                  defaultValue={label}
                  onBlur={(e) => handleRenameChoice(label, e.target.value)}
                  disabled={disabled}
                  placeholder="Choice name"
                  sx={{
                    flex: 1,
                    "& .MuiInputBase-root": { fontSize: "13px", height: 30 },
                  }}
                />
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ whiteSpace: "nowrap" }}
                >
                  Will be shown as
                </Typography>
                <Select
                  size="small"
                  value={catValue}
                  disabled={disabled}
                  onChange={(e) => {
                    const map = { pass: 1, neutral: 0.5, fail: 0 };
                    onChoiceScoresChange({
                      ...choiceScores,
                      [label]: map[e.target.value] ?? 0.5,
                    });
                  }}
                  sx={{ minWidth: 120, fontSize: "13px", height: 30 }}
                  renderValue={(val) => (
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                      <Box
                        sx={{
                          width: 10,
                          height: 10,
                          borderRadius: "2px",
                          backgroundColor:
                            val === "pass"
                              ? "success.main"
                              : val === "fail"
                                ? "error.main"
                                : "primary.light",
                        }}
                      />
                      <span>
                        {val === "pass"
                          ? "Pass"
                          : val === "fail"
                            ? "Fail"
                            : "Neutral"}
                      </span>
                    </Box>
                  )}
                >
                  <MenuItem value="pass" sx={{ fontSize: "13px", gap: 1 }}>
                    <Box
                      sx={{
                        width: 10,
                        height: 10,
                        borderRadius: "2px",
                        backgroundColor: "success.main",
                      }}
                    />
                    Pass
                  </MenuItem>
                  <MenuItem value="neutral" sx={{ fontSize: "13px", gap: 1 }}>
                    <Box
                      sx={{
                        width: 10,
                        height: 10,
                        borderRadius: "2px",
                        backgroundColor: "primary.light",
                      }}
                    />
                    Neutral
                  </MenuItem>
                  <MenuItem value="fail" sx={{ fontSize: "13px", gap: 1 }}>
                    <Box
                      sx={{
                        width: 10,
                        height: 10,
                        borderRadius: "2px",
                        backgroundColor: "error.main",
                      }}
                    />
                    Fail
                  </MenuItem>
                </Select>
                <IconButton
                  size="small"
                  onClick={() => handleRemoveChoice(label)}
                  disabled={disabled}
                >
                  <Iconify icon="solar:trash-bin-trash-bold" width={16} />
                </IconButton>
              </Box>
            );
          })}

          {/* Add Choice button */}
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-start",
              mt: 1,
            }}
          >
            <Button
              size="small"
              color="primary"
              startIcon={<Iconify icon="mingcute:add-line" width={14} />}
              onClick={() => handleAddEmptyRow(0.5)}
              disabled={disabled}
              sx={{ textTransform: "none", fontSize: "12px" }}
            >
              Add Choice
            </Button>

            {/* Multi-choice checkbox */}
            <FormControlLabel
              control={
                <Checkbox
                  size="small"
                  checked={multiChoice}
                  onChange={(e) => onMultiChoiceChange?.(e.target.checked)}
                  disabled={disabled}
                />
              }
              label={
                <Typography variant="caption" color="text.secondary">
                  Allow multiple choices (LLM can select more than one)
                </Typography>
              }
              sx={{ mt: 0.5, px:0.25 }}
            />
          </Box>
        </Box>
      )}
    </Box>
  );
};

OutputTypeConfig.propTypes = {
  outputType: PropTypes.oneOf(["pass_fail", "percentage", "deterministic"]),
  onOutputTypeChange: PropTypes.func.isRequired,
  choiceScores: PropTypes.object,
  onChoiceScoresChange: PropTypes.func.isRequired,
  passThreshold: PropTypes.number,
  onPassThresholdChange: PropTypes.func.isRequired,
  multiChoice: PropTypes.bool,
  onMultiChoiceChange: PropTypes.func,
  disabled: PropTypes.bool,
  categoryLocked: PropTypes.bool,
};

export default OutputTypeConfig;
