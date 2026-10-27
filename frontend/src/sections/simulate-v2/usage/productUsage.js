/*
  Settings → Usage's product cards, with the simulation meters read from the
  prototype's usage ledger — the same runs, priced the same way, as the
  Simulation section and each run's Cost tab. Every other product card keeps
  its backend data; the three simulation meters add the ledger's usage to
  whatever the backend reported, and are priced at the plan's rates after
  the free tier.
*/
import { RATE_PLAN } from "../_mock/rates";

const C = RATE_PLAN.creditUsd;
const R = RATE_PLAN.rates;

/* Dollars per unit of each simulation meter, at the plan's rates. */
const USD_PER_UNIT = {
  text_sim_tokens: (R.text_sim_tokens.credits / R.text_sim_tokens.per) * C,
  voice_sim_minutes: R.voice_sim_minutes.credits * C,
  ai_credits: C,
};
export const SIM_METERS = Object.keys(USD_PER_UNIT);

/* The free tier first, then the plan's rate — as two tier rows. */
const priceMeter = (dim, usage, months) => {
  const rate = USD_PER_UNIT[dim.key];
  const free = (dim.free_allowance || 0) * months;
  const inFree = Math.min(usage, free);
  const paid = Math.max(0, usage - free);
  const cost = paid * rate;
  return {
    cost,
    tiers: [
      {
        range: `0 – ${Math.round(free).toLocaleString()}`,
        usage: inFree,
        rate: "$0",
        cost: 0,
      },
      {
        range: `${Math.round(free).toLocaleString()}+`,
        usage: paid,
        rate: `$${rate}`,
        cost,
      },
    ],
  };
};

/**
 * The backend overview with its simulation meters fed from the ledger.
 *   meters    simMeters(state, { since }) for the same period
 *   months    months in the period (free tiers are monthly)
 *   projectBy month-to-date → full month factor (1 for whole months)
 */
export const withSimulationUsage = (
  overview,
  meters,
  { months = 1, projectBy = 1 } = {},
) => {
  if (!overview?.dimensions || !meters) return overview;
  let delta = 0;
  const dimensions = overview.dimensions.map((d) => {
    if (!(d.key in USD_PER_UNIT)) return d;
    /* Whole units, as a bill counts them. */
    const usage = Math.round((d.current_usage || 0) + (meters[d.key] || 0));
    const { cost, tiers } = priceMeter(d, usage, months);
    delta += cost - (d.estimated_cost || 0);
    return {
      ...d,
      current_usage: usage,
      current_usage_raw: usage,
      projected_usage: Math.max(d.projected_usage || 0, usage * projectBy),
      estimated_cost: cost,
      tier_breakdown: tiers,
    };
  });
  return {
    ...overview,
    dimensions,
    total_estimated_cost: (overview.total_estimated_cost || 0) + delta,
    total_with_platform: (overview.total_with_platform || 0) + delta,
  };
};

/** A simulation meter's daily series added to the backend's, by date. */
export const mergeSeries = (backend = [], local = []) => {
  const byDate = new Map();
  [...backend, ...local].forEach(({ date, usage }) => {
    const k = String(date).slice(0, 10);
    byDate.set(k, (byDate.get(k) || 0) + (usage || 0));
  });
  return [...byDate.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, usage]) => ({ date, usage }));
};
