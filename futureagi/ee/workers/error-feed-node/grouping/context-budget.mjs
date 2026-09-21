import {digest} from './f6/common.mjs';

// One explicitly approved resource-limit transition. Never accept changed
// evidence, membership, model, selection or admission settings as a resume.
export function migrateContextBudget(state, inputs) {
  if (!state || inputs.policy.max_input_bytes !== 240000) return state;
  const binding = digest(inputs);
  if (state.binding === binding) return state;
  const previous = {...inputs.policy, max_input_bytes: 60000};
  delete previous.digest;
  previous.digest = digest(previous);
  const previousBinding = digest({...inputs, policy: previous});
  if (state.binding !== previousBinding) return state;
  return {...state, binding, resource_transitions: [...(state.resource_transitions ?? []), {
    from_binding: previousBinding, to_binding: binding,
    from_max_input_bytes: 60000, to_max_input_bytes: 240000,
    reason: 'User-approved local E2E context allowance increase',
  }]};
}
