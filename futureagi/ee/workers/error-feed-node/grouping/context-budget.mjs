import {digest} from './f6/common.mjs';
import {MAX_EVIDENCE_BYTES} from './request-limits.mjs';

// Explicitly approved resource-limit transitions. Never accept changed
// evidence, membership, model, selection or admission settings as a resume.
export function migrateContextBudget(state, inputs) {
  if (!state || inputs.policy.max_input_bytes !== MAX_EVIDENCE_BYTES) return state;
  const binding = digest(inputs);
  if (state.binding === binding) return state;
  for (const oldLimit of [60000, 240000]) {
    const previous = {...inputs.policy, max_input_bytes: oldLimit};
    delete previous.digest;
    previous.digest = digest(previous);
    const previousBinding = digest({...inputs, policy: previous});
    if (state.binding !== previousBinding) continue;
    return {...state, binding, resource_transitions: [...(state.resource_transitions ?? []), {
      from_binding: previousBinding, to_binding: binding,
      from_max_input_bytes: oldLimit, to_max_input_bytes: MAX_EVIDENCE_BYTES,
      reason: 'Token-counted Gemini context with a separate transport ceiling',
    }]};
  }
  return state;
}
