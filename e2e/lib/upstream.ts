// MockUpstream (H2): e2e/stack/mock-llm/server.mjs's scoped routes as an org
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

export interface MockScenario {
  latencyMs?: number; status?: number; failures?: number;
  usage?: 'full' | 'absent' | 'input' | 'output' | 'zero';
  thinking?: boolean; image?: boolean; served?: string; cost?: number;
}

export class MockUpstream {
  constructor(private readonly readUrl: string = E2E.mockLlmUrl) {}

  /** A provider base_url (save it with extra_config.api_path_prefix '/v1'). Without
   *  options the scenario echoes exactly like the unscoped mock (usage 7/7/14). */
  baseUrl(scenario: string, o: MockScenario = {}): string {
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
    if (o.failures !== undefined) {
      if (!Number.isInteger(o.failures) || o.failures < 0 || o.failures > 100) throw new Error('failures is 0-100');
      directives.push(`failures=${o.failures}`);
    }
    if (o.usage !== undefined) {
      if (!['full', 'absent', 'input', 'output', 'zero'].includes(o.usage)) throw new Error('invalid usage mode');
      directives.push(`usage=${o.usage}`);
    }
    if (o.served !== undefined) {
      if (o.served.length > 160 || !/^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/.test(o.served)) throw new Error('served must be a model fixture id');
      directives.push(`served=${encodeURIComponent(o.served)}`);
    }
    if (o.cost !== undefined) {
      if (!Number.isFinite(o.cost) || !/^\d{1,6}(?:\.\d{1,9})?$/.test(String(o.cost))) throw new Error('cost must be a bounded nonnegative decimal');
      directives.push(`cost=${o.cost}`);
    }
    if (o.thinking) directives.push('thinking=1');
    if (o.image) directives.push('image=1');
    return `http://${PUBLIC_NETWORK.pinned['mock-llm'].alias}:8080/s/${scenario}/${directives.join(',') || '-'}`;
  }

  /** All inference POSTs, including unscoped managed traffic and failures.
   * Optional message marker isolates concurrent walks; neither counter resets. */
  async inferenceCount(probe?: string): Promise<number> {
    if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(this.readUrl).hostname)) throw new Error('mock counts require loopback');
    if (probe !== undefined && !/^e2e-census-[a-f0-9]{24}$/.test(probe)) throw new Error('invalid census probe');
    const res = await fetch(`${this.readUrl}/__counts${probe ? `/${probe}` : ''}`, { signal: AbortSignal.timeout(10_000) });
    if (!res.ok) throw new Error(`mock-llm counts ${res.status}`);
    const body = await res.json() as { inferenceRequests: number };
    if (!Number.isSafeInteger(body.inferenceRequests) || body.inferenceRequests < 0) throw new Error('mock counts invalid');
    return body.inferenceRequests;
  }

  /** Every request the scenario received, oldest first. */
  async requests(scenario: string): Promise<RecordedRequest[]> {
    if (!SCOPE_NAME.test(scenario)) throw new Error(`scenario ${JSON.stringify(scenario)} is not [A-Za-z0-9_.-]{1,80}`);
    const res = await fetch(`${this.readUrl}/__requests/${scenario}`);
    if (!res.ok) throw new Error(`mock-llm read ${res.status}`);
    return ((await res.json()) as { requests: RecordedRequest[] }).requests;
  }
}
