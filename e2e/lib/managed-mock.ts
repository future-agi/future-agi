import { MCP_GATE_KEYS, subfeatureDeclaration, validateMcpGateEnvironment, validateMcpGateEntries } from '../stack/mcp-subfeature-selection.mjs';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, realpathSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parseDocument } from 'yaml';
import { E2E } from './env';
import type { TestActor } from './provisioning';
import type { StateProbe } from './state-probe';
import { standaloneMockStack } from './managed-mock-standalone';
import { PUBLIC_NETWORK } from './public-network';
import { EXPORT_ENDPOINTS } from './export-sinks';
import { walkGatewayProfile, MCP_STATE_KEY, newOrgDefaultDeclared, PROFILE_FILES, PROFILE_SERVES_MCP, validateProfileSource,
  type WalkGatewayProfile } from './profile';
import { withWalkMocks } from './walk-mock-contract';
import { attestLoopbackForwards, linuxProc, type ForwardedEndpoint, type ProbeCredentials, type Prober, type ProcFs,
  type UnpublishedEndpoint } from './loopback-forwarder';

// docker-compose.distributed.yml backend-env; gateway.e2e.yaml providers.openai.
export const MOCK_MODEL = 'gpt-4o';
export const MOCK_BASE = 'http://agentcc-gateway:8080/v1';
export const MOCK_SERVING_BASE = 'http://mock-llm:8080';
export const MOCK_KEY = 'local-dev-only-shared-secret-replace-me';
const root = fileURLToPath(new URL('../../', import.meta.url));
export const gatewayFile = `${root}${PROFILE_FILES.stock}`;
/** The gateway config file the declared profile mounts (lib/profile.ts). */
export const profileFile = (profile: WalkGatewayProfile) => `${root}${PROFILE_FILES[profile]}`;
/** The mount rule's own message, so a wrong profile mount names what was expected. */
export const profileMountReason = (profile: WalkGatewayProfile) =>
  `gateway profile ${profile} expects ${PROFILE_FILES[profile]} mounted`;
export const mockFile = `${root}e2e/stack/mock-llm/server.mjs`;
export const sinkFile = `${root}e2e/stack/webhook-sink/server.mjs`;
const hash = (value: string | Buffer) => createHash('sha256').update(value).digest('hex');
export const requireSafe = (ok: unknown, reason: string): void => {
  if (!ok) throw new Error(`STOP: managed mock ${reason}`);
};
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** The one managed provider gateway.e2e.yaml may declare: FutureAGI-credentialed
 * (`managed: true`) but on the same deterministic mock and dummy key. `type:
 * openrouter` is allowed here only, and only beside an explicit mock base_url,
 * which the gateway keeps over the preset's (providers/presets.go adds just the
 * /v1 path prefix); it is what seeds the bundled OpenRouter catalog snapshot.
 */
export const MANAGED_PROVIDER = 'fagi-managed';
/** The managed section the E2E gateway runs with: the bundled catalog only (no
 * live fetch) and the in-memory, test-only credit ledger (no Redis in this config). */
export const MANAGED_SETTINGS = { markup_percent: 5, require_credits: true, grant_only_credits: true, require_redis: false, catalog_refresh: '0s' };

export function validateManagedMock(provider: Record<string, unknown> | undefined, managed: unknown): void {
  requireSafe(provider && same(Object.keys(provider).sort(), ['api_format', 'api_key', 'auto_discover', 'base_url',
    'conn_pool_size', 'default_timeout', 'managed', 'max_concurrent', 'type']), 'unexpected managed provider fields');
  requireSafe(provider!.base_url === 'http://mock-llm:8080' && provider!.api_key === 'e2e-mock' &&
    provider!.api_format === 'openai' && provider!.type === 'openrouter' && provider!.managed === true &&
    provider!.auto_discover === false, 'managed provider is not the deterministic mock');
  requireSafe(managed && typeof managed === 'object' && same(Object.keys(managed).sort(),
    Object.keys(MANAGED_SETTINGS).sort()) && Object.entries(MANAGED_SETTINGS).every(
    ([key, value]) => (managed as Record<string, unknown>)[key] === value), 'unexpected managed settings');
}

/** Strict safety contract, not a general gateway config validator. No env expansion,
 * aliases, extra providers, presets, model maps, mirrors, tools or control plane;
 * the exceptions are the managed provider and section validateManagedMock pins and
 * the guardrail plugin switched on with no rules (exactly `guardrails: { enabled: true }`).
 * Gateway config.Load expands env; registry.NewRegistry applies these routing knobs.
 */
