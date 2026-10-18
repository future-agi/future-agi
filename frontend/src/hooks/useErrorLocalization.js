import { useDeploymentMode } from "src/hooks/useDeploymentMode";

// Fails closed: `useDeploymentMode` reports "oss" until the fetch resolves, so
// the control stays hidden until cloud/EE is confirmed rather than flashing.
// Per-license entitlement is separate — the AGENTIC_EVAL capability lock.
export function useErrorLocalizationAvailable() {
  const { isCloud, isEE } = useDeploymentMode();
  return isCloud || isEE;
}
