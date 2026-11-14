/**
 * Decides voice vs chat for a call. The call row's own `simulationCallType` is
 * authoritative (a chat sim is "text", matching the product's own drawer
 * routing); when the row omits it we fall back to the run-level `agentType`
 * ("voice"|"text") the KPIs carry.
 * @param {?{ simulationCallType?: ?string }} task
 * @param {?string} agentType
 * @returns {boolean}
 */
export function isVoiceCall(task, agentType) {
  const t = task?.simulationCallType;
  if (t === "text") return false;
  if (t === "voice") return true;
  return agentType === "voice";
}