export function validateMockRouting(source: string, evalBackground = false): void {
  const doc = parseDocument(source, { uniqueKeys: true });
  requireSafe(doc.errors.length === 0, 'invalid gateway YAML');
  let cfg;
  try { cfg = doc.toJS({ maxAliasCount: 0 }); }
  catch { throw new Error('STOP: managed mock aliased gateway YAML'); }
  const all = cfg ? Object.keys(cfg).sort() : [];
  // The guardrail plugin with no rule of the file's own (gateway.e2e.yaml): exactly
  // `enabled: true`. It then runs only the checks an org saves; a file rule could
  // name a webhook or an external provider, so any other key is STOP.
  if (all.includes('guardrails')) {
    const g = cfg.guardrails;
    requireSafe(g !== null && typeof g === 'object' && !Array.isArray(g) && same(Object.keys(g), ['enabled']) &&
      g.enabled === true, 'unexpected guardrails configuration');
  }
  const sections = all.filter(s => s !== 'guardrails');
  const withManaged = same(sections, ['logging', 'managed', 'providers', 'server']);
  requireSafe(withManaged || same(sections, ['logging', 'providers', 'server']), 'unexpected gateway sections');
  requireSafe(same(Object.keys(cfg.providers ?? {}), withManaged ? ['openai', MANAGED_PROVIDER] : ['openai']),
    'provider set is not mock-only');
  if (withManaged) validateManagedMock(cfg.providers[MANAGED_PROVIDER], cfg.managed);
  const provider = cfg.providers.openai;
  requireSafe(same(Object.keys(provider).sort(), ['api_format', 'api_key', 'base_url', 'conn_pool_size',
    'default_timeout', 'max_concurrent', 'models']), 'unexpected provider fields');
  requireSafe(provider.base_url === 'http://mock-llm:8080' && provider.api_key === 'e2e-mock' &&
    provider.api_format === 'openai', 'upstream route or key is not the deterministic mock');
  const judgeModels = ['gpt-3.5-turbo', 'gpt-4-turbo', 'gpt-4o', 'gpt-4o-mini', 'o1', 'o1-mini'];
  const routes = Array.isArray(provider.models) ? [...provider.models].sort() : [];
  requireSafe(same(routes, [...judgeModels, 'turing_flash']) ||
    (!evalBackground && same(routes, judgeModels)), 'unexpected model routes or missing background capability');
  requireSafe(Object.keys(cfg.server).every(k => ['port', 'host', 'read_timeout', 'write_timeout',
    'idle_timeout', 'shutdown_timeout', 'max_request_body_size', 'default_request_timeout',
    'max_request_timeout'].includes(k)) &&
    cfg.server.port === 8080 && cfg.server.host === '0.0.0.0', 'unexpected gateway server settings');
  requireSafe(same(Object.keys(cfg.logging), ['level']) &&
    ['debug', 'info', 'warn', 'error'].includes(cfg.logging.level), 'unexpected logging configuration');
  requireSafe(!JSON.stringify(cfg).includes('$'), 'environment-dependent gateway configuration');
}

/** Refused in every E2E container: a proxy or preload can reroute or rewrite any call. */
const PROXY_OR_PRELOAD = /^(https?_proxy|all_proxy|node_options|ld_preload|pythonpath|pythonstartup)$/i;
/** Licence, notification and credential settings, refused wherever the inspection
 * is strict: the Distributed background opt-in, and always in Standalone. */
const CREDENTIAL_OVERRIDE = /^(EE_LICENSE_KEY|SENTRY_DSN|SLACK_.*|DEPLOYMENT_TELEMETRY_SLACK_WEBHOOK|ERROR_LOGS_WEBHOOK|MIX_PANEL_TOKEN|MAILGUN_.*|SMTP_.*|SENDGRID_.*|RESEND_.*|AWS_SESSION_TOKEN|GOOGLE_APPLICATION_CREDENTIALS|DAYTONA_API_KEY|E2B_API_KEY)$/;
const MOCK_SLACK_ENV: Record<string, string> = {
  SLACK_CLIENT_ID: 'e2e-client',
  SLACK_CLIENT_SECRET: 'e2e-secret',
  SLACK_OAUTH_AUTHORIZE_URL: 'http://localhost:28081/oauth/v2/authorize',
  SLACK_REDIRECT_URI: 'http://localhost:8100/integrations/slack/callback/',
  SLACK_API_URL: 'http://mock-slack:8080/api',
};

/** Where the gateway, the API it syncs from and Temporal listen: their own
 * services in Distributed, loopback inside Standalone's one `app` container. */
export const replicaDeclared = () => process.env.E2E_STACK === 'standalone' &&
  process.env.E2E_GATEWAY_REPLICAS === '2' && mcpProfileDeclared();

export interface MockTopology { gateway: string; controlPlane: string; temporal: string; settingsModule: string;
  /** The integration export overrides the API and worker may carry, exactly (lib/export-sinks.ts); none when absent. */
  exportEndpoints?: Readonly<Record<string, string>> }
const DISTRIBUTED: MockTopology = { gateway: 'http://agentcc-gateway:8080', controlPlane: 'http://backend',
  temporal: 'temporal:7233', settingsModule: 'tfc.settings.e2e', exportEndpoints: EXPORT_ENDPOINTS };
/** The containers that run the export services: the API (validate) and the worker (sync). */
const EXPORTING_SERVICES = ['backend', 'worker'];

function gatewayValues({ gateway, controlPlane }: MockTopology) {
  const allowed: Record<string, string> = { AGENTCC_INTERNAL_API_KEY: MOCK_KEY,
    AGENTCC_ADMIN_TOKEN: 'local-dev-only-admin-token-replace-me',
    AGENTCC_INTERNAL_URL: gateway, AGENTCC_GATEWAY_INTERNAL_URL: gateway };
  // Control-plane wiring of the gateway, in both compose files: it pulls keys
  // from the API and posts request logs back. No provider route,
  // credential or network allowance: private provider URLs stay refused.
  const wiring: Record<string, string> = { AGENTCC_CONTROL_PLANE_URL: controlPlane,
    AGENTCC_CONTROL_PLANE_TOKEN: allowed.AGENTCC_ADMIN_TOKEN, AGENTCC_SYNC_ON_STARTUP: 'true',
    AGENTCC_SYNC_INTERVAL: '60s', AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS: 'false',
    // The stack keeps the published admin token, which the gateway and the
    // API's admin endpoints refuse without this dev-only opt-in (e2e/stack/*.env
    // set it; the backend and workers carry it too).
    AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN: '1' };
  return { allowed, wiring };
}

/** The new-org default (agentcc/services/managed_models_default.py): it turns Future
 * AGI models on for an organization's first org config. It routes nothing to a
 * provider and carries no credential, but it changes what a new org's key may
 * call, so it is accepted only as `1` and only on a run that declares it
 * (E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS=1: the stack the @new-org-default flows run
 * on, lib/quarantine.ts). Anywhere else it is an unsupported gateway override. */
