// A key policy saved before the gateway ran key policies is stored but not
// sent to the gateway until an admin saves it again (any edit or toggle).
export const isDormantPolicy = (policy) =>
  Boolean(policy?.is_active) && policy?.runs_on_gateway === false;

export const DORMANT_POLICY_LABEL = "Not running – save to start";
