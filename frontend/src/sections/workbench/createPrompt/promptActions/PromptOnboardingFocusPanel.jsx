import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import {
  isPromptSecondVersionJourneyStep,
  PROMPT_ONBOARDING_MODES,
} from "./promptOnboardingRoute";

const FOCUS_COPY = {
  "create-prompt": {
    title: "Create the first prompt",
    description:
      "Write one focused prompt, pick a model, and run it before saving a baseline.",
    stepNumber: 1,
    steps: ["Prompt content", "Model", "First run"],
    targetTab: "Playground",
  },
  "run-test": {
    title: "Run one prompt test",
    description:
      "Generate one output from this prompt so the workspace has a real result to inspect.",
    stepNumber: 2,
    steps: ["Prompt content", "Model", "Run Prompt"],
    targetTab: "Playground",
  },
  "save-version": {
    title: "Save the prompt baseline",
    description:
      "Commit the tested draft so the next edit has a version to compare against.",
    stepNumber: 3,
    steps: ["Tested draft", "Version note", "Saved baseline"],
    targetTab: "Playground",
  },
  compare: {
    title: "Compare prompt versions",
    description:
      "Open version history, select another version, and review the output difference.",
    stepNumber: 5,
    steps: ["Baseline", "Second version", "Comparison"],
    targetTab: "Playground",
  },
  "add-failure": {
    title: "Capture a failure example",
    description:
      "Add a concrete failing case so the next prompt edit is tied to evidence.",
    stepNumber: 6,
    steps: ["Failure case", "Expected behavior", "Next edit"],
    targetTab: "Evaluation",
  },
  metrics: {
    title: "Review prompt metrics",
    description:
      "Inspect linked-version metrics and decide what needs to improve next.",
    stepNumber: 6,
    steps: ["Linked version", "Metric signal", "Next action"],
    targetTab: "Metrics",
  },
};

const TOTAL_PROMPT_SETUP_STEPS = 6;

const resolveFocusCopy = ({ mode, source }) => {
  if (FOCUS_COPY[mode]) return FOCUS_COPY[mode];
  if (source === "onboarding") return FOCUS_COPY["create-prompt"];
  return null;
};

