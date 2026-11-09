import { subfeatureDeclaration } from '../stack/mcp-subfeature-selection.mjs';
// Standalone's stack for inspectManagedMock (lib/managed-mock.ts), used only
// when E2E_STACK=standalone (bin/e2e targets docker-compose.yml +
// e2e/stack/docker-compose.standalone-e2e.yml, project futureagi-e2e-standalone).
//
// The one inspector checks what both stacks share: localhost endpoints and an
// explicit local Docker context; every service running in the managed project
// on its default bridge network (and the public members on e2e-public); the
// harness ports; the mock's read-only sources;
// and gateway.e2e.yaml routing every model to mock-llm only. In Standalone the
// gateway, API, workers and UI all run in the single `app` container, so this
// adds what differs:
//   - the gateway reads its profile's config (lib/profile.ts: gateway.e2e.yaml,
//     or gateway.r7alt.yaml under E2E_GATEWAY_PROFILE=r7alt) from a read-only bind;
//   - `app`'s only extra hosts are the in-app sandbox and the gateway alias;
//   - the Google credential file is the read-only /dev/null suppression;
//   - `app` carries no real provider key, licence, notification credential or
//     proxy, its gateway wiring stays on loopback, and telemetry is off.
// The public-classified e2e-public network (lib/public-network.ts) holds `app`,
// mock-llm, webhook-sink and smtp-sink; the shared inspector checks it.
// The mcp and mcp-signin profiles (H-MCP, lib/profile.ts) adds `mcp-mock`, agentcc-mcpmock: on
// e2e-public only (no default network, so no way out of the host), at its pinned
// address and alias, from the image `bin/e2e build mcpmock` tags, with exactly its
// entrypoint and command, no mount and only the reviewed authentication selection; and the gateway
// file must be exactly stock plus MCP_DELTA, naming no MCP upstream.
// Not re-expressed: the Python constructor attestation of the background eval
// client (managed-mock-background.py pins Distributed hostnames such as
// temporal:7233 and agentcc-gateway:8080). evalBackground therefore adds only
// the environment pins Distributed's backend and worker carry (requiredMockValues).
import { E2E } from './env';
import { containerEnvironment, mockFile, profileFile, profileMountReason, requiredMockValues, requireSafe, replicaDeclared, sinkFile,
  validateEnvironmentEntries, type Container, type MockStack, type MockTopology } from './managed-mock';
import { PROFILE_RUNS_MCP_MOCK, type WalkGatewayProfile } from './profile';
import { MCP_MOCK, PUBLIC_NETWORK } from './public-network';
import { WALK_MOCKS } from './walk-mock-contract';

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

// docker-compose.yml app: the gateway, the API it syncs from and Temporal share the container.
const STANDALONE: MockTopology = { gateway: 'http://127.0.0.1:8080', controlPlane: 'http://127.0.0.1:8000',
  temporal: '127.0.0.1:7233', settingsModule: 'tfc.settings.settings' };

export function validateStandaloneAppEnvironment(env: Record<string, string>, evalBackground: boolean): void {
  // One container runs the gateway and the workers, so the strict checks always apply.
  validateEnvironmentEntries('app', env, STANDALONE, true);
  for (const [key, value] of Object.entries(requiredMockValues(STANDALONE, evalBackground))) {
    requireSafe(env[key] === value, `required app ${key} mismatch`);
  }
}

/** The services the Standalone inspection checks under `profile`. */
export const standaloneServices = (profile: WalkGatewayProfile = 'stock') => ['app', 'mock-llm', 'webhook-sink', 'smtp-sink', 'postgres',
  'clickhouse', ...WALK_MOCKS];

/** agentcc-mcpmock as bin/e2e starts it under the mcp profile. */
export function validateMcpMockContainer(c: Container): void {
  requireSafe(c.Config.Image === MCP_MOCK.image && same(c.Config.Entrypoint, MCP_MOCK.entrypoint) &&
    same(c.Config.Cmd ?? [], MCP_MOCK.command) && c.Mounts.length === 0 &&
    Object.keys(c.NetworkSettings.Ports ?? {}).every(port => !c.NetworkSettings.Ports[port]?.length),
  'mcp-mock is not agentcc-mcpmock as the mcp profile runs it');
  const env = containerEnvironment(c);
  requireSafe(env.E2E_MCP_MOCK_AUTH === subfeatureDeclaration().mockAuth, 'mcp-mock auth does not match the declared selection');
  validateEnvironmentEntries(MCP_MOCK.service, env, STANDALONE, true);
}

