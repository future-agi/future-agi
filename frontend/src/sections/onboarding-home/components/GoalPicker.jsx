import React from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import FormControl from "@mui/material/FormControl";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { readablePath, readableToken } from "../onboarding-home.constants";

export default function GoalPicker({
  goals,
  selectedGoal,
  onSelectGoal,
  onSaveGoal,
  skipHref,
  skipLabel,
  isSaving = false,
  error,
  disabled = false,
}) {
  const selectedOption = goals.find((goal) => goal.goal === selectedGoal);
  const errorMessage =
    error?.result?.message || error?.message || error?.detail || null;

  return (
    <Box
      data-testid="onboarding-goal-picker"
      sx={{
        border: "1px solid",
        borderColor: "primary.main",
        borderRadius: 1,
        p: 2,
        bgcolor: "background.paper",
      }}
    >
      <Stack spacing={2}>
        <Stack spacing={0.5}>
          <Typography variant="subtitle2">
            What do you want to improve first?
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Pick the outcome that matters most. We will open the first screen
            for it and keep the next step ready on Home.
          </Typography>
        </Stack>

        {errorMessage ? (
          <Alert severity="warning" sx={{ borderRadius: 1 }}>
            {errorMessage}
          </Alert>
        ) : null}

        <FormControl disabled={disabled || isSaving} fullWidth>
          <RadioGroup
            value={selectedGoal || ""}
            onChange={(event) => {
              const option = goals.find(
                (goal) => goal.goal === event.target.value,
              );
              if (option && !option.disabled) {
                onSelectGoal(option);
              }
            }}
          >
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: { xs: "1fr", md: "repeat(2, 1fr)" },
                gap: 1,
              }}
            >
              {goals.map((goal) => (
                <Box
                  key={goal.id || goal.goal}
                  component="label"
                  sx={{
                    minHeight: 118,
                    border: "1px solid",
                    borderColor:
                      selectedGoal === goal.goal ? "primary.main" : "divider",
                    borderRadius: 1,
                    p: 1.5,
                    cursor: goal.disabled || disabled ? "default" : "pointer",
                    opacity: goal.disabled ? 0.62 : 1,
                    bgcolor:
                      selectedGoal === goal.goal ? "action.hover" : "inherit",
                  }}
                >
                  <Stack spacing={0.75}>
                    <Stack direction="row" alignItems="flex-start" spacing={1}>
                      <Radio
                        size="small"
                        value={goal.goal}
                        disabled={disabled || goal.disabled || isSaving}
                        inputProps={{ "aria-label": goal.label }}
                        sx={{ p: 0.25, mt: 0.25 }}
                      />
                      <Box sx={{ minWidth: 0 }}>
                        <Typography variant="subtitle2">
                          {goal.label}
                        </Typography>
                        <Typography variant="body2" color="text.secondary">
                          {goal.description}
                        </Typography>
                      </Box>
                    </Stack>
                    <Stack direction="row" spacing={0.75} flexWrap="wrap">
                      <Chip
                        size="small"
                        label={readablePath(goal.primaryPath)}
                      />
                      {goal.estimatedMinutes ? (
                        <Chip
                          size="small"
                          variant="outlined"
                          label={`${goal.estimatedMinutes} min`}
                        />
                      ) : null}
                      {goal.disabledReason ? (
                        <Chip
                          size="small"
                          color="default"
                          variant="outlined"
                          label={readableToken(goal.disabledReason)}
                          sx={{ textTransform: "capitalize" }}
                        />
                      ) : null}
                    </Stack>
                  </Stack>
                </Box>
              ))}
            </Box>
          </RadioGroup>
        </FormControl>

        <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
          <Button
            variant="contained"
            onClick={() => onSaveGoal(selectedOption)}
            disabled={
              disabled || isSaving || !selectedOption || selectedOption.disabled
            }
            startIcon={
              isSaving ? (
                <CircularProgress size={16} color="inherit" />
              ) : (
                <Iconify icon="mdi:arrow-right" width={18} />
              )
            }
          >
            Open the first step
          </Button>
          {skipHref ? (
            <Button
              variant="outlined"
              component={RouterLink}
              href={skipHref}
              disabled={isSaving}
            >
              {skipLabel || "Open fallback"}
            </Button>
          ) : null}
        </Stack>
      </Stack>
    </Box>
  );
}

GoalPicker.propTypes = {
  disabled: PropTypes.bool,
  error: PropTypes.object,
  goals: PropTypes.arrayOf(PropTypes.object).isRequired,
  isSaving: PropTypes.bool,
  onSaveGoal: PropTypes.func.isRequired,
  onSelectGoal: PropTypes.func.isRequired,
  selectedGoal: PropTypes.string,
  skipHref: PropTypes.string,
  skipLabel: PropTypes.string,
};
