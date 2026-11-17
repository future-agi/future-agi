// H1/H2: the compose network both stack overlays declare as `e2e-public`
// (e2e/stack/docker-compose*.yml). Its subnet is outside every private,
// reserved and CGNAT range, so the product's SSRF guards (Django
// tfc/utils/ssrf_guard.py classify_ip, the gateway's netguard) treat its
// addresses as public and save and call a webhook or provider there with their
// default settings on. Ports publish on loopback; browser/strict-kit boundaries
// enforce no egress (an internal-only bridge breaks browser reachability).
// The managed-mock inspector (lib/managed-mock.ts) checks a running stack against
// exactly these values.
export const PUBLIC_NETWORK = {
  key: 'e2e-public',
  subnet: '11.88.0.0/24',
  /** Members at a fixed address, reachable by an alias defined on this network only. */
  pinned: {
    'webhook-sink': { ip: '11.88.0.10', alias: 'hooks.e2e-public.test' },
    'mock-llm': { ip: '11.88.0.11', alias: 'llm.e2e-public.test' },
    'mcp-mock': { ip: '11.88.0.13', alias: 'mcp.e2e-public.test' },
    'k2-mock': { ip: '11.88.0.16', alias: 'k2.e2e-public.test' },
    'smtp-sink': { ip: '11.88.0.12', alias: 'smtp.e2e-public.test' },
    'otlp-sink': { ip: '11.88.0.14', alias: 'otlp.e2e-public.test' },
    'siem-sink': { ip: '11.88.0.15', alias: 'siem.e2e-public.test' },
  },
  /** Every service attached to it, per stack (the app containers at any address). */
  members: {
    standalone: ['app', 'mock-llm', 'webhook-sink', 'smtp-sink', 'mcp-mock', 'k2-mock', 'otlp-sink', 'siem-sink'],
    distributed: ['agentcc-gateway', 'backend', 'worker', 'mock-llm', 'webhook-sink', 'smtp-sink', 'mcp-mock', 'k2-mock', 'otlp-sink', 'siem-sink'],
  },
  /** Services a gateway profile adds on this network ONLY (no default network, so
   * no route out of the host and nothing but the members reaches them). */
  publicOnly: {
    mcp: ['mcp-mock'],
  },
} as const;

/** H-MCP: the mcp profile's MCP upstream as an org saves it, and the command the
 * mock runs (e2e/stack/docker-compose.standalone-e2e.yml mcp-mock): agentcc-mcpmock
 * from agentcc-gateway/cmd/agentcc-mcpmock with the scenarios' common fixtures
 * (echo and get_weather read-only, delete_file destructive; one resource, one
 * prompt), refusing any call without the bearer below. The bearer is a fixed test
 * value that guards nothing outside this stack. */
export const MCP_MOCK = {
  service: 'mcp-mock',
  image: 'futureagi/agentcc-mcpmock:e2e-local',
  url: 'https://mcp.e2e-public.test:8443/mcp',
  bearer: 'e2e-mcp-upstream-bearer-not-a-secret',
  entrypoint: ['/agentcc-mcpmock'],
  // The E2E entrypoint reuses mcpmock with these fixed settings, on HTTP and TLS.
  command: [],
  catalogUrl: 'https://mcp.e2e-public.test:8443/mcp',
  appId: 'e2e-echo',
  appName: 'E2E Echo',
} as const;

/** Channel and scenario names the sink and the mock accept: minted per test, URL-safe. */
export const SCOPE_NAME = /^[A-Za-z0-9_.-]{1,80}$/;
