import { useGatewayConfig } from "../../providers/hooks/useGatewayConfig";
import { guardrailsAreOff } from "../../utils/guardrailPipeline";

// True once this gateway's config says it runs no guardrail check
// (guardrail_pipeline.enabled is false). False while the config loads, when
// it failed to load and when the gateway does not report the pipeline: no
// control is changed on a guess.
export function useGuardrailsOff(gatewayId) {
  return guardrailsAreOff(
    useGatewayConfig(gatewayId, { meta: { errorHandled: true } }),
  );
}