export const NEW_ORG_DEFAULT = 'AGENTCC_MANAGED_MODELS_ON_FOR_NEW_ORGS';

/** Whether the declared gateway profile serves MCP; an undeclared or unknown one does not. */
const mcpProfileDeclared = (): boolean => {
  try { return PROFILE_SERVES_MCP[walkGatewayProfile()]; } catch { return false; }
};

/** The checks every variable of one container passes, in both stacks. `strict`
 * adds the licence, notification and credential refusals and the local-mail and
 * Sentry-off pins. Empty values mean unset: disabled per settings.py.
 */
export function validateEnvironmentEntries(service: string, env: Record<string, string>, topology: MockTopology,
  strict: boolean, mounts: Container['Mounts'] = []): void {
  const { allowed, wiring } = gatewayValues(topology);
  validateMcpGateEnvironment(service, env, subfeatureDeclaration(), false);
  for (const [key, value] of Object.entries(env)) {
    if (!value) continue;
    requireSafe(!PROXY_OR_PRELOAD.test(key), `${service} has a proxy/preload override`);
    // The E2E backend enables webhook authentication; the worker may leave it
    // unset. This dummy secret grants no provider route or credential override.
    if (key.startsWith('AGENTCC_')) requireSafe(allowed[key] === value || wiring[key] === value ||
      MCP_GATE_KEYS.includes(key) ||
      (key === 'AGENTCC_WEBHOOK_SECRET' && value === 'e2e-agentcc-webhook-secret') ||
      (key === NEW_ORG_DEFAULT && value === '1' && newOrgDefaultDeclared()) ||
      // The MCP profiles' fixed test state key (lib/profile.ts MCP_STATE_KEY), on the
      // app only and only when the declared profile serves MCP (the MCP line's rule).
      (['app', 'agentcc-gateway-2'].includes(service) && key === 'AGENTCC_MCP_STATE_KEYS' && value === MCP_STATE_KEY && mcpProfileDeclared()) ||
      (replicaDeclared() && ['app', 'agentcc-gateway-2'].includes(service) &&
        ({ AGENTCC_REDIS_ADDRESS: '127.0.0.1:6379', AGENTCC_REDIS_DB: '4',
          AGENTCC_REDIS_PASSWORD: 'local-dev-only-redis-password',
          ...(service === 'agentcc-gateway-2' ? { AGENTCC_PORT: '8081' } : {}) } as Record<string, string>)[key] === value),
    `${service} has an unsupported gateway override (${key})`);
    // An integration export override (futureagi/integrations/services/endpoints.py)
    // redirects a vendor host: only the declared local sink, only where the export
    // services run. Any other destination is STOP.
    if (key.startsWith('INTEGRATIONS_')) requireSafe(EXPORTING_SERVICES.includes(service) &&
      topology.exportEndpoints?.[key] === value, `${service} exports to an undeclared destination (${key})`);
    if (/_API_KEY$/.test(key) || ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'].includes(key)) {
      requireSafe(value === MOCK_KEY || value === 'e2e-mock', `${service} has a non-mock provider key (${key})`);
    }
    if (!strict) continue;
    if (service === 'agentcc-gateway' && key === 'GOOGLE_APPLICATION_CREDENTIALS' &&
        value === '/app/Vertex_AI_Creds.json') {
      validateBackgroundMockMounts(service, mounts);
      continue; // Exact root-Compose /dev/null suppression, never a credential.
    }
    if (key.startsWith('SLACK_') && ['backend', 'worker'].includes(service) &&
        env.SLACK_API_URL === MOCK_SLACK_ENV.SLACK_API_URL &&
        MOCK_SLACK_ENV[key] === value) continue;
    requireSafe(!CREDENTIAL_OVERRIDE.test(key), `${service} has a license, notification or credential override (${key})`);
    if (key === 'EMAIL_BACKEND') requireSafe(value === 'django.core.mail.backends.console.EmailBackend', 'nonlocal email backend');
    if (key === 'SENTRY_ENABLED') requireSafe(value === 'false', 'Sentry must be disabled');
  }
}

/** Values the containers running the API and the workers must carry, checked
 * even when absent: the gateway wiring and the telemetry opt-out, plus, with the
 * background opt-in, the mock serving URL, the local Temporal and no licence or
 * mail key. Distributed checks them with the opt-in only; Standalone always.
 */
export function requiredMockValues(topology: MockTopology, evalBackground: boolean): Record<string, string> {
  const pins = { ...gatewayValues(topology).allowed, FUTURE_AGI_TELEMETRY_DISABLED: 'true' };
  if (!evalBackground) return pins;
  return { ...pins, MODEL_SERVING_URL: MOCK_SERVING_BASE, ENV_TYPE: 'local', EE_LICENSE_KEY: '',
    NO_STARTUP_DB_MUTATIONS: 'true', OTEL_ENABLED: 'false', TEMPORAL_HOST: topology.temporal,
    TEMPORAL_NAMESPACE: 'default', DJANGO_SETTINGS_MODULE: topology.settingsModule, MAILGUN_API_KEY: '' };
}

/** Required values are checked even when absent. No license or generic-provider
 * fallback. Pure validation so offline tests never need Docker, API or SDK requests.
 */
