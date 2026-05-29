import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import {
  canOpenSample,
  hasSampleRoute,
  isSampleHidden,
} from "../activation-state-utils";
import { readableToken } from "../onboarding-home.constants";

export default function SampleProjectPanel({
  sampleProject,
  activationStage,
  selectedGoal,
  onOpenSample,
  onHideSample,
  onConnectRealData,
  isOpening = false,
  isHiding = false,
}) {
  if (
    !sampleProject ||
    isSampleHidden(sampleProject) ||
    sampleProject.status === "unavailable"
  ) {
    return null;
  }

  const hasRoute = hasSampleRoute(sampleProject);
  const canOpen = canOpenSample(sampleProject);
  const realSetupHref =
    sampleProject.realSetupHref ||
    "/dashboard/observe?setup=true&source=onboarding";
  const statusLabel =
    sampleProject.status === "not_created"
      ? "Ready to create"
      : readableToken(sampleProject.status);
  const proofPoints = [
    {
      label: "Trace context",
      value: "Input, output, latency, cost",
    },
    {
      label: "Quality issue",
      value: "A reviewable failure signal",
    },
    {
      label: "Next action",
      value: "Turn it into an evaluator",
    },
  ];

  return (
    <Box
      data-testid="sample-project-panel"
      sx={{
        border: "1px solid",
        borderColor: "primary.main",
        borderRadius: 1,
        p: 2,
        bgcolor: "action.hover",
      }}
    >
      <Stack spacing={1.5}>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          alignItems={{ xs: "flex-start", sm: "center" }}
          justifyContent="space-between"
        >
          <Stack direction="row" spacing={0.75} alignItems="center">
            <Chip size="small" color="primary" label="Fastest path to Aha" />
            <Chip size="small" label={sampleProject.label || "Sample"} />
            <Chip size="small" variant="outlined" label={statusLabel} />
          </Stack>
          <Typography variant="caption" color="text.secondary">
            {selectedGoal
              ? readableToken(selectedGoal)
              : readableToken(activationStage)}
          </Typography>
        </Stack>

        <Stack spacing={0.5}>
          <Typography variant="h6">Preview the quality loop first</Typography>
          <Typography variant="body2" color="text.secondary">
            Open a seeded trace, inspect the quality issue, then connect real
            observability with the product shape already clear.
          </Typography>
        </Stack>

        <Box
          data-testid="sample-project-aha-preview"
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: "repeat(3, 1fr)" },
            gap: 1,
          }}
        >
          {proofPoints.map((point) => (
            <Box
              key={point.label}
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                bgcolor: "background.paper",
                p: 1,
                minHeight: 72,
              }}
            >
              <Typography variant="subtitle2">{point.label}</Typography>
              <Typography variant="caption" color="text.secondary">
                {point.value}
              </Typography>
            </Box>
          ))}
        </Box>

        {sampleProject.status === "partially_ready" && !hasRoute ? (
          <Typography variant="body2" color="text.secondary">
            The sample trace is not ready. Connect real observability to
            continue.
          </Typography>
        ) : null}

        <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
          <Button
            variant="contained"
            onClick={onOpenSample}
            disabled={!canOpen || isOpening}
            startIcon={<Iconify icon="mdi:flask-outline" width={18} />}
            sx={{ alignSelf: { xs: "stretch", sm: "flex-start" } }}
          >
            {isOpening ? "Opening..." : "Open sample trace"}
          </Button>
          <Button
            variant="text"
            component={RouterLink}
            href={realSetupHref}
            onClick={onConnectRealData}
            startIcon={<Iconify icon="mdi:connection" width={18} />}
            sx={{ alignSelf: { xs: "stretch", sm: "flex-start" } }}
          >
            Connect real observability
          </Button>
          <Button
            variant="text"
            color="inherit"
            onClick={onHideSample}
            disabled={isHiding}
            startIcon={<Iconify icon="mdi:close" width={18} />}
            sx={{ alignSelf: { xs: "stretch", sm: "flex-start" } }}
          >
            Hide sample
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
}

SampleProjectPanel.propTypes = {
  activationStage: PropTypes.string,
  isHiding: PropTypes.bool,
  isOpening: PropTypes.bool,
  onConnectRealData: PropTypes.func,
  onHideSample: PropTypes.func,
  onOpenSample: PropTypes.func,
  sampleProject: PropTypes.object,
  selectedGoal: PropTypes.string,
};
