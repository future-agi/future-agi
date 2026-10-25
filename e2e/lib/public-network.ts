// The compose network both stack overlays declare as `e2e-public`
// (e2e/stack/docker-compose*.yml). Its subnet is outside every private,
// reserved and CGNAT range, so the product's SSRF guards (Django
// tfc/utils/ssrf_guard.py classify_ip, the gateway's netguard) treat its
// addresses as public and save and call a webhook or provider there with their
// default settings on; `internal: true` keeps everything on it inside the host.
// The managed-mock inspector (lib/managed-mock.ts) checks a running stack against
// exactly these values.
export const PUBLIC_NETWORK = {
  key: 'e2e-public',
  subnet: '11.88.0.0/24',
  /** Members at a fixed address, reachable by an alias defined on this network only. */
  pinned: {
    'webhook-sink': { ip: '11.88.0.10', alias: 'hooks.e2e-public.test' },
    'mock-llm': { ip: '11.88.0.11', alias: 'llm.e2e-public.test' },
  },
  /** Every service attached to it, per stack (the app containers at any address). */
  members: {
    standalone: ['app', 'mock-llm', 'webhook-sink'],
    distributed: ['agentcc-gateway', 'backend', 'worker', 'mock-llm', 'webhook-sink'],
  },
} as const;

/** Channel and scenario names the sink and the mock accept: minted per test, URL-safe. */
export const SCOPE_NAME = /^[A-Za-z0-9_.-]{1,80}$/;