export function validateMockEnvironment(service: string, env: Record<string, string>, evalBackground = false,
  mounts: Container['Mounts'] = []): void {
  validateEnvironmentEntries(service, env, DISTRIBUTED, evalBackground, mounts);
  if (!evalBackground) return;
  const { allowed } = gatewayValues(DISTRIBUTED);
  if (service === 'agentcc-gateway') {
    for (const key of ['AGENTCC_INTERNAL_API_KEY', 'AGENTCC_ADMIN_TOKEN']) {
      requireSafe(env[key] === allowed[key], `required ${service} ${key} mismatch`);
    }
  }
  if (!['backend', 'worker'].includes(service)) return;
  for (const [key, value] of Object.entries(requiredMockValues(DISTRIBUTED, true))) {
    requireSafe(env[key] === value, `required ${service} ${key} mismatch`);
  }
  if (service === 'worker') {
    requireSafe(env.TEMPORAL_ALL_QUEUES === 'true' && env.TEMPORAL_EXCLUDED_QUEUES === 'simulation_runner',
      'worker must include the native agent_compass queue');
  }
}

export interface NetworkEndpoint { NetworkID: string; Aliases: string[] | null; IPAddress: string }
export interface Container {
  Id: string; Image: string; State: { Running: boolean; StartedAt: string };
  Config: { Labels: Record<string, string>; Env: string[]; Cmd: string[]; Entrypoint: string[]; Image?: string };
  HostConfig: { Tmpfs?: Record<string, string>; NetworkMode?: string; ExtraHosts: string[] | null; Dns?: string[]; DnsSearch?: string[] };
  Mounts: { Type: string; Source: string; Destination: string; RW: boolean }[];
  NetworkSettings: { Networks: Record<string, NetworkEndpoint>;
    Ports: Record<string, { HostIp: string; HostPort: string }[] | null> };
}

/** A public-only service's (absent) default-network endpoint, for validatePublicMember. */
const NO_ENDPOINT: NetworkEndpoint = { NetworkID: '', Aliases: [], IPAddress: '' };

/** A member of the public-classified network (lib/public-network.ts): a pinned
 * member sits at its address with its alias there; nobody else carries a pinned
 * alias, and no pinned alias is defined on the default network, so Docker DNS
 * answers it with the public address alone.
 */
export function validatePublicMember(service: string, onDefault: NetworkEndpoint, onPublic: NetworkEndpoint): void {
  const pin = (PUBLIC_NETWORK.pinned as Record<string, { ip: string; alias: string }>)[service];
  const aliases: string[] = Object.values(PUBLIC_NETWORK.pinned).map(p => p.alias);
  const carries = (endpoint: NetworkEndpoint, alias: string) => (endpoint.Aliases ?? []).includes(alias);
  requireSafe(!aliases.some(alias => carries(onDefault, alias)) && (pin
    ? onPublic.IPAddress === pin.ip && carries(onPublic, pin.alias) &&
      aliases.every(alias => alias === pin.alias || !carries(onPublic, alias))
    : !aliases.some(alias => carries(onPublic, alias))), `service ${service} public address or alias mismatch`);
}

export function validateBackgroundMockMounts(service: string, mounts: Container['Mounts']): void {
  const gateway = service === 'agentcc-gateway';
  requireSafe(gateway || service === 'mock-llm', 'unsupported mock mount service');
  const primary = mounts.filter(m => m.Destination === (gateway ? '/app/config.yaml' : '/srv/server.mjs'));
  // inspectManagedMock separately pins this primary source's real path/hash.
  requireSafe(primary.length === 1 && primary[0].Type === 'bind' && primary[0].RW === false,
    `unexpected ${service} source mount`);
  requireSafe(mounts.length === 2, `unexpected additional ${service} mount`);
  if (!gateway) {
    const wire = mounts.filter(m => m.Destination === '/srv/wire.mjs');
    requireSafe(wire.length === 1 && wire[0].Type === 'bind' && !wire[0].RW, 'mock wire source must be read-only');
  }
  if (gateway) {
    // Root Compose intentionally masks any packaged Google credential file.
    const suppressed = mounts.filter(m => m.Destination === '/app/Vertex_AI_Creds.json');
    requireSafe(suppressed.length === 1 && suppressed[0].Type === 'bind' &&
      suppressed[0].Source === '/dev/null' && suppressed[0].RW === false,
    'gateway credential suppression must be an exact read-only /dev/null bind');
  }
}
export interface MockReceipt {
  context: string; project: string; networkId: string; publicNetworkId: string;
  /** The declared gateway profile and the hash of the file it mounts. */
  profile: WalkGatewayProfile; gatewaySha: string; mockSha: string; sourceHashes: Record<string, string>;
  services: { service: string; id: string; image: string; startedAt: string }[];
  background?: { capability: 'eval-clustering'; sourceHashes: Record<string, string>; probeSha: string;
    processes: unknown[] };
  /** Endpoints Docker does not publish, proven reached through the runner's loopback
   * forwarder (lib/loopback-forwarder.ts), each with its probe and delivered counts;
   * absent when every endpoint is published. */
  forwarded?: ForwardedEndpoint[];
}

export function managedMockInspectionError(error: unknown): Error {
  const failure = (error ?? {}) as { stderr?: unknown; code?: unknown; status?: unknown; signal?: unknown };
  const stderr = String(failure.stderr ?? '').trim();
  if (/^STOP: managed mock background [a-zA-Z0-9 _/.:=-]{1,200}$/.test(stderr)) return new Error(stderr);
  // Never expose command arguments, SDK text or Docker output (they can hold credentials).
  const code = ['ETIMEDOUT', 'ENOBUFS', 'ENOENT', 'EACCES'].includes(String(failure.code)) ? failure.code : 'unknown';
  const status = Number.isInteger(failure.status) ? failure.status : 'unknown';
  const signal = ['SIGTERM', 'SIGKILL', 'SIGABRT', 'SIGSEGV'].includes(String(failure.signal)) ? failure.signal : 'unknown';
  return new Error(`STOP: managed mock read-only Docker inspection failed (code=${code}, status=${status}, signal=${signal})`);
}

