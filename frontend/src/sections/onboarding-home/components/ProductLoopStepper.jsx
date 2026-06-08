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
  getStageCopy,
  ONBOARDING_PRODUCT_LOOP_STEPS,
  readablePath,
  readableToken,
} from "../onboarding-home.constants";

const STATUS_COPY = {
  complete: {
    label: "Complete",
    color: "success",
    icon: "mdi:check-circle",
  },
  selected: {
    label: "Current",
    color: "primary",
    icon: "mdi:progress-clock",
  },
  not_started: {
    label: "Queued",
    color: "default",
    icon: "mdi:circle-outline",
  },
};

const actionHref = (action) => {
  if (!action || action.blocked || !action.routeAvailable || !action.href) {
    return null;
  }
  return action.href;
};

const actionMetadata = (action) =>
  [
    action?.kind ? readableToken(action.kind) : null,
    action?.estimatedMinutes ? `${action.estimatedMinutes} min` : null,
  ].filter(Boolean);

export default function ProductLoopStepper({
  fallbackAction,
  goal,
  onActionClick,
  primaryPath,
  progress = {},
  recommendedAction,
  stage,
}) {
  const completedCount = ONBOARDING_PRODUCT_LOOP_STEPS.filter(
    (step) => progress[step.id] === "complete",
  ).length;
  const currentStep =
    ONBOARDING_PRODUCT_LOOP_STEPS.find(
      (step) => progress[step.id] === "selected",
    ) ||
    ONBOARDING_PRODUCT_LOOP_STEPS.find(
      (step) => progress[step.id] !== "complete",
    ) ||
    ONBOARDING_PRODUCT_LOOP_STEPS[ONBOARDING_PRODUCT_LOOP_STEPS.length - 1];
  const primaryHref = actionHref(recommendedAction);
  const fallbackHref = actionHref(fallbackAction);
  const nextAction = primaryHref ? recommendedAction : fallbackAction;
  const nextActionHref = primaryHref || fallbackHref;
  const nextActionLabel = primaryHref ? "Next step" : "Alternate setup";
  const nextActionButtonLabel = primaryHref
    ? "Open next step"
    : "Open alternate setup";
  const metadata = actionMetadata(nextAction);
  const stageLabel = stage ? getStageCopy({ stage }).title : null;

  return (
    <Stack data-testid="onboarding-product-loop-stepper" spacing={1.5}>
      <Stack
        direction={{ xs: "column", md: "row" }}
        spacing={1}
        justifyContent="space-between"
        alignItems={{ xs: "flex-start", md: "center" }}
      >
        <Stack spacing={0.25}>
          <Typography variant="subtitle2">Setup progress</Typography>
          <Typography variant="body2" color="text.secondary">
            {completedCount} of {ONBOARDING_PRODUCT_LOOP_STEPS.length} complete
          </Typography>
        </Stack>
        <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
          {primaryPath ? (
            <Chip size="small" label={readablePath(primaryPath)} />
          ) : null}
          {stage ? (
            <Chip size="small" variant="outlined" label={stageLabel} />
          ) : null}
          {goal && !primaryPath ? (
            <Chip
              size="small"
              variant="outlined"
              label={readableToken(goal)}
              sx={{ textTransform: "capitalize" }}
            />
          ) : null}
        </Stack>
      </Stack>

      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: {
            xs: "1fr",
            lg: "minmax(0, 1.6fr) minmax(280px, 0.8fr)",
          },
          gap: 1.5,
          alignItems: "stretch",
        }}
      >
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "1fr",
              sm: "repeat(2, minmax(0, 1fr))",
              md: "repeat(5, minmax(0, 1fr))",
            },
            gap: 1,
          }}
        >
          {ONBOARDING_PRODUCT_LOOP_STEPS.map((step) => {
            const status = progress[step.id] || "not_started";
            const statusCopy = STATUS_COPY[status] || STATUS_COPY.not_started;
            const isCurrent = step.id === currentStep?.id;
            return (
              <Box
                key={step.id}
                data-testid={`product-loop-step-${step.id}`}
                sx={{
                  minHeight: 128,
                  border: "1px solid",
                  borderColor:
                    status === "complete"
                      ? "success.main"
                      : isCurrent
                        ? "primary.main"
                        : "divider",
                  borderRadius: 1,
                  p: 1.25,
                  bgcolor: isCurrent ? "action.hover" : "background.paper",
                }}
              >
                <Stack spacing={0.75}>
                  <Stack
                    direction="row"
                    alignItems="center"
                    justifyContent="space-between"
                    spacing={1}
                  >
                    <Stack direction="row" alignItems="center" spacing={0.75}>
                      <Iconify
                        icon={statusCopy.icon}
                        width={18}
                        sx={{
                          color:
                            statusCopy.color === "success"
                              ? "success.main"
                              : statusCopy.color === "primary"
                                ? "primary.main"
                                : "text.disabled",
                          flexShrink: 0,
                        }}
                      />
                      <Typography variant="subtitle2">{step.label}</Typography>
                    </Stack>
                    <Chip
                      size="small"
                      label={statusCopy.label}
                      color={statusCopy.color}
                      variant={status === "complete" ? "filled" : "outlined"}
                    />
                  </Stack>
                  <Typography variant="body2" color="text.secondary">
                    {step.description}
                  </Typography>
                </Stack>
              </Box>
            );
          })}
        </Box>

        <Box
          data-testid="product-loop-next-action"
          sx={{
            border: "1px solid",
            borderColor: nextActionHref ? "primary.main" : "divider",
            borderRadius: 1,
            p: 2,
            minHeight: 188,
            bgcolor: "background.paper",
          }}
        >
          <Stack spacing={1.25} sx={{ height: "100%" }}>
            <Stack direction="row" justifyContent="space-between" gap={1}>
              <Typography variant="subtitle2">{nextActionLabel}</Typography>
              <Chip
                size="small"
                variant="outlined"
                label={currentStep?.label || "Loop"}
              />
            </Stack>
            <Stack spacing={0.5} sx={{ flex: 1 }}>
              <Typography variant="h6">
                {nextAction?.title || "No action available"}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {nextAction?.description ||
                  "No route is available for this workspace yet."}
              </Typography>
            </Stack>
            {metadata.length ? (
              <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
                {metadata.map((item) => (
                  <Chip
                    key={item}
                    size="small"
                    variant="outlined"
                    label={item}
                    sx={{ textTransform: "capitalize" }}
                  />
                ))}
              </Stack>
            ) : null}
            <Button
              variant="contained"
              component={nextActionHref ? RouterLink : "button"}
              href={nextActionHref || undefined}
              disabled={!nextActionHref}
              onClick={() => onActionClick?.(nextAction)}
              startIcon={<Iconify icon="mdi:arrow-right" width={18} />}
              sx={{ alignSelf: "flex-start" }}
            >
              {nextActionButtonLabel}
            </Button>
          </Stack>
        </Box>
      </Box>
    </Stack>
  );
}

ProductLoopStepper.propTypes = {
  fallbackAction: PropTypes.object,
  goal: PropTypes.string,
  onActionClick: PropTypes.func,
  primaryPath: PropTypes.string,
  progress: PropTypes.object,
  recommendedAction: PropTypes.object,
  stage: PropTypes.string,
};
