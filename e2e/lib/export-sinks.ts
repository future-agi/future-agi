// The destinations the integration export flows (flows/integrations/)
// deliver to. The product exports through its own services
// (futureagi/integrations/temporal/activities.py _export_*); nothing here calls
// a vendor.
// - PostHog takes its host from the connection (self-hosted PostHog), so a flow
//   saves a per-test webhook-sink channel answering as PostHog (`posthogHost`).
// - Datadog, Mixpanel, Amazon S3 and Linear have their hosts fixed in code. The stack
//   points them at local sinks with the server-side overrides of
//   futureagi/integrations/services/endpoints.py, set on backend and worker in
//   e2e/stack/docker-compose.e2e.yml (Distributed only): EXPORT_ENDPOINTS, which
//   the managed-mock inspector requires exactly, and refuses any other value of
//   an INTEGRATIONS_* variable on any container (lib/managed-mock.ts).
// Requests at the fixed channels are shared by every worker, so a flow selects
// its own by the credential it minted (the Datadog API key header, the Mixpanel
// project token, the Linear API key header); objects in the storage sink by the
// key prefix it minted.
import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { gunzipSync } from 'node:zlib';
import { PUBLIC_NETWORK, SCOPE_NAME } from './public-network';

const root = fileURLToPath(new URL('../../', import.meta.url));
const HOOKS = `http://${PUBLIC_NETWORK.pinned['webhook-sink'].alias}:8080`;

/** The vendors the webhook sink answers as (stack/webhook-sink/server.mjs `/as/<vendor>`). */
export type SinkVendor = 'datadog' | 'linear' | 'mixpanel' | 'posthog';

/** The base URL of a sink channel that answers as `vendor`, as the product addresses it. */
export const vendorBase = (channel: string, vendor: SinkVendor) => {
  if (!SCOPE_NAME.test(channel)) throw new Error(`channel ${JSON.stringify(channel)} is not [A-Za-z0-9_.-]{1,80}`);
  return `${HOOKS}/r/${channel}/as/${vendor}`;
};

/** The fixed channels the Datadog, Mixpanel and Linear overrides name. */
export const EXPORT_CHANNELS = {
  datadog: 'datadog-export', mixpanel: 'mixpanel-export', linear: 'linear-export',
} as const;

/** Every endpoint override the Distributed stack sets on backend and worker, exactly. */
export const EXPORT_ENDPOINTS: Readonly<Record<string, string>> = {
  INTEGRATIONS_DATADOG_URL: vendorBase(EXPORT_CHANNELS.datadog, 'datadog'),
  INTEGRATIONS_MIXPANEL_URL: vendorBase(EXPORT_CHANNELS.mixpanel, 'mixpanel'),
  INTEGRATIONS_S3_ENDPOINT_URL: 'http://storage-sink:9000',
  INTEGRATIONS_LINEAR_URL: vendorBase(EXPORT_CHANNELS.linear, 'linear'),
};

/** A self-hosted PostHog host URL for the connection: a per-test channel. */
export const posthogHost = (channel: string) => vendorBase(channel, 'posthog');

/** The prefix of a credential every vendor stand-in refuses as its vendor refuses a wrong
 * key (stack/webhook-sink/server.mjs REFUSED_PREFIX): Datadog 403, PostHog 401, Mixpanel
 * /track 0 and /import 401. */
export const REFUSED_PREFIX = 'e2e-refused-';

/** A minted credential the vendor stand-ins refuse. */
export const refusedCredential = (label: string) => {
  const credential = `${REFUSED_PREFIX}${label}`;
  if (!SCOPE_NAME.test(credential)) throw new Error(`credential ${JSON.stringify(credential)} is not [A-Za-z0-9_.-]{1,80}`);
  return credential;
};

/** A disposable access key pair of the storage sink, allowed only to write and delete
 * objects under one key prefix of its bucket. */
export interface StorageUser { accessKeyId: string; secretAccessKey: string; policy: string }

export interface StoredObject {
  key: string;
  size: number;
  contentType: string;
  contentEncoding: string;
  /** The object's gunzipped JSON Lines, one parsed record per line. */
  records: Record<string, unknown>[];
}

/** One command inside the storage sink, through bin/e2e (DOCKER_HOST, never a context). */
const inSink = (args: string[], input?: string): Buffer => {
  const env = { ...process.env };
  delete env.DOCKER_CONTEXT;
  return execFileSync(`${root}bin/e2e`, ['compose', 'exec', '-T', 'storage-sink', ...args],
    { env, timeout: 60_000, maxBuffer: 32 * 1024 * 1024, input, stdio: [input === undefined ? 'ignore' : 'pipe', 'pipe', 'pipe'] });
};

/** One `mc` call inside the storage sink. */
const mc = (args: string[]): Buffer => inSink(['mc', ...args]);

/** The S3 policy of a storage user: put and delete objects under `prefix/` of `bucket`,
 * nothing else (the connection check writes and deletes `<prefix>/_agentcc_test.txt`; the
 * export only puts objects; integrations/services/cloud_storage_service.py). */