/** One E2E stack's shape for inspectMockStack, which checks what every stack
 * shares: a local daemon, the managed project's services on its default bridge
 * network (the public members also on its internal public-classified one), the
 * harness ports (Docker-published, or on an internal default network reached
 * through an attested loopback forwarder), the mock's read-only sources and the gateway routing. `check` adds the rest: entrypoints, mounts, environment.
 */
export interface MockStack {
  /** The Compose project bin/e2e must resolve to, where the stack pins one. */
  project?: string;
  /** Compose SERVICE keys, never machine-specific/generated container names. */
  services: string[];
  /** The services also on the public-classified network (PUBLIC_NETWORK.members); every other one is on default only. */
  publicMembers: readonly string[];
  /** Services on the public-classified network only (PUBLIC_NETWORK.publicOnly), never on default. */
  publicOnly?: readonly string[];
  /** Exact extra_hosts per service; every other service has none. */
  extraHosts?: Record<string, string[]>;
  /** Explicit service network-namespace sharing; no arbitrary host/container mode. */
  networkNamespace?: Record<string, string>;
  /** [service, container port, the harness endpoint it must publish]. */
  endpoints: [string, string, string][];
  /** The declared gateway profile; its file is the gateway's mounted config. */
  profile: WalkGatewayProfile;
  /** [service, mount destination, repo source, exact command, wrong-source reason]: read-only binds. */
  sources: [string, string, string, string[]?, string?][];
  check(inspection: StackInspection): MockReceipt['background'];
}
export interface StackInspection {
  context: string; network: string; selected: Record<string, Container>;
  config: { services: Record<string, { image: string }> };
  run(file: string, args: string[], input?: string): string;
  docker(...args: string[]): string;
}

export const containerEnvironment = (c: Container): Record<string, string> =>
  Object.fromEntries(c.Config.Env.map(s => { const i = s.indexOf('='); return [s.slice(0, i), s.slice(i + 1)]; }));

/** Read-only Docker calls only (a loopback-forwarded endpoint adds its probes'
 * docker exec reads and docker logs, lib/forward-probe.mjs; their only writes are the
 * ClickHouse probe's per-probe secret files in that container's user_files
 * directory and the harness mocks' per-inspection probe key in /tmp/fwdprobe-keys,
 * each removed after). Never print compose config /
 * inspect output: it contains secrets. CI may use a verified local default daemon; local users must
 * select their context explicitly. An unattested attach target FAILS, never skips.
 * `proc` is where the loopback-forwarder evidence is read: the real /proc unless an
 * offline test passes recorded procfs text; `probe` likewise replaces the active
 * forwarder probes with recorded outcomes (flows never pass either).
 */
export function inspectManagedMock({ evalBackground = false, exportSinks = false, proc, probe }:
  { evalBackground?: boolean; exportSinks?: boolean; proc?: ProcFs; probe?: Prober } = {}): MockReceipt {
  let profile: WalkGatewayProfile;
  try { profile = walkGatewayProfile(); } catch { throw new Error('STOP: managed mock gateway profile is not declared correctly'); }
  // Standalone: one `app` container instead of backend/worker/gateway/frontend.
  if (process.env.E2E_STACK === 'standalone') {
    // The export overrides are set on the Distributed stack only.
    requireSafe(!exportSinks, 'export sinks are Distributed only');
    return inspectMockStack(withWalkMocks(standaloneMockStack(evalBackground, profile)), evalBackground, proc, probe);
  }
  requireSafe(profile === 'stock', `gateway profile ${profile} is Standalone only`);
  return inspectMockStack(withWalkMocks(distributedMockStack(evalBackground, exportSinks)), evalBackground, proc, probe);
}

/** The stores' credentials the harness itself connects with (lib/env.ts), for the forwarder probes. */
function probeCredentials(): ProbeCredentials {
  const pg = new URL(E2E.pgUrl);
  return { pg: { user: decodeURIComponent(pg.username), password: decodeURIComponent(pg.password),
    database: decodeURIComponent(pg.pathname.slice(1)) }, ch: { user: 'default', password: E2E.chPassword } };
}

