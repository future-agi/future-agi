// USD stays an exact decimal string; no implicit allowance or numeric coercion.
export function spendPolicyInput(value) {
  if (typeof value.periodLimitUsd !== 'string') throw new Error('Supply the allowance as a decimal string.');
  return { revision: value.revision, enabled: value.enabled, period_limit_usd: value.periodLimitUsd };
}
