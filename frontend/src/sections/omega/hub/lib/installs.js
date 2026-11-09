import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { SORTS } from "../constants";

import { handoffFlags } from "./useThis";
import { admittedHandoffFlags } from "./useHandoffAdmission";

// Installs and opens are counted only through a real way in (SPEC §6.2;
// omega_hub/services/counts.py): an install is a bundle download by the Omega
// CLI (INSTALL_SOURCES "cli"), an open is a signed-in "Use this" handoff to
// Studio, Deploy or the Console (OPEN_SOURCES). While neither exists every
// count is zero, so the Hub names no installs and no opens anywhere: no count,
// no "Most installed" sort, no "it still installs" (HIDE-UNFINISHED-FA round
// 2; round 1 hid only the entry page's panel). A count the server did see is
// still shown, so a real number is never hidden.
//
// Every Hub file whose copy says "install" or "opened in" reads one of these;
// `sections/omega/__tests__/no-install-copy.test.js` holds the Hub to it.

/** True once a customer can install entries: the CLI has `omega hub`. */
export function useInstallPath() {
  return useOmegaPart(OMEGA_PART.HUB_CLI);
}

/** Whether to show an installs count: there is an install path, or one was counted. */
export const showsInstalls = (installPath, count) =>
  Boolean(installPath) || Number(count) > 0;

/** True once any "Use this" handoff that counts an open has shipped. */
export const hasOpenPath = (flags = handoffFlags(), admission = {}) =>
  Object.values(admittedHandoffFlags(flags, admission)).some(Boolean);

/** Whether to show an opens count: an admitted handoff exists, or an open was counted. */
export const showsOpens = (count, flags = handoffFlags(), admission = {}) =>
  hasOpenPath(flags, admission) || Number(count) > 0;

/** The Hub home's sorts: "Most installed" only with an install path. */
export const sortsFor = (installPath) =>
  installPath ? SORTS : SORTS.filter((sort) => sort.value !== "installs");

/** A `?sort=` value the page offers; an old `?sort=installs` link reads the default. */
export const offeredSort = (sort, installPath) =>
  sortsFor(installPath).some((option) => option.value === sort)
    ? sort
    : undefined;
