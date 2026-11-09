// Kept in step with the gateway admin contract and tenant.MaxModelFallbacks
// by agentcc/tests/test_model_fallback_bounds.py.
export const MAX_MODEL_FALLBACKS = 15;
export const FALLBACK_LIMIT_MESSAGE = `Up to ${MAX_MODEL_FALLBACKS} fallback models per model`;
