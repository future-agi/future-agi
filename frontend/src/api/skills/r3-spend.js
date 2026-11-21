import axios from 'src/utils/axios';
import { apiPath, getContractedApiMethods } from 'src/api/contracts/api-surface';
import { idempotencyHeaders, skillV11Result, useSkillV11Mutation, useSkillV11Query } from './testing';

export const spendPolicyReady = () => ['get', 'put'].every((method) => getContractedApiMethods('/model-hub/skills/spend-policy/').includes(method));
const endpoint = () => apiPath('/model-hub/skills/spend-policy/');
export const mapSpendSummary = (value) => ({
  revision: value.revision, enabled: value.enabled, periodLimitUsd: value.period_limit_usd,
  startsAt: value.starts_at, endsAt: value.ends_at, committedUsd: value.committed_usd,
  remainingUsd: value.remaining_usd, knownEstimateUsd: value.known_estimate_usd,
  unknownReservedUsd: value.unknown_reserved_usd, priorPeriodUnknownUsd: value.prior_period_unknown_usd,
  activeSenders: value.active_senders, commercialBasis: value.commercial_basis,
  simulationEntitlements: value.simulation_entitlements,
});
export function useSkillsSpendPolicy() {
  return useSkillV11Query({ parts: ['spendPolicy'], url: endpoint, mapper: mapSpendSummary, enabled: spendPolicyReady() });
}
export function useUpdateSkillsSpendPolicy() {
  return useSkillV11Mutation({
    send: async ({ idempotencyKey, ...input }, config) => mapSpendSummary(skillV11Result(await axios.put(endpoint(), spendPolicyInput(input), { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }))),
    invalidate: () => [['spendPolicy']], refreshOnError: () => [['spendPolicy']],
  });
}
// USD stays an exact decimal string; no implicit allowance or numeric coercion.
export function spendPolicyInput(value) {
  if (typeof value.periodLimitUsd !== 'string') throw new Error('Supply the allowance as a decimal string.');
  return { revision: value.revision, enabled: value.enabled, period_limit_usd: value.periodLimitUsd };
}
