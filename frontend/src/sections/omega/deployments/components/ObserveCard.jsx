import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { buildObserveLink } from "src/sections/omega/shared/observeLinks";

import { deploymentPath } from "../lib/links";
import DeployIcon from "./DeployIcon";

/**
 * Where this deployment's traces land (EE `observe`, PX-OBSERVE-WIRE). EE asks
 * Future AGI for the deployment's own Observe project on every deploy and
 * shows it here once it is linked; the relay forwards the deployment's traces
 * to that project only. So the card shows exactly one of:
 *  - linked: one click to the deployment's traces in Observe (a real link,
 *    same tab, with the way back);
 *  - pending / failing: that traces do not reach Observe, and why. EE's
 *    `linkError.message` names what fixes it (most failures EE retries by
 *    itself; a link Future AGI holds in another workspace cannot move), so
 *    this card adds no promise of its own;
 *  - off: this server does not link deployments to Observe.
 * With no relay at all (`observe` is null) there is nothing to show.
 */
function observeHref(deployment, location = window.location) {
  const observe = deployment?.observe;
  if (!observe || observe.link !== "linked" || !observe.projectId) return null;
  return buildObserveLink({
    projectId: observe.projectId,
    deploymentId: deployment.id,
    deploymentName: deployment.name,
    returnTo: deploymentPath(deployment.id),
    location,
    window: "7d",
  });
}

function Shell({ children, testId }) {
  return (
    <Box
      data-testid={testId}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        p: 2,
        display: "flex",
        flexDirection: { xs: "column", sm: "row" },
        alignItems: { xs: "flex-start", sm: "center" },
        justifyContent: "space-between",
        gap: 1.5,
        minWidth: 0,
      }}
    >
      {children}
    </Box>
  );
}

Shell.propTypes = { children: PropTypes.node, testId: PropTypes.string };

export default function ObserveCard({ deployment }) {
  const observe = deployment?.observe;
  if (!observe) return null;
  const href = observeHref(deployment);

  if (href) {
    return (
      <Shell testId="observe-card">
        <Box sx={{ minWidth: 0 }}>
          <Typography
            typography="s1"
            fontWeight="fontWeightMedium"
            color="text.primary"
          >
            Traces
          </Typography>
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ overflowWrap: "anywhere" }}
          >
            Every run of this deployment lands in its own Observe project,{" "}
            <Box component="span" sx={{ fontFamily: "monospace" }}>
              {observe.projectName}
            </Box>
            .
            {observe.alertsDeferred
              ? " Its two default alerts start once your plan has room for them."
              : ""}
          </Typography>
        </Box>
        <Button
          component={RouterLink}
          href={href}
          variant="outlined"
          color="inherit"
          size="small"
          sx={{ flexShrink: 0 }}
          startIcon={<DeployIcon name="external" size={14} />}
          data-testid="open-in-observe"
        >
          Open in Observe
        </Button>
      </Shell>
    );
  }

  const message =
    observe.link === "off"
      ? "This server does not send deployment traces to Observe. An administrator maps this workspace to Future AGI Observe to turn it on."
      : observe.link === "failing"
        ? `Traces do not reach Observe: ${observe.linkError?.message || "the link to Observe failed."}`
        : "Linking this deployment to its own Observe project. Traces reach Observe once it is linked.";
  return (
    <Shell testId="observe-card">
      <Box sx={{ minWidth: 0 }}>
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          color="text.primary"
        >
          Traces
        </Typography>
        <Typography
          typography="s2"
          color={
            observe.link === "failing" ? "status.warning.fg" : "text.secondary"
          }
          data-testid="observe-link-state"
          data-link={observe.link}
        >
          {message}
        </Typography>
      </Box>
    </Shell>
  );
}

ObserveCard.propTypes = { deployment: PropTypes.object.isRequired };
