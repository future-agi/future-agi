// "Use this" targets (SPEC §7.4). An item shows only when the version's
// checker capability is true AND the receiving lane has shipped, which the
// integrator signals with a Vite flag. The Omega CLI item shows only once a
// customer can install a CLI with `omega hub` (`flags.cli`, the server's
// OMEGA_PART HUB_CLI); with nothing to offer, there is no "Use this" at all.

import { hubCommand } from "./registry";
import { parsePinnedHubRef } from "./pinnedRef";
import { admittedHandoffFlags } from "./useHandoffAdmission";

const envFlag = (key) => {
  try {
    return import.meta.env?.[key] === "1" || import.meta.env?.[key] === "true";
  } catch {
    return false;
  }
};

export function handoffFlags() {
  return {
    openInStudio: envFlag("VITE_OMEGA_HUB_OPEN_STUDIO"),
    deploy: envFlag("VITE_OMEGA_HUB_DEPLOY"),
    consoleTemplate: envFlag("VITE_OMEGA_HUB_CONSOLE"),
  };
}

export function cliCommands(ref, version, apiBase) {
  const pinned = `hub:${ref}${version ? `@${version}` : ""}`;
  return {
    add: hubCommand(["add", pinned], apiBase),
    run: hubCommand(["run", pinned], apiBase),
  };
}

export function useThisTargets({
  ref,
  version,
  capabilities,
  flags = handoffFlags(),
  deploymentsAdmitted = false,
  studioAdmitted = false,
  consoleAdmitted = false,
  consoleEnabled = false,
}) {
  const caps = capabilities || {};
  const admitted = admittedHandoffFlags(flags, {
    deploymentsAdmitted,
    studioAdmitted,
    consoleAdmitted,
    consoleEnabled,
  });
  const pinned = `${ref}@${version}`;
  const query = encodeURIComponent(pinned);
  const targets = [];
  if (flags.cli === true) {
    targets.push({
      key: "cli",
      group: "Omega CLI",
      label: "Install with the Omega CLI",
      kind: "copy",
      commands: cliCommands(ref, version),
    });
  }
  try {
    parsePinnedHubRef(pinned);
  } catch {
    return targets;
  }
  if (caps.openInStudio === true && admitted.openInStudio) {
    targets.push({
      key: "studio",
      group: "Open in Studio",
      label: "Open in Studio",
      kind: "link",
      href: `/dashboard/studio?hub=${query}`,
    });
  }
  // Deploy opens a Deployments route, which renders only when the server
  // admits Deployments (shell/ProductGate.jsx); the caller passes that status
  // as `deploymentsAdmitted`, and anything but `true` leaves Deploy out.
  if (deploymentsAdmitted === true && caps.deploy === true && flags.deploy) {
    targets.push({
      key: "deploy",
      group: "Deploy",
      label: "Deploy",
      kind: "link",
      href: `/dashboard/deployments/new?hub=${query}`,
    });
  }
  if (caps.consoleTemplate === true && admitted.consoleTemplate) {
    targets.push({
      key: "console",
      group: "Start in Omega Console",
      label: "Start in Omega Console",
      kind: "link",
      href: `/dashboard/console?hub=${query}`,
    });
  }
  return targets;
}
