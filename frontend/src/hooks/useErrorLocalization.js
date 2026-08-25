import { useDeploymentMode } from "src/hooks/useDeploymentMode";

/**
 * TH-7177: Error Localization is not available on OSS, so OSS builds must
 * not offer it at all (previously the checkbox rendered and runs failed
 * with a generic upgrade message). Cloud and licensed self-hosted EE
 * deployments keep the control; per-license entitlement on those stays
 * with the existing AGENTIC_EVAL capability lock at the render sites.
 * Fails closed: while deployment info loads the mode defaults to "oss",
 * so the control stays hidden until cloud/EE is confirmed - the same
 * "never flash an unavailable feature" convention the capabilities hooks
 * follow.
 */
export function useErrorLocalizationAvailable() {
  const { isCloud, isEE } = useDeploymentMode();
  return isCloud || isEE;
}
