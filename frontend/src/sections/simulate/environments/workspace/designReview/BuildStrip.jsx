import PropTypes from "prop-types";
import { Box, CircularProgress, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useDesignReview } from "src/api/simulate-environments/designReview";
import { BUILD_COPY } from "./designReview.constants";
import { approvedBuildSteps, plannedScenarios } from "./designReview.utils";

const kept = (items) => (items || []).filter((item) => !item.removed);

function StepIcon({ state }) {
  if (state === "done")
    return (
      <Iconify
        icon="solar:check-circle-bold"
        width={14}
        sx={{ color: "status.success.fg", flexShrink: 0 }}
      />
    );
  if (state === "running")
    return <CircularProgress size={12} thickness={5} sx={{ flexShrink: 0 }} />;
  return (
    <Box
      sx={{
        width: 12,
        height: 12,
        borderRadius: "50%",
        border: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
      }}
    />
  );
}

StepIcon.propTypes = { state: PropTypes.string.isRequired };

export default function BuildStrip({ envId, stage }) {
  const { data: review } = useDesignReview(envId);
  const counts = {
    tables: kept(review?.tables).length,
    tools: kept(review?.tools).length,
    rules: kept(review?.rules).length,
    scenarios: review ? plannedScenarios(kept(review.use_cases)) : 0,
  };
  const steps = approvedBuildSteps(stage);
  const finished = steps.every((step) => step.state === "done");
  return (
    <Box
      role="status"
      aria-live="polite"
      sx={{
        mx: 2,
        mt: 1.5,
        px: 1.5,
        py: 1,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
      }}
    >
      <Typography sx={{ typography: "s1", fontWeight: 500, mb: 0.75 }}>
        {finished ? BUILD_COPY.done : BUILD_COPY.title}
      </Typography>
      <Stack
        direction="row"
        flexWrap="wrap"
        sx={{ columnGap: 2.5, rowGap: 0.5 }}
      >
        {steps.map((step) => (
          <Stack
            key={step.kind}
            direction="row"
            alignItems="center"
            spacing={0.75}
            aria-label={`${BUILD_COPY.step[step.kind](counts)}: ${BUILD_COPY.stateLabel[step.state]}`}
          >
            <StepIcon state={step.state} />
            <Typography
              sx={{
                typography: "s1",
                color:
                  step.state === "pending" ? "text.tertiary" : "text.primary",
              }}
            >
              {BUILD_COPY.step[step.kind](counts)}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}

BuildStrip.propTypes = {
  envId: PropTypes.string.isRequired,
  stage: PropTypes.string,
};
