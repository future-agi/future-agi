import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { Stack, Box, Typography } from "@mui/material";
import { experimentCreationSteps, EXPERIMENT_STEP } from "./constants";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { useRunExperimentStoreShallow } from "../states";

/**
 * Configuration → Data → Evaluations → Review & run. Every step is
 * clickable; a visited step shows ✓ when it has no issues, or ⚠ with the
 * issue count (the messages are in the tooltip).
 */
const StepperForExperiment = ({ issues = [] }) => {
  const steps = isSurfaceShown("exp.new")
    ? experimentCreationSteps
    : experimentCreationSteps.slice(0, EXPERIMENT_STEP.REVIEW);
  const { currentStep, setCurrentStep, visitedSteps } =
    useRunExperimentStoreShallow((state) => ({
      currentStep: state.currentStep,
      setCurrentStep: state.setCurrentStep,
      visitedSteps: state.visitedSteps,
    }));

  return (
    <Stack
      direction="row"
      alignItems="center"
      sx={{ flexWrap: "wrap", gap: 2, mt: -1 }}
    >
      {steps.map((section, index) => {
        const isActive = currentStep === index;
        const stepIssues = issues[index] ?? [];
        const visited = Boolean(visitedSteps?.[index]) && !isActive;
        const isDone = visited && stepIssues.length === 0;
        const isWarn = visited && stepIssues.length > 0;

        let marker = index + 1;
        if (isDone)
          marker = (
            <SvgColor
              src="/assets/icons/ic_check.svg"
              sx={{ width: 24, height: 24, color: "background.default" }}
            />
          );
        if (isWarn)
          marker = (
            <Iconify
              icon="mdi:alert"
              width={14}
              sx={{ color: "warning.dark" }}
            />
          );

        return (
          <Box
            key={section.id}
            display="flex"
            alignItems="center"
            gap={1.25}
            data-testid={`experiment-step-${section.id}`}
            sx={{ flexShrink: 0, cursor: "pointer" }}
            onClick={() => setCurrentStep(index)}
          >
            <CustomTooltip
              show={isWarn}
              title={stepIssues.join(" · ")}
              size="small"
              type="black"
              arrow
            >
              <Box
                sx={{
                  width: 24,
                  height: 24,
                  borderRadius: "50%",
                  border: isDone ? "none" : "1px solid",
                  borderColor: isWarn
                    ? "warning.main"
                    : isActive
                      ? "text.primary"
                      : "text.disabled",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontWeight: "fontWeightMedium",
                  typography: "s2",
                  transition: "all 0.2s ease",
                  backgroundColor: isDone
                    ? "green.600"
                    : isWarn
                      ? "orange.o10"
                      : "transparent",
                  color: isActive ? "text.primary" : "text.secondary",
                }}
              >
                {marker}
              </Box>
            </CustomTooltip>
            <Typography
              typography="s1"
              fontWeight="fontWeightMedium"
              color={isActive || visited ? "text.primary" : "text.secondary"}
            >
              {section.title}
              {isWarn ? (
                <Typography
                  component="span"
                  typography="s2"
                  color="warning.dark"
                  sx={{ ml: 0.5 }}
                >
                  {stepIssues.length}
                </Typography>
              ) : null}
            </Typography>
            {index < steps.length - 1 && (
              <SvgColor
                src="/assets/icons/navbar/ic_nav_close_toggle.svg"
                sx={{
                  height: 20,
                  width: 20,
                  opacity: isActive || visited ? 0.8 : 0.3,
                }}
              />
            )}
          </Box>
        );
      })}
    </Stack>
  );
};

StepperForExperiment.propTypes = {
  issues: PropTypes.arrayOf(PropTypes.arrayOf(PropTypes.string)),
};

export default StepperForExperiment;