function inspectMockStack(stack: MockStack, evalBackground: boolean, proc?: ProcFs, probe?: Prober): MockReceipt {
  for (const endpoint of [E2E.appUrl, E2E.apiUrl, E2E.gatewayUrl, E2E.pgUrl, E2E.chUrl, E2E.slackMockUrl,
    E2E.mockLlmUrl, E2E.webhookSinkUrl, E2E.mailSinkUrl]) {
    requireSafe(new URL(endpoint).hostname === 'localhost', 'requires localhost endpoints');
  }
  const context = process.env.DOCKER_CONTEXT || (process.env.CI ? 'default' : '');
  requireSafe(context, 'requires an explicit Docker context outside CI');
  requireSafe(!process.env.DOCKER_HOST, 'DOCKER_HOST override is not supported');
  const run = (file: string, args: string[], input?: string) => {
    try {
      return execFileSync(file, args, { encoding: 'utf8', timeout: 30_000, input, maxBuffer: 2 * 1024 * 1024,
        env: { ...process.env, DOCKER_CONTEXT: context }, stdio: ['pipe', 'pipe', 'pipe'] });
    } catch (error) {
      throw managedMockInspectionError(error);
    }
  };
  const docker = (...args: string[]) => run('docker', ['--context', context, ...args]);
  const [daemon] = JSON.parse(docker('context', 'inspect', context));
  requireSafe(daemon.Endpoints?.docker?.Host?.startsWith('unix://'), 'daemon is not local');
  const compose = (...args: string[]) => run(`${root}bin/e2e`, ['compose', ...args]);
  const config = JSON.parse(compose('config', '--format', 'json'));
  // bin/e2e chooses the managed project from E2E_STACK.
  requireSafe(!stack.project || config.name === stack.project, `project is not ${stack.project}`);
  const ids = compose('ps', '-q', ...stack.services).trim().split(/\s+/);
  requireSafe(ids.length === stack.services.length && ids.every(id => /^[a-f0-9]{64}$/.test(id)), 'services missing or ambiguous');
  const containers: Container[] = JSON.parse(docker('inspect', ...ids));
  const selected: Record<string, Container> = {};
  const network = config.networks.default.name;
  const publicNetwork: string = config.networks[PUBLIC_NETWORK.key]?.name;
  const publicOnly = stack.publicOnly ?? [];
  requireSafe(publicNetwork && [...stack.publicMembers, ...publicOnly].every(s => stack.services.includes(s)),
    'public network is not declared');
  for (const service of stack.services) {
    const matches = containers.filter(c => c.Config.Labels['com.docker.compose.service'] === service);
    requireSafe(matches.length === 1, `service ${service} is ambiguous`);
    const c = selected[service] = matches[0];
    requireSafe(c.State.Running && c.Config.Labels['com.docker.compose.project'] === config.name,
      `service ${service} is not running in the managed project`);
    const namespaceOwner = stack.networkNamespace?.[service];
    if (namespaceOwner) {
      const owner = containers.find(x => x.Config.Labels['com.docker.compose.service'] === namespaceOwner);
      requireSafe(owner && c.HostConfig.NetworkMode === `container:${owner.Id}` &&
        Object.keys(c.NetworkSettings.Networks).length === 0 && !c.HostConfig.Dns?.length &&
        !c.HostConfig.DnsSearch?.length && !c.HostConfig.ExtraHosts?.length,
      `service ${service} network namespace override`);
      continue;
    }
    // Members are on exactly default and the public network; a public-only
    // service is on exactly the public network; everyone else on default only.
    const member = stack.publicMembers.includes(service);
    const only = publicOnly.includes(service);
    const networks = only ? [publicNetwork] : member ? [network, publicNetwork].sort() : [network];
    requireSafe(same(Object.keys(c.NetworkSettings.Networks).sort(), networks) &&
      (only || (c.NetworkSettings.Networks[network].Aliases ?? []).includes(service)) &&
      !c.HostConfig.Dns?.length && !c.HostConfig.DnsSearch?.length,
    `service ${service} network or DNS override`);
    if (member || only) {
      validatePublicMember(service, only ? NO_ENDPOINT : c.NetworkSettings.Networks[network],
        c.NetworkSettings.Networks[publicNetwork]);
    }
    requireSafe(same([...(c.HostConfig.ExtraHosts ?? [])].sort(), stack.extraHosts?.[service] ?? []),
      `service ${service} extra_hosts override`);
  }
  const declaration = subfeatureDeclaration();
  for (const service of stack.services) {
    validateMcpGateEntries(service, selected[service].Config.Env, declaration);
  }
  // Some Django workers are not part of the routing/source receipt inventory.
  // They still consume all four gates: inspect their running environment too,
  // without widening the network/source exceptions or changing that inventory.
  const gateProcesses = ['app', 'backend', 'worker', 'worker-exact-aggregation', 'agentcc-gateway', 'agentcc-gateway-2'];
  for (const service of gateProcesses.filter(name => config.services?.[name] && !selected[name])) {
    const ids = compose('ps', '-q', service).trim().split(/\s+/);
    requireSafe(ids.length === 1 && /^[a-f0-9]{64}$/.test(ids[0]), `MCP subfeature service ${service} missing or ambiguous`);
    const observed: Container[] = JSON.parse(docker('inspect', ids[0]));
    requireSafe(observed.length === 1 && observed[0].Id === ids[0] && observed[0].State.Running &&
      observed[0].Config.Labels['com.docker.compose.service'] === service &&
      observed[0].Config.Labels['com.docker.compose.project'] === config.name,
    `MCP subfeature service ${service} is not running in the managed project`);
    validateMcpGateEntries(service, observed[0].Config.Env, declaration);
  }
  const networkId = selected['mock-llm'].NetworkSettings.Networks[network].NetworkID;
  const onDefault = containers.filter(c => !publicOnly.includes(c.Config.Labels['com.docker.compose.service']) && !stack.networkNamespace?.[c.Config.Labels['com.docker.compose.service']]);
  requireSafe(onDefault.every(c => c.NetworkSettings.Networks[network]?.NetworkID === networkId), 'network identity mismatch');
  const [net] = JSON.parse(docker('network', 'inspect', networkId));
  requireSafe(net.Driver === 'bridge' && net.Labels['com.docker.compose.project'] === config.name &&
    net.Labels['com.docker.compose.network'] === 'default', 'unmanaged network');
  // The public-classified network: the project's own internal bridge on exactly
  // its subnet, attached to exactly the members (an unlisted container is STOP
  // even when the inspection does not otherwise look at it).
  const members = [...stack.publicMembers, ...publicOnly].map(service => selected[service]);
  const publicNetworkId = members[0].NetworkSettings.Networks[publicNetwork].NetworkID;
  requireSafe(members.every(c => c.NetworkSettings.Networks[publicNetwork].NetworkID === publicNetworkId),
    'public network identity mismatch');
  const [pub] = JSON.parse(docker('network', 'inspect', publicNetworkId));
  requireSafe(pub.Driver === 'bridge' && pub.Labels?.['com.docker.compose.project'] === config.name &&
    pub.Labels?.['com.docker.compose.network'] === PUBLIC_NETWORK.key, 'unmanaged public network');
  requireSafe(pub.Internal === false, 'public network must support loopback-published browser access');
  requireSafe(pub.IPAM?.Config?.length === 1 && pub.IPAM.Config[0].Subnet === PUBLIC_NETWORK.subnet,
    `public network subnet is not ${PUBLIC_NETWORK.subnet}`);
  requireSafe(same(Object.keys(pub.Containers ?? {}).sort(), members.map(c => c.Id).sort()),
    'public network has an unlisted member');
  // Each harness port is Docker-published by its service, or (internal default
  // network, no published ports) proven to reach it through the runner's loopback
  // forwarder, by its declaration and by active probes delivered to the inspected
  // container; without a forwarder declaration the first unpublished one STOPs as before.
  const unpublished: UnpublishedEndpoint[] = [];
  for (const [service, port, endpoint] of stack.endpoints) {
    if (selected[service].NetworkSettings.Ports[port]?.some(p =>
      p.HostPort === new URL(endpoint).port && ['127.0.0.1', '0.0.0.0', '::'].includes(p.HostIp))) continue;
    unpublished.push({ service, containerPort: port, hostPort: Number(new URL(endpoint).port) });
  }
  const forwarded = unpublished.length ? attestLoopbackForwards(unpublished, { project: config.name, network, net,
    selected, inspect: ids => JSON.parse(docker('inspect', ...ids)), proc: proc ?? linuxProc(), context,
    credentials: probeCredentials(), probe }) : [];
  for (const [service, destination, source, command, wrongSource] of stack.sources) {
    const c = selected[service];
    const mounts = c.Mounts.filter(m => m.Destination === destination);
    requireSafe(mounts.length === 1 && mounts[0].Type === 'bind' && !mounts[0].RW, `unexpected ${service} source mount`);
    requireSafe(realpathSync(mounts[0].Source) === realpathSync(source), wrongSource ?? `unexpected ${service} source mount`);
    if (command) requireSafe(same(c.Config.Cmd, command), `unexpected ${service} command`);
    requireSafe(statSync(source).mtimeMs <= Date.parse(c.State.StartedAt), `${service} source changed after startup`);
  }
  // The routing contract is the stock file's; another profile must be exactly
  // stock plus its declared delta (lib/profile.ts).
  const stockSource = readFileSync(gatewayFile, 'utf8');
  validateMockRouting(stockSource, evalBackground);
  if (stack.profile !== 'stock') validateProfileSource(stack.profile, stockSource, readFileSync(profileFile(stack.profile), 'utf8'));
  const background = stack.check({ context, network, selected, config, run, docker });
  return { context, project: config.name, networkId, publicNetworkId, profile: stack.profile,
    gatewaySha: hash(readFileSync(profileFile(stack.profile))),
    sourceHashes: Object.fromEntries(stack.sources.map(([, , source]) => [source.slice(root.length), `sha256:${hash(readFileSync(source))}`])),
    mockSha: hash(readFileSync(mockFile)), ...(background ? { background } : {}), services: stack.services.map(service => ({ service,
      id: selected[service].Id, image: selected[service].Image, startedAt: selected[service].State.StartedAt })),
    ...(forwarded.length ? { forwarded } : {}) };
}

