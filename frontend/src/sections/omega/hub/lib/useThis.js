// "Use this" targets (SPEC §7.4). An item shows only when the version's
// checker capability is true AND the receiving product has shipped, which a
// Vite flag signals. The Omega CLI item shows only once a customer can
// install a CLI with `omega hub` (`flags.cli`, the server's OMEGA_PART
// HUB_CLI); with nothing to offer, there is no "Use this" at all.

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

// The pinned add and run commands. `live` names a model for a live run
// (`--live vendor:model`); the run command then carries it, before the
// registry flag that every command ends with off the hosted default.
export function cliCommands(ref, version, apiBase, live) {
  const pinned = `hub:${ref}${version ? `@${version}` : ""}`;
  const run = live ? ["run", pinned, "--live", live] : ["run", pinned];
  return {
    add: hubCommand(["add", pinned], apiBase),
    run: hubCommand(run, apiBase),
  };
}

// The models a version names for a live run (manifest `models.live`), each
// with the keys it reads, in the manifest's order. Malformed rows are left
// out rather than shown as an empty choice.
export function liveModels(models) {
  const live = Array.isArray(models?.live) ? models.live : [];
  return live
    .filter((row) => row && typeof row.model === "string" && row.model)
    .map((row) => ({
      model: row.model,
      keys: Array.isArray(row.keys)
        ? row.keys.filter((key) => typeof key === "string" && key)
        : [],
    }));
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