export const storageUserPolicy = (bucket: string, prefix: string) => ({
  Version: '2012-10-17',
  Statement: [{
    Effect: 'Allow', Action: ['s3:PutObject', 's3:DeleteObject'], Resource: [`arn:aws:s3:::${bucket}/${prefix}/*`],
  }],
});

/** The S3-compatible storage sink (the `storage-sink` service, MinIO): one
 * bucket, made at start; read with `mc` inside the container, whose alias
 * `sink` the service's own start command sets. */
export class StorageSink {
  readonly bucket = 'e2e-exports';
  readonly region = 'us-east-1';
  /** The sink's own root user (docker-compose.e2e.yml storage-sink), what a flow enters as the AWS keys. */
  readonly accessKeyId = 'e2e-storage-sink';
  readonly secretAccessKey = 'e2e-storage-sink-password';

  /** Adds a disposable user `name` whose keys may only put and delete objects under
   * `prefix/` of the bucket (storageUserPolicy); removeUser takes it away again. */
  addUser(name: string, prefix: string): StorageUser {
    for (const [kind, value] of [['name', name], ['prefix', prefix]]) {
      if (!SCOPE_NAME.test(value)) throw new Error(`${kind} ${JSON.stringify(value)} is not [A-Za-z0-9_.-]{1,80}`);
    }
    // MinIO takes an access key of 3-20 characters and a secret of 8-40.
    if (name.length > 20) throw new Error(`storage user ${JSON.stringify(name)} is longer than 20 characters`);
    const user: StorageUser = {
      accessKeyId: name, secretAccessKey: `e2e-${randomBytes(16).toString('hex')}`, policy: `${name}-policy`,
    };
    const file = `/tmp/${user.policy}.json`;
    inSink(['sh', '-ec', `cat > ${file}`], JSON.stringify(storageUserPolicy(this.bucket, prefix)));
    mc(['admin', 'policy', 'create', 'sink', user.policy, file]);
    mc(['admin', 'user', 'add', 'sink', user.accessKeyId, user.secretAccessKey]);
    mc(['admin', 'policy', 'attach', 'sink', user.policy, '--user', user.accessKeyId]);
    return user;
  }

  /** Removes the user and its policy; a user already removed is not an error. */
  removeUser(user: StorageUser): void {
    for (const args of [['admin', 'user', 'rm', 'sink', user.accessKeyId], ['admin', 'policy', 'rm', 'sink', user.policy]]) {
      try {
        mc(args);
      } catch (error) {
        const { stdout, stderr } = error as { stdout?: Buffer; stderr?: Buffer };
        if (!/not found|does not exist|specified user does not exist|policy not found/i.test(`${stdout ?? ''}${stderr ?? ''}`)) throw error;
      }
    }
  }

  /** The users the sink has, by access key (`mc admin user list`). */
  users(): string[] {
    return mc(['admin', 'user', 'list', 'sink', '--json']).toString('utf8').split('\n').filter(Boolean)
      .map((line) => (JSON.parse(line) as { accessKey?: string }).accessKey ?? '').filter(Boolean).sort();
  }

  /** Every object under `prefix/`, sorted by key, each with its metadata and gunzipped records. */
  objects(prefix: string): StoredObject[] {
    if (!SCOPE_NAME.test(prefix)) throw new Error(`prefix ${JSON.stringify(prefix)} is not [A-Za-z0-9_.-]{1,80}`);
    let listing: string;
    try {
      listing = mc(['ls', '--json', '--recursive', `sink/${this.bucket}/${prefix}/`]).toString('utf8');
    } catch (error) {
      // A prefix nothing was written under is not an error: it holds no objects.
      const { stdout, stderr } = error as { stdout?: Buffer; stderr?: Buffer };
      if (/does not exist/i.test(`${stdout ?? ''}${stderr ?? ''}`)) return [];
      throw new Error(`storage sink: mc ls failed (${(error as { status?: number }).status ?? 'no status'})`);
    }
    const keys = listing.split('\n').filter(Boolean).map((line) => JSON.parse(line) as { key?: string; status?: string })
      .map((row) => {
        if (row.status !== 'success' || typeof row.key !== 'string') throw new Error('storage sink: unexpected listing');
        return `${prefix}/${row.key}`;
      }).sort();
    return keys.map((key) => {
      const stat = JSON.parse(mc(['stat', '--json', `sink/${this.bucket}/${key}`]).toString('utf8')) as {
        size: number; metadata?: Record<string, string>;
      };
      const meta = Object.fromEntries(Object.entries(stat.metadata ?? {}).map(([k, v]) => [k.toLowerCase(), v]));
      const body = gunzipSync(mc(['cat', `sink/${this.bucket}/${key}`])).toString('utf8');
      return {
        key, size: stat.size, contentType: meta['content-type'] ?? '', contentEncoding: meta['content-encoding'] ?? '',
        records: body.split('\n').filter(Boolean).map((line) => JSON.parse(line) as Record<string, unknown>),
      };
    });
  }
}