function distributedMockStack(evalBackground: boolean, exportSinks = false): MockStack {
  return {
    // With the export opt-in, the storage sink the S3 override names is this
    // project's own container, on its default network only.
    services: ['agentcc-gateway', 'mock-llm', 'mock-slack', 'webhook-sink', 'smtp-sink', 'backend', 'worker', 'frontend',
      'postgres', 'clickhouse', ...(evalBackground ? ['temporal'] : []), ...(exportSinks ? ['storage-sink'] : [])],
    publicMembers: PUBLIC_NETWORK.members.distributed,
    profile: 'stock',
    endpoints: [
      ['frontend', '80/tcp', E2E.appUrl], ['backend', '80/tcp', E2E.apiUrl],
      ['agentcc-gateway', '8080/tcp', E2E.gatewayUrl], ['postgres', '5432/tcp', E2E.pgUrl],
      ['clickhouse', '8123/tcp', E2E.chUrl], ['mock-slack', '8080/tcp', E2E.slackMockUrl],
      ['mock-llm', '8080/tcp', E2E.mockLlmUrl], ['webhook-sink', '8080/tcp', E2E.webhookSinkUrl],
      ['smtp-sink', '8025/tcp', E2E.mailSinkUrl],
    ],
    sources: [
      ['agentcc-gateway', '/app/config.yaml', gatewayFile, ['--config', '/app/config.yaml'], profileMountReason('stock')],
      ['mock-llm', '/srv/server.mjs', mockFile, ['node', '/srv/server.mjs']],
      ['mock-slack', '/srv/server.mjs', `${root}e2e/stack/mock-slack/server.mjs`, ['node', '/srv/server.mjs']],
      ['webhook-sink', '/srv/server.mjs', sinkFile, ['node', '/srv/server.mjs']],
    ],
    check: ({ context, network, selected, config, run, docker }) => {
      if (evalBackground) {
        for (const service of ['agentcc-gateway', 'mock-llm']) validateBackgroundMockMounts(service, selected[service].Mounts);
      }
      requireSafe(same(selected['agentcc-gateway'].Config.Entrypoint, ['/app/agentcc-gateway']) &&
        same(selected['mock-llm'].Config.Entrypoint, ['docker-entrypoint.sh']) &&
        same(selected['mock-slack'].Config.Entrypoint, ['docker-entrypoint.sh']) &&
        same(selected['webhook-sink'].Config.Entrypoint, ['docker-entrypoint.sh']) &&
        selected['mock-slack'].Mounts.length === 1 && selected['webhook-sink'].Mounts.length === 1,
      'unexpected gateway/mock entrypoint or mount');
      const environments: Record<string, Record<string, string>> = {};
      for (const service of ['agentcc-gateway', 'mock-llm', 'webhook-sink', 'backend', 'worker']) {
        const env = environments[service] = containerEnvironment(selected[service]);
        validateMockEnvironment(service, env, evalBackground, selected[service].Mounts);
      }
      // The export flows' opt-in: every declared override is present, so no
      // export there can reach a vendor's real host.
      if (exportSinks) {
        for (const service of EXPORTING_SERVICES) {
          for (const [key, value] of Object.entries(EXPORT_ENDPOINTS)) {
            requireSafe(environments[service][key] === value, `required ${service} ${key} mismatch`);
          }
        }
      }
      if (!evalBackground) return undefined;
      // No tag resolution against a registry: local image inspect only. Main must
      // supply the same version pins used to recreate the managed application.
      for (const service of ['backend', 'worker', 'agentcc-gateway', 'mock-llm']) {
        const [image] = JSON.parse(docker('image', 'inspect', config.services[service].image));
        requireSafe(image.Id === selected[service].Image, `${service} configured image differs from running image`);
      }
      requireSafe(selected.backend.Image === selected.worker.Image, 'backend/worker image mismatch');
      const paths = ['entrypoint.sh', 'tfc/settings/settings.py', 'tfc/logging/sentry.py', 'analytics/utils.py', 'analytics/mixpanel_util.py',
        'tfc/management/commands/start_temporal_worker.py', 'tfc/temporal/__init__.py',
        'tfc/temporal/common/client.py', 'tfc/temporal/common/registry.py', 'tfc/temporal/common/worker.py',
        'tracer/tasks/eval_clustering.py', 'tracer/services/eval_tasks/run_entry.py', 'tracer/ee_boundary.py',
        'tracer/queries/eval_clustering.py', 'tracer/utils/eval_clustering.py',
        'ee/usage/services/gateway_llm_client.py', 'ee/agenthub/trace_scanner/eval_cluster_title.py',
        'agentic_eval/core/embeddings/serving_client.py', 'agentic_eval/core/embeddings/embedding_manager.py',
        'agentic_eval/core/utils/model_config.py'];
      const sourceHashes = Object.fromEntries(paths.map(path => [path, hash(readFileSync(`${root}futureagi/${path}`))]));
      for (const [packaged, source] of Object.entries({
        'tfc/settings/e2e.py': 'settings_e2e.py',
        'tfc/urls_e2e.py': 'urls_e2e.py',
        'tracer/views/e2e_feed_issue.py': 'e2e_feed_issue.py',
      })) {
        sourceHashes[packaged] = hash(readFileSync(`${root}e2e/stack/backend/${source}`));
      }
      const probeSource = readFileSync(`${root}e2e/lib/managed-mock-background.py`, 'utf8');
      const addresses = Object.fromEntries(['temporal', 'mock-llm', 'agentcc-gateway'].map(service =>
        [service, selected[service].NetworkSettings.Networks[network].IPAddress]));
      const processes = ['backend', 'worker'].map(service => {
        requireSafe(selected[service].Mounts.length === 0, `${service} has application mounts`);
        // Read-only process/source/constructor/GET-health and Describe RPCs only;
        // no Django setup, completion/embed call, registration or task submission.
        return JSON.parse(run('docker', ['--context', context, 'exec', '-i', '-e', 'PYTHONDONTWRITEBYTECODE=1',
          selected[service].Id, 'python', '-I', '-B', '-c', probeSource], JSON.stringify({ service, sourceHashes,
          containerId: selected[service].Id, environment: environments[service], addresses })));
      });
      return { capability: 'eval-clustering', sourceHashes, probeSha: hash(probeSource), processes };
    },
  };
}

