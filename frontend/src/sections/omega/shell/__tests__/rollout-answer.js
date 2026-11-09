import {
  OMEGA_PART,
  OMEGA_PRODUCTS,
  PRODUCT_LABEL,
  capabilityIdFor,
  partCapabilityId,
} from "../rollout";

// The /api/capabilities/ answer for a workspace admitted to `admitted`, with
// `paused` of those paused, in the server's wire shape (READINESS 3.13).
// `parts` lists the product parts that work today (omega_rollout/readiness.py);
// none by default, as on every deployment until a part ships.
export function capabilitiesAnswer({
  admitted = OMEGA_PRODUCTS,
  paused = [],
  parts = [],
} = {}) {
  const features = {};
  for (const product of OMEGA_PRODUCTS) {
    const isPaused = paused.includes(product) && admitted.includes(product);
    const allowed = admitted.includes(product) && !isPaused;
    let reason = null;
    if (isPaused) reason = "PRODUCT_PAUSED";
    else if (!allowed) reason = "PRODUCT_NOT_ADMITTED";
    features[capabilityIdFor(product)] = {
      display_name: product,
      allowed,
      reason_code: reason,
      requires_network: null,
      oss_baseline: false,
    };
  }
  for (const part of Object.values(OMEGA_PART)) {
    const allowed = parts.includes(part);
    features[partCapabilityId(part)] = {
      display_name: part,
      allowed,
      reason_code: allowed ? null : "PRODUCT_NOT_ADMITTED",
      requires_network: null,
      oss_baseline: false,
    };
  }
  return {
    deployment_flavor: "oss",
    display_mode: "oss",
    license_state: "missing",
    features,
  };
}

// The /omega-rollout/early-access/ listing in the server's wire shape: the
// products this org is admitted to, plus the ones taking waitlist requests
// (`requestable`). Nothing else is listed (READINESS 3.13, RC-01, RC-05).
export function earlyAccessAnswer({
  admitted = OMEGA_PRODUCTS,
  paused = [],
  requestable = [],
  canRequest = true,
} = {}) {
  const products = OMEGA_PRODUCTS.filter(
    (product) => admitted.includes(product) || requestable.includes(product),
  ).map((product) => {
    const isAdmitted = admitted.includes(product);
    let state = null;
    if (isAdmitted) state = paused.includes(product) ? "paused" : "active";
    return {
      product,
      label: PRODUCT_LABEL[product],
      admitted: isAdmitted,
      state,
      request_status: null,
      requested_at: null,
    };
  });
  return { can_request: canRequest, products };
}
