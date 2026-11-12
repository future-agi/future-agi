import { E2E } from '../../../lib/env';
import { MockUpstream } from '../../../lib/upstream';
import { WebhookSink } from '../../../lib/sinks';

/** Existing fixture APIs only, not a new fixture server or lifecycle executor.
 * Provenance: e2e/lib/upstream.ts and e2e/stack/mock-llm/server.mjs scoped routes;
 * e2e/lib/sinks.ts and e2e/stack/webhook-sink/server.mjs control routes.
 * Their existing public-network/managed-mock inspection remains mandatory.
 * The shared walk runner has NOT yet bound these to journey invocations.
 */
export interface UpstreamFault { latencyMs?: number; status?: number }

function requireLoopback(endpoint: string): void {
  const url = new URL(endpoint);
  if (!['http:', 'https:'].includes(url.protocol) ||
      !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) || url.username || url.password) {
    throw new Error('Journey fixture control reads require an uncredentialed loopback endpoint');
  }
}

export function gatewayFaultFixtures() {
  requireLoopback(E2E.mockLlmUrl);
  requireLoopback(E2E.webhookSinkUrl);
  const upstream = new MockUpstream();
  const webhook = new WebhookSink();
  return {
    upstream: {
      // Source validates scenario, 0..120000 ms and HTTP status 400..599.
      // This delays BEFORE response; it is NOT a mid-stream/commit barrier.
      providerBaseUrl: (scenario: string, fault: UpstreamFault = {}) => upstream.baseUrl(scenario, fault),
      deliveryCount: async (scenario: string): Promise<number> => (await upstream.requests(scenario)).length,
    },
    webhook: {
      destinationUrl: (channel: string, failFirst?: number) => webhook.url(channel, { failFirst }),
      // PUT /__stopped/<channel>; DELETE restores that channel only.
      stop: (channel: string) => webhook.stopped(channel, true),
      restore: (channel: string) => webhook.stopped(channel, false),
      // /__revoked/<credential>, PUT/DELETE. Only per-run synthetic credentials;
      // never attach, log, interpolate into titles, or pass a product credential.
      revokeSyntheticCredential: (credential: string) => webhook.revoked(credential, true),
      restoreSyntheticCredential: (credential: string) => webhook.revoked(credential, false),
      // Preserve multiplicity and chronology, but do not expose captured headers/bodies.
      statuses: async (channel: string): Promise<number[]> =>
        (await webhook.received(channel)).map(row => row.status),
    },
  };
}

// No Redis pause/restart, image replacement, worker kill, usage suppression,
// OAuth, grant, payment or mutation methods: the kit exposes no accepted callable
// contract for these. FOUNDATION.json records owners and exact assertion rows.
// Never implement one with fetch to a guessed route, docker, Redis or subprocess.
