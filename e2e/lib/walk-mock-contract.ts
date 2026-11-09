// Shared by the two managed stack inspectors and their offline fixture builder.
// Gateway profiles remain the H5 lane's concern; these services have no vendor route.
import { fileURLToPath } from 'node:url';
import type { MockStack } from './managed-mock';
export const WALK_MOCKS = ['mcp-mock', 'k2-mock', 'otlp-sink', 'siem-sink'] as const;
export const WALK_COMMANDS: Record<string, string[]> = {
  'mcp-mock': ['mcpmock', '--addr', '0.0.0.0:8443', '--auth', 'oauth', '--public-url', 'https://mcp.e2e-public.test:8443', '--tls-cert', '/certs/cert.pem', '--tls-key', '/certs/key.pem'],
  'k2-mock': ['node', '/srv/k2.mjs'],
  'otlp-sink': ['otlpmock', '--addr', '0.0.0.0:4318', '--control', '0.0.0.0:8081'],
  'siem-sink': ['node', '/srv/siem.mjs'],
};
export const WALK_PORTS: Record<string, Record<string, string>> = {
  'mcp-mock': { '8443/tcp': '28443' }, 'k2-mock': { '8443/tcp': '28445', '8081/tcp': '28086' },
  'otlp-sink': { '4318/tcp': '28418', '8081/tcp': '28084' }, 'siem-sink': { '8443/tcp': '28446', '8081/tcp': '28085' },
};
const root = fileURLToPath(new URL('../../', import.meta.url));
export const WALK_SOURCES: MockStack['sources'] = [
  ['mock-llm', '/srv/wire.mjs', `${root}e2e/stack/mock-llm/wire.mjs`],
  ...(['k2-mock', 'siem-sink'] as const).flatMap(service => {
    const file = service === 'k2-mock' ? 'k2.mjs' : 'siem.mjs';
    return [[service, `/srv/${file}`, `${root}e2e/stack/walk-mocks/${file}`, WALK_COMMANDS[service]],
      [service, '/srv/http.mjs', `${root}e2e/stack/walk-mocks/http.mjs`]] as MockStack['sources'];
  }),
];
export function withWalkMocks(stack: MockStack): MockStack {
  const original = stack.check;
  // The product's mcp profile has a separate public-only bearer fixture; do
  // not reinterpret that service as the walk profiles' OAuth recorder.
  const mocks = WALK_MOCKS.filter(service => !stack.publicOnly?.includes(service));
  return { ...stack, services: [...new Set([...stack.services, ...mocks])],
    endpoints: [...stack.endpoints, ...mocks.flatMap(service => Object.entries(WALK_PORTS[service])
      .map(([port, host]) => [service, port, `http://localhost:${host}`] as [string, string, string]))],
    sources: [...stack.sources, ...WALK_SOURCES],
    check: inspection => {
      for (const service of mocks) {
        const c = inspection.selected[service];
        if (JSON.stringify(c.Config.Cmd) !== JSON.stringify(WALK_COMMANDS[service])) throw new Error(`STOP: managed mock unexpected ${service} command`);
        if (c.Config.Env.some(e => /^(https?_proxy|all_proxy|node_options|ld_preload)=.+/i.test(e))) throw new Error(`STOP: managed mock ${service} proxy/preload`);
        for (const port of Object.keys(WALK_PORTS[service])) {
          const bindings = c.NetworkSettings.Ports[port];
          if (!bindings?.length || bindings.some(b => b.HostIp !== '127.0.0.1')) throw new Error(`STOP: managed mock ${service} must publish loopback only`);
        }
      }
      return original(inspection);
    },
  };
}
