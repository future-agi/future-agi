import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, realpathSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parseDocument } from 'yaml';
import { E2E } from './env';
import type { TestActor } from './provisioning';
import type { StateProbe } from './state-probe';

// docker-compose.yml backend-env; gateway.e2e.yaml providers.openai.
export const MOCK_MODEL = 'gpt-4o';
export const MOCK_BASE = 'http://agentcc-gateway:8080/v1';
export const MOCK_SERVING_BASE = 'http://mock-llm:8080';
const MOCK_KEY = 'local-dev-only-shared-secret-replace-me';
const root = fileURLToPath(new URL('../../', import.meta.url));
const gatewayFile = `${root}e2e/stack/gateway.e2e.yaml`;
const mockFile = `${root}e2e/stack/mock-llm/server.mjs`;
const hash = (value: string | Buffer) => createHash('sha256').update(value).digest('hex');
const requireSafe = (ok: unknown, reason: string): void => {
  if (!ok) throw new Error(`STOP: managed mock ${reason}`);
};
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** Strict safety contract, not a general gateway config validator. No env expansion,
 * aliases, extra providers, presets, model maps, mirrors, tools or control plane.
 * Gateway config.Load expands env; registry.NewRegistry applies these routing knobs.
 */
export function validateMockRouting(source: string, evalBackground = false): void {
  const doc = parseDocument(source, { uniqueKeys: true });
  requireSafe(doc.errors.length === 0, 'invalid gateway YAML');
  let cfg;
  try { cfg = doc.toJS({ maxAliasCount: 0 }); }
  catch { throw new Error('STOP: managed mock aliased gateway YAML'); }
  requireSafe(cfg && same(Object.keys(cfg).sort(), ['logging', 'providers', 'server']), 'unexpected gateway sections');
  requireSafe(same(Object.keys(cfg.providers ?? {}), ['openai']), 'provider set is not mock-only');
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
    'idle_timeout', 'shutdown_timeout', 'max_request_body_size', 'default_request_timeout'].includes(k)) &&
    cfg.server.port === 8080 && cfg.server.host === '0.0.0.0', 'unexpected gateway server settings');
  requireSafe(same(Object.keys(cfg.logging), ['level']) &&
    ['debug', 'info', 'warn', 'error'].includes(cfg.logging.level), 'unexpected logging configuration');
  requireSafe(!JSON.stringify(cfg).includes('$'), 'environment-dependent gateway configuration');
}

/** Required values are checked even when absent. Empty optional notification
 * keys mean disabled per settings.py; no license or generic-provider fallback.
 * Pure validation so offline tests never need Docker, API or SDK requests.
 */
export function validateMockEnvironment(service: string, env: Record<string, string>, evalBackground = false,
  mounts: Container['Mounts'] = []): void {
  const allowed: Record<string, string> = { AGENTCC_INTERNAL_API_KEY: MOCK_KEY,
    AGENTCC_ADMIN_TOKEN: 'local-dev-only-admin-token-replace-me',
    AGENTCC_INTERNAL_URL: 'http://agentcc-gateway:8080',
    AGENTCC_GATEWAY_INTERNAL_URL: 'http://agentcc-gateway:8080' };
  for (const [key, value] of Object.entries(env)) {
    if (!value) continue;
    requireSafe(!/^(https?_proxy|all_proxy|node_options|ld_preload|pythonpath|pythonstartup)$/i.test(key),
      `${service} has a proxy/preload override`);
    if (key.startsWith('AGENTCC_')) requireSafe(allowed[key] === value, `${service} has an unsupported gateway override`);
    if (/_API_KEY$/.test(key) || ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'].includes(key)) {
      requireSafe(value === MOCK_KEY || value === 'e2e-mock', `${service} has a non-mock provider key`);
    }
    if (evalBackground) {
      if (service === 'agentcc-gateway' && key === 'GOOGLE_APPLICATION_CREDENTIALS' &&
          value === '/app/Vertex_AI_Creds.json') {
        validateBackgroundMockMounts(service, mounts);
        continue; // Exact root-Compose /dev/null suppression, never a credential.
      }
      requireSafe(!/^(EE_LICENSE_KEY|SENTRY_DSN|SLACK_.*|DEPLOYMENT_TELEMETRY_SLACK_WEBHOOK|ERROR_LOGS_WEBHOOK|MIX_PANEL_TOKEN|MAILGUN_.*|SMTP_.*|SENDGRID_.*|RESEND_.*|AWS_SESSION_TOKEN|GOOGLE_APPLICATION_CREDENTIALS)$/.test(key),
        `${service} has a license, notification or credential override`);
      if (key === 'EMAIL_BACKEND') requireSafe(value === 'django.core.mail.backends.console.EmailBackend', 'nonlocal email backend');
      if (key === 'SENTRY_ENABLED') requireSafe(value === 'false', 'Sentry must be disabled');
    }
  }
  if (!evalBackground) return;
  if (service === 'agentcc-gateway') {
    for (const key of ['AGENTCC_INTERNAL_API_KEY', 'AGENTCC_ADMIN_TOKEN']) {
      requireSafe(env[key] === allowed[key], `required ${service} ${key} mismatch`);
    }
  }
  if (!['backend', 'worker'].includes(service)) return;
  const required = { ...allowed, MODEL_SERVING_URL: MOCK_SERVING_BASE, ENV_TYPE: 'local',
    EE_LICENSE_KEY: '', NO_STARTUP_DB_MUTATIONS: 'true', OTEL_ENABLED: 'false',
    FUTURE_AGI_TELEMETRY_DISABLED: 'true', TEMPORAL_HOST: 'temporal:7233', TEMPORAL_NAMESPACE: 'default',
    DJANGO_SETTINGS_MODULE: 'tfc.settings.settings', MAILGUN_API_KEY: '' };
  for (const [key, value] of Object.entries(required)) requireSafe(env[key] === value, `required ${service} ${key} mismatch`);
  if (service === 'worker') {
    requireSafe(env.TEMPORAL_ALL_QUEUES === 'true' && env.TEMPORAL_EXCLUDED_QUEUES === 'simulation_runner',
      'worker must include the native agent_compass queue');
  }
}

