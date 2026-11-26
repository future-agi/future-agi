import { request, type BrowserContext } from '@playwright/test';
import { E2E } from './env';

export type GoogleCase = 'valid' | 'signature' | 'issuer' | 'audience' | 'expired' | 'unknown' | 'rotated';
export interface GoogleEvent { kind: 'authorization' | 'token' | 'jwks'; scenario: string | null }

/** Only the owned TLS provider uses the self-signed fixture certificate. */
export async function googleFixture() {
  const url = new URL(E2E.googleMockUrl);
  if (url.protocol !== 'https:' || url.hostname !== 'localhost') throw new Error('Google fixture must be owned localhost HTTPS');
  const api = await request.newContext({ baseURL: url.origin, ignoreHTTPSErrors: true });
  return {
    async configure(context: BrowserContext, email: string, mode: GoogleCase) {
      const response = await api.post('/scenario', { data: { email, mode } });
      if (response.status() !== 201) throw new Error(`Google fixture scenario refused: ${response.status()}`);
      const { scenario } = await response.json() as { scenario: string };
      await context.addCookies([
        { name: 'e2e_google_scenario', value: scenario, url: 'https://mock-google:28443', secure: true, sameSite: 'Lax' },
      ]);
      return scenario;
    },
    async events(): Promise<GoogleEvent[]> {
      const response = await api.get('/events');
      if (!response.ok()) throw new Error(`Google fixture events unavailable: ${response.status()}`);
      return response.json();
    },
    dispose: () => api.dispose(),
  };
}
