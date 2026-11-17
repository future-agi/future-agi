// W00's real managed request probe. Uses ONLY the key minted by the console;
// never enables credits or adds a provider/config/version to the fresh org.
import { request } from '@playwright/test';
import { randomBytes } from 'node:crypto';
import { E2E } from '../env';
import { MockUpstream } from '../upstream';
import { CAT2_IDS, cat2CreditsRefused, type Cat2CreditsRefusal } from './census-contract';

export async function censusCreditsRefusals(rawKey: string): Promise<Cat2CreditsRefusal[]> {
  if (!rawKey || !['localhost', '127.0.0.1', '[::1]'].includes(new URL(E2E.gatewayUrl).hostname)) {
    throw new Error('CAT2_CREDITS_LOCAL_KEY_REQUIRED');
  }
  const ctx = await request.newContext({ timeout: 10_000 });
  const mock = new MockUpstream();
  const probe = `e2e-census-${randomBytes(12).toString('hex')}`;
  const rows: Cat2CreditsRefusal[] = [];
  try {
    for (const id of CAT2_IDS) {
      const upstreamBefore = await mock.inferenceCount(probe);
      // gateway.e2e.yaml:41-52 pins fagi-managed as the credits provider. An
      // explicit prefix disambiguates the vendor part of the catalogue ID.
      const response = await ctx.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
        headers: { Authorization: `Bearer ${rawKey}` },
        data: { model: `fagi-managed/${id}`, messages: [{ role: 'user', content: probe }] },
      });
      const body = await response.json() as { error?: { code?: string } };
      const row: Cat2CreditsRefusal = { id, verdict: 'refused', status: response.status(), code: body.error?.code ?? '',
        upstreamBefore, upstreamAfter: await mock.inferenceCount(probe) };
      // Never include response bodies, prompts, keys or org IDs in an error.
      if (!cat2CreditsRefused(row)) throw new Error(`CAT2_CREDITS_NOT_REFUSED: ${id}: status=${row.status} upstream=${row.upstreamAfter}`);
      rows.push(row);
    }
    return rows;
  } finally { await ctx.dispose(); }
}
