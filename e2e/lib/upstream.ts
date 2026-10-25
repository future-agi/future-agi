// MockUpstream: e2e/stack/mock-llm/server.mjs's scoped routes as an org
// provider's upstream. An org key reaches only the providers its org configured,
// and the product refuses a private base URL (Django url_safety, the gateway's
// netguard) unless AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS, which the stacks keep off;
// so the provider's base URL is the mock on the public-classified e2e-public
// network (llm.e2e-public.test), with the product's SSRF guards at their defaults.
import { E2E } from './env';
import { PUBLIC_NETWORK, SCOPE_NAME } from './public-network';

export interface RecordedRequest {
  at: string; method: string; path: string; headers: Record<string, string>; body: unknown;
}

export class MockUpstream {
  constructor(private readonly readUrl: string = E2E.mockLlmUrl) {}

  /** A provider base_url (save it with extra_config.api_path_prefix '/v1'). Without
   *  options the scenario echoes exactly like the unscoped mock (usage 7/7/14). */
  baseUrl(scenario: string, o: { latencyMs?: number; status?: number } = {}): string {
    if (!SCOPE_NAME.test(scenario)) throw new Error(`scenario ${JSON.stringify(scenario)} is not [A-Za-z0-9_.-]{1,80}`);
    const directives: string[] = [];
    if (o.latencyMs !== undefined) {
      if (!(Number.isInteger(o.latencyMs) && o.latencyMs >= 0 && o.latencyMs <= 120_000)) throw new Error('latencyMs is 0-120000');
      directives.push(`lat=${o.latencyMs}`);
    }
    if (o.status !== undefined) {
      if (!(Number.isInteger(o.status) && o.status >= 400 && o.status <= 599)) throw new Error('status is 400-599');
      directives.push(`status=${o.status}`);
    }
    return `http://${PUBLIC_NETWORK.pinned['mock-llm'].alias}:8080/s/${scenario}/${directives.join(',') || '-'}`;
  }

  /** Every request the scenario received, oldest first. */
  async requests(scenario: string): Promise<RecordedRequest[]> {
    if (!SCOPE_NAME.test(scenario)) throw new Error(`scenario ${JSON.stringify(scenario)} is not [A-Za-z0-9_.-]{1,80}`);
    const res = await fetch(`${this.readUrl}/__requests/${scenario}`);
    if (!res.ok) throw new Error(`mock-llm read ${res.status}`);
    return ((await res.json()) as { requests: RecordedRequest[] }).requests;
  }
}