export default function PromptOnboardingFocusPanel({
  blocker,
  compareNeedsSecondVersion = false,
  currentTab,
  isRunDisabled = false,
  isSaveDisabled = false,
  journeyStep,
  mode,
  onCreateSecondVersion,
  onOpenEvaluation,
  onOpenMetrics,
  onOpenPlayground,
  onOpenSaveVersion,
  onOpenVersionHistory,
  onRunPrompt,
  source,
  tourAnchor,
}) {
  const isCompareMode = mode === "compare";
  const isSecondVersionJourney = isPromptSecondVersionJourneyStep(journeyStep);
  const baseCopy = resolveFocusCopy({ mode, source });
  const copy =
    isCompareMode && compareNeedsSecondVersion
      ? {
          title: "Create a second version",
          description:
            "Edit the prompt, run it, and save one more version before comparing behavior.",
          stepNumber: 4,
          steps: ["Baseline", "Second run", "Second version"],
          targetTab: "Playground",
        }
      : isSecondVersionJourney && mode === PROMPT_ONBOARDING_MODES.RUN_TEST
        ? {
            title: "Run the second version",
            description:
              "Use the same example so the comparison shows the effect of the edit.",
            stepNumber: 4,
            steps: ["Edited prompt", "Second run", "Ready to save"],
            targetTab: "Playground",
          }
        : isSecondVersionJourney &&
            mode === PROMPT_ONBOARDING_MODES.SAVE_VERSION
          ? {
              title: "Save the second version",
              description:
                "Commit the edited version so you can compare it against the baseline.",
              stepNumber: 4,
              steps: ["Second run", "Version note", "Ready to compare"],
              targetTab: "Playground",
            }
          : baseCopy;

  if (!copy) {
    return null;
  }

  const isRunMode = mode === "run-test";
  const isSaveMode = mode === "save-version";
  const isMetricMode = mode === "metrics";
  const isFailureMode = mode === "add-failure";
  const isOnTargetTab = currentTab === copy.targetTab;

  const primaryAction = (() => {
    if (isRunMode && isOnTargetTab) {
      return {
        label: isSecondVersionJourney ? "Run second version" : "Run Prompt",
        onClick: onRunPrompt,
        disabled: isRunDisabled,
      };
    }
    if (isSaveMode) {
      return {
        label: isSecondVersionJourney ? "Save second version" : "Save version",
        onClick: onOpenSaveVersion,
        disabled: isSaveDisabled,
      };
    }
    if (isCompareMode && compareNeedsSecondVersion) {
      return {
        label: "Create second version",
        onClick: onCreateSecondVersion,
      };
    }
    if (isCompareMode) {
      return {
        label: "Open version history",
        onClick: onOpenVersionHistory,
      };
    }
    if (isMetricMode) {
      return {
        label: isOnTargetTab ? "Metrics open" : "Open Metrics",
        onClick: onOpenMetrics,
        disabled: isOnTargetTab,
      };
    }
    if (isFailureMode) {
      return {
        label: isOnTargetTab ? "Evaluation open" : "Open Evaluation",
        onClick: onOpenEvaluation,
        disabled: isOnTargetTab,
      };
    }
    return {
      label: isOnTargetTab ? "Playground open" : "Open Playground",
      onClick: onOpenPlayground,
      disabled: isOnTargetTab && !isRunMode,
    };
  })();

  return (
    <Box
      data-testid="prompt-onboarding-focus"
      sx={{
        mx: 2,
        mb: 1.5,
        border: "1px solid",
        borderColor: "primary.main",
        borderRadius: 1,
        bgcolor: "background.paper",
        p: 1.5,
      }}
    >
      <Stack
        direction={{ xs: "column", md: "row" }}
        spacing={1.5}
        justifyContent="space-between"
        alignItems={{ xs: "flex-start", md: "center" }}
      >
        <Stack spacing={0.75} sx={{ minWidth: 0 }}>
          <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
            <Chip size="small" label="Prompt setup" />
            <Chip size="small" variant="outlined" label={copy.targetTab} />
            {copy.stepNumber ? (
              <Chip
                size="small"
                variant="outlined"
                label={`Step ${copy.stepNumber} of ${TOTAL_PROMPT_SETUP_STEPS}`}
              />
            ) : null}
            {blocker ? (
              <Chip size="small" color="warning" label={blocker} />
            ) : null}
          </Stack>
          <Box>
            <Typography variant="subtitle2">{copy.title}</Typography>
            <Typography variant="body2" color="text.secondary">
              {copy.description}
            </Typography>
          </Box>
          <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
            {copy.steps.map((step, index) => (
              <Chip
                key={step}
                size="small"
                variant={
                  index === copy.steps.length - 1 ? "filled" : "outlined"
                }
                label={step}
              />
            ))}
          </Stack>
        </Stack>
        <Button
          data-tour-anchor={tourAnchor}
          variant="contained"
          disabled={primaryAction.disabled}
          onClick={primaryAction.onClick}
          startIcon={<Iconify icon="mdi:arrow-right" width={18} />}
          sx={{ flexShrink: 0 }}
        >
          {primaryAction.label}
        </Button>
      </Stack>
    </Box>
  );
}

PromptOnboardingFocusPanel.propTypes = {
  blocker: PropTypes.string,
  compareNeedsSecondVersion: PropTypes.bool,
  currentTab: PropTypes.string,
  isRunDisabled: PropTypes.bool,
  isSaveDisabled: PropTypes.bool,
  journeyStep: PropTypes.string,
  mode: PropTypes.string,
  onCreateSecondVersion: PropTypes.func,
  onOpenEvaluation: PropTypes.func,
  onOpenMetrics: PropTypes.func,
  onOpenPlayground: PropTypes.func,
  onOpenSaveVersion: PropTypes.func,
  onOpenVersionHistory: PropTypes.func,
  onRunPrompt: PropTypes.func,
  source: PropTypes.string,
  tourAnchor: PropTypes.string,
};