/** custom_model.py:248 validates with a LIVE completion before storing the row.
 * Keep the preflight inside registration so callers cannot accidentally invert it.
 */
export async function registerMockModel(actor: TestActor, probe: StateProbe,
  assertStack: () => unknown = () => inspectManagedMock()) {
  await assertStack();
  const existing = await probe.pg('SELECT id FROM model_hub_customaimodel WHERE organization_id=$1 AND user_model_id=$2 AND deleted=false',
    [actor.organizationId, MOCK_MODEL]);
  requireSafe(existing.length === 0, 'model already exists in this test organization');
  const created = await actor.api.post<{ result: { data: { id: string } } }>('/model-hub/custom_models/create/', {
    model_provider: 'openai', model_name: MOCK_MODEL, input_token_cost: 0, output_token_cost: 0,
    config_json: { key: MOCK_KEY, api_base: MOCK_BASE },
  });
  const id = created.result.data.id;
  const rows = () => probe.pg<{ id: string; provider: string; workspace_id: string; key_config: unknown }>(
    `SELECT id,provider,workspace_id,key_config FROM model_hub_customaimodel
     WHERE organization_id=$1 AND user_model_id=$2 AND deleted=false`, [actor.organizationId, MOCK_MODEL]);
  const initial = await rows();
  requireSafe(initial.length === 1 && initial[0].id === id, 'registered model identity mismatch');
  const fingerprint = hash(JSON.stringify(initial[0].key_config));
  const assertReady = async () => {
    await assertStack();
    const current = await rows();
    requireSafe(current.length === 1 && current[0].id === id && current[0].provider === 'openai' &&
      current[0].workspace_id === actor.workspaceId && hash(JSON.stringify(current[0].key_config)) === fingerprint,
    'custom-model identity, credentials or route changed');
  };
  await assertReady();
  return { id, model: MOCK_MODEL, assertReady };
}