export function standaloneMockStack(evalBackground: boolean, profile: WalkGatewayProfile = 'stock'): MockStack {
  const replica = replicaDeclared();
  return {
    profile,
    project: 'futureagi-e2e-standalone',
    services: [...standaloneServices(profile), ...(replica ? ['agentcc-gateway-2'] : [])],
    networkNamespace: replica ? { 'agentcc-gateway-2': 'app' } : undefined,
    publicMembers: PUBLIC_NETWORK.members.standalone.filter(service => !PROFILE_RUNS_MCP_MOCK[profile] || service !== MCP_MOCK.service),
    publicOnly: PROFILE_RUNS_MCP_MOCK[profile] ? PUBLIC_NETWORK.publicOnly.mcp : [],
    // docker-compose.yml maps code-executor to the in-app sandbox; the overlay adds
    // the agentcc-gateway alias for flows that register the Distributed gateway URL.
    extraHosts: { app: ['agentcc-gateway:127.0.0.1', 'code-executor:127.0.0.1'] },
    endpoints: [
      ...(replica ? [['app', '8081/tcp', process.env.E2E_GATEWAY_REPLICA_URL ?? 'http://localhost:28091'] as [string, string, string]] : []),
      ['app', '3000/tcp', E2E.appUrl], ['app', '8000/tcp', E2E.apiUrl], ['app', '8080/tcp', E2E.gatewayUrl],
      ['postgres', '5432/tcp', E2E.pgUrl], ['clickhouse', '8123/tcp', E2E.chUrl],
      ['mock-llm', '8080/tcp', E2E.mockLlmUrl], ['webhook-sink', '8080/tcp', E2E.webhookSinkUrl],
      ['smtp-sink', '8025/tcp', E2E.mailSinkUrl],
    ],
    sources: [
      ...(replica ? [['agentcc-gateway-2', '/etc/futureagi/secrets/agentcc.yaml', profileFile(profile),
        ['--config', '/etc/futureagi/secrets/agentcc.yaml'], profileMountReason(profile)] as [string, string, string, string[], string]] : []),
      ['app', '/etc/futureagi/secrets/agentcc.yaml', profileFile(profile), undefined, profileMountReason(profile)],
      ['mock-llm', '/srv/server.mjs', mockFile, ['node', '/srv/server.mjs']],
      ['webhook-sink', '/srv/server.mjs', sinkFile, ['node', '/srv/server.mjs']],
    ],
    check: ({ selected }) => {
      const vertex = selected.app.Mounts.filter(m => m.Destination === '/etc/futureagi/secrets/vertex.json');
      requireSafe(vertex.length === 1 && vertex[0].Source === '/dev/null' && !vertex[0].RW,
        'app Google credential mount must be the read-only /dev/null suppression');
      validateStandaloneAppEnvironment(containerEnvironment(selected.app), evalBackground);
      if (replica) {
        const r = selected['agentcc-gateway-2'];
        requireSafe(r.Image === selected.app.Image &&
          JSON.stringify(r.HostConfig.Tmpfs) === JSON.stringify({ '/data': 'rw,noexec,nosuid,size=16m' }) &&
          r.Mounts.filter(m => m.Destination !== '/etc/futureagi/secrets/agentcc.yaml')
            .every(m => m.Type === 'tmpfs' && m.Destination === '/data') &&
          JSON.stringify(r.Config.Entrypoint) === JSON.stringify(['agentcc-gateway']), 'replica image, entrypoint or mount mismatch');
        const env = containerEnvironment(r);
        validateEnvironmentEntries('agentcc-gateway-2', env, STANDALONE, true);
        for (const [key, value] of Object.entries({ AGENTCC_REDIS_ADDRESS: '127.0.0.1:6379', AGENTCC_REDIS_DB: '4',
          AGENTCC_REDIS_PASSWORD: 'local-dev-only-redis-password', AGENTCC_PORT: '8081',
          AGENTCC_CONTROL_PLANE_URL: STANDALONE.controlPlane, AGENTCC_SYNC_INTERVAL: '60s' })) {
          requireSafe(env[key] === value, `replica required ${key} mismatch`);
          if (key.startsWith('AGENTCC_REDIS_')) requireSafe(containerEnvironment(selected.app)[key] === value,
            `app/replica ${key} mismatch`);
        }
      }
      if (PROFILE_RUNS_MCP_MOCK[profile]) validateMcpMockContainer(selected[MCP_MOCK.service]);
      return undefined;
    },
  };
}
