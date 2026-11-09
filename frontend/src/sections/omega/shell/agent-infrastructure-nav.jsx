import React from "react";
import Label from "src/components/label";
import { DEPLOYMENT_UNAVAILABLE } from "src/sections/omega/shell/switches";
import { paths } from "src/routes/paths";
import { omegaNavIcon } from "./icons";
import { OMEGA_PRODUCT } from "./rollout";

// Future AGI's own sidebar gains its developer entries (Studio, Deployments,
// Sandboxes, as LangSmith does, and the Omega Hub), in one group placed right
// after BUILD. The nav-section components render the
// subheader in title case, like the host's other groups.

export const AGENT_INFRASTRUCTURE_SUBHEADER = "Agent infrastructure";
const BUILD_SUBHEADER = "BUILD";

const ITEMS = [
  {
    title: "Studio",
    path: paths.dashboard.studio,
    icon: omegaNavIcon("studio"),
    product: OMEGA_PRODUCT.STUDIO_TAB,
  },
  {
    title: "Deployments",
    path: paths.dashboard.deployments.root,
    icon: omegaNavIcon("deployments"),
    product: OMEGA_PRODUCT.DEPLOYMENTS,
  },
  {
    title: "Sandboxes",
    path: paths.dashboard.sandboxes.root,
    icon: omegaNavIcon("sandboxes"),
    product: OMEGA_PRODUCT.SANDBOXES,
  },
  {
    title: "Hub",
    path: paths.dashboard.hub.root,
    icon: omegaNavIcon("hub"),
    product: OMEGA_PRODUCT.HUB,
  },
];

// Working entries still require admission (or pause). RL 1808 adds only
// informational rows for explicitly hosted-withheld Sandboxes/Deployments.
export function agentInfrastructureGroup(
  listed = () => true,
  { hostedWithheld = () => false } = {},
) {
  return {
    subheader: AGENT_INFRASTRUCTURE_SUBHEADER,
    items: ITEMS.flatMap(({ product, ...item }) => {
      if (listed(product)) return [item];
      if (
        (product === OMEGA_PRODUCT.SANDBOXES ||
          product === OMEGA_PRODUCT.DEPLOYMENTS) &&
        hostedWithheld(product)
      ) {
        // Deliberate allowlist: no path, child route, or action survives.
        return [
          {
            title: item.title,
            icon: item.icon,
            inert: true,
            info: (
              <Label sx={{ textTransform: "none" }}>
                {DEPLOYMENT_UNAVAILABLE}
              </Label>
            ),
          },
        ];
      }
      return [];
    }),
  };
}

// Returns a new section list with the group inserted after BUILD (or appended
// when BUILD is absent). With the flag off, or with neither an admitted product
// nor hosted-withheld information, return the input untouched.
export function withAgentInfrastructure(
  sections,
  enabled,
  listed = () => true,
  options = {},
) {
  if (!enabled) return sections;
  const group = agentInfrastructureGroup(listed, options);
  if (group.items.length === 0) return sections;
  const next = [...sections];
  const buildIndex = next.findIndex(
    (section) => section?.subheader === BUILD_SUBHEADER,
  );
  const insertAt = buildIndex === -1 ? next.length : buildIndex + 1;
  next.splice(insertAt, 0, group);
  return next;
}
