import SvgColor from "src/components/svg-color";
import { Stack, Box, Typography } from "@mui/material";
import { experimentCreationSteps } from "./common";
import { useRunExperimentStoreShallow } from "../states";

const StepperForExperiment = () => {
  const { currentStep, setCurrentStep, validatedSteps, mode } =
    useRunExperimentStoreShallow((state) => ({
      currentStep: state.currentStep,
      setCurrentStep: state.setCurrentStep,
      validatedSteps: state.validatedSteps,
      mode: state.mode,
    }));
  const finalizedSteps =
    mode === "create"
      ? experimentCreationSteps
      : experimentCreationSteps.slice(1);

  return (
    <Stack
      direction="row"
      alignItems="center"
      sx={{
        flexWrap: "wrap",

        borderRadius: 2,
        gap: 3,
        mt: -1,
      }}
    >
      {finalizedSteps.map((section, index) => {
        const isActive = currentStep === index;
        const isValidated = validatedSteps[index];

        return (
          <Box
            key={section.id}
            display="flex"
            alignItems="center"
            gap={2}
            sx={{
              flexShrink: 0,
              cursor:
                isValidated || index === currentStep ? "pointer" : "default",
              opacity: isValidated || index === currentStep ? 1 : 0.5,
            }}
            onClick={() => {
              if (isValidated || index === currentStep) setCurrentStep(index);
            }}
          >
            {/* Step Number or Check */}
            <Box
              sx={{
                width: 24,
                height: 24,
                borderRadius: "50%",
                border: isValidated ? "none" : "1px solid",
                borderColor: isActive ? "text.primary" : "text.secondary",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontWeight: "fontWeightMedium",
                transition: "all 0.2s ease",
                backgroundColor: isValidated
                  ? "status.success.solid"
                  : "transparent",
                color:
                  isActive || isValidated ? "text.primary" : "text.secondary",
              }}
            >
              {isValidated ? (
                <SvgColor
                  src="/assets/icons/ic_check.svg"
                  sx={{ width: 24, height: 24, color: "background.default" }}
                />
              ) : (
                index + 1
              )}
            </Box>

            {/* Step Title */}
            <Typography
              typography="s1"
              fontWeight="fontWeightMedium"
              color={
                isActive || isValidated ? "text.primary" : "text.secondary"
              }
            >
              {section.title}
            </Typography>

            {/* Connector */}
            {index < finalizedSteps.length - 1 && (
              <SvgColor
                src="/assets/icons/navbar/ic_nav_close_toggle.svg"
                sx={{
                  height: 25,
                  width: 25,
                  transition: "opacity 0.2s ease",
                  opacity:
                    currentStep === index || validatedSteps[index] ? 1 : 0.3,
                }}
              />
            )}
          </Box>
        );
      })}
    </Stack>
  );
};

export default StepperForExperiment;
