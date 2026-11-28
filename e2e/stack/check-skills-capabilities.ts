// The stack flag must be effective before any inventory or campaign is created.
import { request } from '@playwright/test';
import { E2E } from '../lib/env';
import { provisionActor } from '../lib/provisioning';

export default async function checkSkillsCapabilities() {
  const req = await request.newContext({ baseURL: E2E.apiUrl });
  try {
    const actor = await provisionActor(req, 'skills-mode');
    const capability = await actor.api.get<{ result: { campaigns_enabled: boolean } }>(
      '/model-hub/skills/contexts/capabilities/',
    );
    const expected = process.env.SKILLS_COHORT?.startsWith('campaign-');
    if (capability.result.campaigns_enabled !== expected) throw new Error('Wrong capability');
  } catch {
    throw new Error('Skills capability preflight failed; check the scoped actor and campaign mode');
  } finally {
    await req.dispose();
  }
}
