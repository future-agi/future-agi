/**
 * Self-improvement (the prompt optimizer launched from a run) is in beta: its
 * entry points stay visible but cannot be used. This is the one switch; a real
 * beta flag plugs in here.
 */
export function useSelfImprovementOpen() {
  return false;
}

export const SELF_IMPROVEMENT_BETA_HINT = "In beta, send early access request";