interface Container {
  Id: string; Image: string; State: { Running: boolean; StartedAt: string };
  Config: { Labels: Record<string, string>; Env: string[]; Cmd: string[]; Entrypoint: string[] };
  HostConfig: { ExtraHosts: string[] | null; Dns?: string[]; DnsSearch?: string[] };
  Mounts: { Type: string; Source: string; Destination: string; RW: boolean }[];
  NetworkSettings: { Networks: Record<string, { NetworkID: string; Aliases: string[]; IPAddress: string }>;
    Ports: Record<string, { HostIp: string; HostPort: string }[] | null> };
}

export function validateBackgroundMockMounts(service: string, mounts: Container['Mounts']): void {
  const gateway = service === 'agentcc-gateway';
  requireSafe(gateway || service === 'mock-llm', 'unsupported mock mount service');
  const primary = mounts.filter(m => m.Destination === (gateway ? '/app/config.yaml' : '/srv/server.mjs'));
  // inspectManagedMock separately pins this primary source's real path/hash.
  requireSafe(primary.length === 1 && primary[0].Type === 'bind' && primary[0].RW === false,
    `unexpected ${service} source mount`);
  requireSafe(mounts.length === (gateway ? 2 : 1), `unexpected additional ${service} mount`);
  if (gateway) {
    // Root Compose intentionally masks any packaged Google credential file.
    const suppressed = mounts.filter(m => m.Destination === '/app/Vertex_AI_Creds.json');
    requireSafe(suppressed.length === 1 && suppressed[0].Type === 'bind' &&
      suppressed[0].Source === '/dev/null' && suppressed[0].RW === false,
    'gateway credential suppression must be an exact read-only /dev/null bind');
  }
}
export interface MockReceipt {
  context: string; project: string; networkId: string; gatewaySha: string; mockSha: string;
  services: { service: string; id: string; image: string; startedAt: string }[];
  background?: { capability: 'eval-clustering'; sourceHashes: Record<string, string>; probeSha: string;
    processes: unknown[] };
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

/** Read-only Docker calls only. Never print compose config / inspect output: it
 * contains secrets. CI may use a verified local default daemon; local users must
 * select their context explicitly. An unattested attach target FAILS, never skips.
 */
export function inspectManagedMock({ evalBackground = false }: { evalBackground?: boolean } = {}): MockReceipt {
  for (const endpoint of [E2E.appUrl, E2E.apiUrl, E2E.gatewayUrl, E2E.pgUrl, E2E.chUrl]) {
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
  // bin/e2e chooses the managed project; names below are Compose SERVICE keys,
  // never machine-specific/generated container names.
  const serviceNames = ['agentcc-gateway', 'mock-llm', 'backend', 'worker', 'frontend', 'postgres', 'clickhouse'];
  if (evalBackground) serviceNames.push('temporal');
  const ids = compose('ps', '-q', ...serviceNames).trim().split(/\s+/);
  requireSafe(ids.length === serviceNames.length && ids.every(id => /^[a-f0-9]{64}$/.test(id)), 'services missing or ambiguous');
  const containers: Container[] = JSON.parse(docker('inspect', ...ids));
  const selected: Record<string, Container> = {};
  const network = config.networks.default.name;
  for (const service of serviceNames) {
    const matches = containers.filter(c => c.Config.Labels['com.docker.compose.service'] === service);
    requireSafe(matches.length === 1, `service ${service} is ambiguous`);
    const c = selected[service] = matches[0];
    requireSafe(c.State.Running && c.Config.Labels['com.docker.compose.project'] === config.name,
      `service ${service} is not running in the managed project`);
    requireSafe(same(Object.keys(c.NetworkSettings.Networks), [network]) &&
      c.NetworkSettings.Networks[network].Aliases.includes(service) && !c.HostConfig.ExtraHosts?.length &&
      !c.HostConfig.Dns?.length && !c.HostConfig.DnsSearch?.length,
    `service ${service} network or DNS override`);
  }
  const networkId = selected['mock-llm'].NetworkSettings.Networks[network].NetworkID;
  requireSafe(containers.every(c => c.NetworkSettings.Networks[network].NetworkID === networkId), 'network identity mismatch');
  const [net] = JSON.parse(docker('network', 'inspect', networkId));
  requireSafe(net.Driver === 'bridge' && net.Labels['com.docker.compose.project'] === config.name &&
    net.Labels['com.docker.compose.network'] === 'default', 'unmanaged network');
  for (const [service, port, endpoint] of [
    ['frontend', '80/tcp', E2E.appUrl], ['backend', '80/tcp', E2E.apiUrl],
    ['agentcc-gateway', '8080/tcp', E2E.gatewayUrl], ['postgres', '5432/tcp', E2E.pgUrl],
    ['clickhouse', '8123/tcp', E2E.chUrl],
  ]) {
    requireSafe(selected[service].NetworkSettings.Ports[port]?.some(p =>
      p.HostPort === new URL(endpoint).port && ['127.0.0.1', '0.0.0.0', '::'].includes(p.HostIp)),
    `endpoint is not published by ${service}`);
  }
  for (const [service, destination, source, command] of [
    ['agentcc-gateway', '/app/config.yaml', gatewayFile, ['--config', '/app/config.yaml']],
    ['mock-llm', '/srv/server.mjs', mockFile, ['node', '/srv/server.mjs']],
  ] as const) {
    const c = selected[service];
    const mounts = c.Mounts.filter(m => m.Destination === destination);
    requireSafe(mounts.length === 1 && mounts[0].Type === 'bind' && !mounts[0].RW &&
      realpathSync(mounts[0].Source) === realpathSync(source), `unexpected ${service} source mount`);
    if (evalBackground) validateBackgroundMockMounts(service, c.Mounts);
    requireSafe(same(c.Config.Cmd, command), `unexpected ${service} command`);
    requireSafe(statSync(source).mtimeMs <= Date.parse(c.State.StartedAt), `${service} source changed after startup`);
  }
  requireSafe(same(selected['agentcc-gateway'].Config.Entrypoint, ['/app/agentcc-gateway']) &&
    same(selected['mock-llm'].Config.Entrypoint, ['docker-entrypoint.sh']), 'unexpected gateway/mock entrypoint');
  validateMockRouting(readFileSync(gatewayFile, 'utf8'), evalBackground);
  const environments: Record<string, Record<string, string>> = {};
  for (const service of ['agentcc-gateway', 'mock-llm', 'backend', 'worker']) {
    const env = Object.fromEntries(selected[service].Config.Env.map(s => {
      const i = s.indexOf('='); return [s.slice(0, i), s.slice(i + 1)];
    }));
    environments[service] = env;
    validateMockEnvironment(service, env, evalBackground, selected[service].Mounts);
  }
  let background: MockReceipt['background'];
  if (evalBackground) {
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
    background = { capability: 'eval-clustering', sourceHashes, probeSha: hash(probeSource), processes };
  }
  return { context, project: config.name, networkId, gatewaySha: hash(readFileSync(gatewayFile)),
    mockSha: hash(readFileSync(mockFile)), ...(background ? { background } : {}), services: serviceNames.map(service => ({ service,
      id: selected[service].Id, image: selected[service].Image, startedAt: selected[service].State.StartedAt })) };
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
