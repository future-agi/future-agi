import { useDeploymentMode } from "src/hooks/useDeploymentMode";

/**
 * TH-7177: Error Localization only runs on cloud deployments, so OSS and
 * self-hosted EE builds must not offer it at all (previously the checkbox
 * rendered and runs failed with a generic upgrade message). Fails closed:
 * while deployment info loads the mode defaults to "oss", so the control
 * stays hidden until cloud is confirmed - the same "never flash an
 * unavailable feature" convention the capabilities hooks follow.
 */
export function useErrorLocalizationAvailable() {
  return useDeploymentMode().isCloud;
}
