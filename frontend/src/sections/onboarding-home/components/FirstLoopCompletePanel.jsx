import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { ObservePanelActions, ObservePanelHeader } from "./observe-panel-utils";
import { readableEvent, readablePath } from "../onboarding-home.constants";

const formatValueSignal = (valueSignal) => {
  if (!valueSignal) return null;
  if (valueSignal.summary) return valueSignal.summary;
  if (Array.isArray(valueSignal.metrics) && valueSignal.metrics.length) {
    return valueSignal.metrics
      .filter((metric) => metric?.label && metric?.value)
      .map((metric) => `${metric.label}: ${metric.value}`)
      .join(" · ");
  }
  const { latencyMs, cost, totalTokens } = valueSignal;
  const parts = [];
  if (typeof latencyMs === "number" && Number.isFinite(latencyMs)) {
    parts.push(`${(latencyMs / 1000).toFixed(1)} s`);
  }
  if (typeof totalTokens === "number" && Number.isFinite(totalTokens)) {
    parts.push(`${totalTokens.toLocaleString("en-US")} tokens`);
  }
  if (typeof cost === "number" && Number.isFinite(cost)) {
    parts.push(`$${cost.toFixed(4)}`);
  }
  return parts.length ? parts.join(" · ") : null;
};

export default function FirstLoopCompletePanel({
  action,
  dailyQualityRoute,
  fallbackAction,
  lastMeaningfulEvent,
  primaryPath,
  onPrimaryClick,
  onFallbackClick,
  onCheckAgain,
  isChecking = false,
  activatedVia = null,
  valueSignal = null,
}) {
  const productPath = primaryPath || action?.analytics?.targetPath;
  const pathLabel = productPath ? readablePath(productPath) : null;
  // When activation is inherited from organization-level setup (a teammate
  // already shipped a workflow here), avoid claiming this user personally
  // completed a loop — show honest, collaborative copy instead.
  const isOrgInherited = activatedVia === "organization";
  // Prefer the real observed value signal (latency/cost/tokens from the
  // reviewed trace) as the "Latest proof". When absent, fall back to the
  // honest activation event label — never fabricate metrics.
  const signalProof = formatValueSignal(valueSignal);
  const proofLabel = lastMeaningfulEvent?.name
    ? readableEvent(lastMeaningfulEvent.name)
    : null;
  const proofText = signalProof || proofLabel;
  const dailyQualityHref = dailyQualityRoute?.isAvailable
    ? dailyQualityRoute.href
    : null;
  const hasDailyQualityRoute = Boolean(dailyQualityHref);

  return (
    <Box
      data-testid="first-loop-complete-panel"
      sx={{
        border: "1px solid",
        borderColor: "success.main",
        borderRadius: 1,
        p: 2,
        bgcolor: "background.paper",
      }}
    >
      <Stack spacing={2}>
        <ObservePanelHeader
          eyebrow={
            isOrgInherited ? "Workspace ready" : "First quality loop complete"
          }
          title={
            isOrgInherited
              ? "Your team already set up this workspace"
              : "Your first workflow is live"
          }
          description={
            isOrgInherited
              ? "Someone on your team already has FutureAGI running here, so we skipped first-time setup. Pick up a quality check or start your own next step."
              : "A product signal is now connected to a repeatable check. Review it, then keep the loop running."
          }
          chips={[pathLabel, isOrgInherited ? "Team setup" : "Complete"].filter(
            Boolean,
          )}
        />
        <Stack spacing={0.5}>
          <Typography variant="subtitle2">Next best step</Typography>
          <Typography variant="body2" color="text.secondary">
            {hasDailyQualityRoute
              ? "Review daily quality next, then open the current loop when a signal needs attention."
              : "Open the current loop next. Daily quality will take over when a reviewable signal is available."}
          </Typography>
        </Stack>
        {proofText ? (
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              p: 1.5,
            }}
          >
            <Typography variant="subtitle2">Latest proof</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {proofText}
            </Typography>
          </Box>
        ) : null}
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
          {hasDailyQualityRoute ? (
            <Button
              variant="contained"
              component={RouterLink}
              href={dailyQualityHref}
              startIcon={<Iconify icon="mdi:calendar-check" width={18} />}
            >
              Review daily quality
            </Button>
          ) : null}
          <ObservePanelActions
            action={action}
            fallbackAction={fallbackAction}
            onPrimaryClick={onPrimaryClick}
            onFallbackClick={onFallbackClick}
            onCheckAgain={onCheckAgain}
            isChecking={isChecking}
            primaryVariant={hasDailyQualityRoute ? "outlined" : "contained"}
          />
          {/* Quality review is a team act — once the first loop is live, offer a
              skippable step to bring in a reviewer (links to the real team
              management surface). */}
          <Button
            variant="text"
            component={RouterLink}
            href={paths.dashboard.manageteam}
            startIcon={<Iconify icon="mdi:account-plus-outline" width={18} />}
            data-testid="invite-reviewer-button"
          >
            Invite a reviewer
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
}

FirstLoopCompletePanel.propTypes = {
  action: PropTypes.object,
  activatedVia: PropTypes.string,
  dailyQualityRoute: PropTypes.object,
  fallbackAction: PropTypes.object,
  isChecking: PropTypes.bool,
  lastMeaningfulEvent: PropTypes.object,
  onCheckAgain: PropTypes.func,
  onFallbackClick: PropTypes.func,
  onPrimaryClick: PropTypes.func,
  primaryPath: PropTypes.string,
  valueSignal: PropTypes.shape({
    kind: PropTypes.string,
    headline: PropTypes.string,
    summary: PropTypes.string,
    metrics: PropTypes.arrayOf(
      PropTypes.shape({
        label: PropTypes.string,
        value: PropTypes.string,
      }),
    ),
    latencyMs: PropTypes.number,
    cost: PropTypes.number,
    totalTokens: PropTypes.number,
  }),
};
