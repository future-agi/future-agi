import { isOmegaConsoleEnabled } from "src/sections/omega/shell/flags";
import {
  OMEGA_PRODUCT,
  PRODUCT_STATUS,
} from "src/sections/omega/shell/rollout";
import { useOmegaProductStatus } from "src/sections/omega/shell/useOmegaProducts";

/** Product status does NOT include the master switch; keep it explicit. */
export function useHandoffAdmission() {
  const studio = useOmegaProductStatus(OMEGA_PRODUCT.STUDIO_TAB);
  const deployments = useOmegaProductStatus(OMEGA_PRODUCT.DEPLOYMENTS);
  const console = useOmegaProductStatus(OMEGA_PRODUCT.CONSOLE);
  return {
    studioAdmitted: studio === PRODUCT_STATUS.ADMITTED,
    deploymentsAdmitted: deployments === PRODUCT_STATUS.ADMITTED,
    consoleAdmitted: console === PRODUCT_STATUS.ADMITTED,
    consoleEnabled: isOmegaConsoleEnabled(),
  };
}

/** Shared by target links and opens counts; counts do not invent capability support. */
export function admittedHandoffFlags(flags, admission = {}) {
  return {
    openInStudio:
      Boolean(flags?.openInStudio) &&
      admission.studioAdmitted === true &&
      admission.consoleEnabled === true,
    deploy: Boolean(flags?.deploy) && admission.deploymentsAdmitted === true,
    consoleTemplate:
      Boolean(flags?.consoleTemplate) &&
      admission.consoleAdmitted === true &&
      admission.consoleEnabled === true,
  };
}
