import { getJob } from './jobs.mjs';
import { requireValue } from '../../lib/skills-r3/canonical.mjs';

/** Load trusted repository code only, never a path from preregistration data. */
export async function createRegisteredDriver({ jobId, runId, registration }) {
  requireValue(process.platform === 'linux', 'Run benchmark code only on the approved cloud host');
  const { integration } = getJob(jobId);
  if (integration.status === 'missing') return null;
  const { createDriver } = await import(new URL(integration.module, import.meta.url).href);
  requireValue(typeof createDriver === 'function', 'Registered driver must export createDriver');
  // An incomplete implementation may produce a failed attempt, never exemption
  // from the runner's complete-stage, independent-oracle or teardown checks.
  return createDriver({ jobId, runId, registration });
}
