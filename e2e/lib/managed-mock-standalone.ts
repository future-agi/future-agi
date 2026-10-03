// Standalone's stack for inspectManagedMock (lib/managed-mock.ts), used only
// when E2E_STACK=standalone (bin/e2e targets docker-compose.yml +
// e2e/stack/docker-compose.standalone-e2e.yml, project futureagi-e2e-standalone).
//
// The one inspector checks what both stacks share: localhost endpoints and an
// explicit local Docker context; every service running in the managed project
// on its one bridge network; the harness ports; the mock's read-only sources;
// and gateway.e2e.yaml routing every model to mock-llm only. In Standalone the
// gateway, API, workers and UI all run in the single `app` container, so this
// adds what differs:
//   - the gateway reads e2e/stack/gateway.e2e.yaml from a read-only bind;
//   - `app`'s only extra hosts are the in-app sandbox and the gateway alias;
//   - the Google credential file is the read-only /dev/null suppression;
//   - `app` carries no real provider key, licence, notification credential or
//     proxy, its gateway wiring stays on loopback, and telemetry is off.
// Not re-expressed: the Python constructor attestation of the background eval
// client (managed-mock-background.py pins Distributed hostnames such as
// temporal:7233 and agentcc-gateway:8080). evalBackground therefore adds only
// the environment pins Distributed's backend and worker carry (requiredMockValues).
import { E2E } from './env';
import { containerEnvironment, gatewayFile, mockFile, requiredMockValues, requireSafe, validateEnvironmentEntries,
  type MockStack, type MockTopology } from './managed-mock';

// docker-compose.yml app: the gateway, the API it syncs from and Temporal share the container.
const STANDALONE: MockTopology = { gateway: 'http://127.0.0.1:8080', controlPlane: 'http://127.0.0.1:8000',
  temporal: '127.0.0.1:7233' };

export function validateStandaloneAppEnvironment(env: Record<string, string>, evalBackground: boolean): void {
  // One container runs the gateway and the workers, so the strict checks always apply.
  validateEnvironmentEntries('app', env, STANDALONE, true);
  for (const [key, value] of Object.entries(requiredMockValues(STANDALONE, evalBackground))) {
    requireSafe(env[key] === value, `required app ${key} mismatch`);
  }
}

export function standaloneMockStack(evalBackground: boolean): MockStack {
  return {
    project: 'futureagi-e2e-standalone',
    services: ['app', 'mock-llm', 'postgres', 'clickhouse'],
    // docker-compose.yml maps code-executor to the in-app sandbox; the overlay adds
    // the agentcc-gateway alias for flows that register the Distributed gateway URL.
    extraHosts: { app: ['agentcc-gateway:127.0.0.1', 'code-executor:127.0.0.1'] },
    endpoints: [
      ['app', '3000/tcp', E2E.appUrl], ['app', '8000/tcp', E2E.apiUrl], ['app', '8080/tcp', E2E.gatewayUrl],
      ['postgres', '5432/tcp', E2E.pgUrl], ['clickhouse', '8123/tcp', E2E.chUrl],
    ],
    sources: [
      ['app', '/etc/futureagi/secrets/agentcc.yaml', gatewayFile],
      ['mock-llm', '/srv/server.mjs', mockFile, ['node', '/srv/server.mjs']],
    ],
    check: ({ selected }) => {
      const vertex = selected.app.Mounts.filter(m => m.Destination === '/etc/futureagi/secrets/vertex.json');
      requireSafe(vertex.length === 1 && vertex[0].Source === '/dev/null' && !vertex[0].RW,
        'app Google credential mount must be the read-only /dev/null suppression');
      validateStandaloneAppEnvironment(containerEnvironment(selected.app), evalBackground);
      return undefined;
    },
  };
}
