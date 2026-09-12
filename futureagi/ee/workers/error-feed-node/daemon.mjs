import {readFile} from 'node:fs/promises';
import {hostname} from 'node:os';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
import kafkaModule from 'kafkajs';
import SnappyCodec from 'kafkajs-snappy';
import {gatewayConfig} from './gateway-provider.mjs';
import {createControlClient} from './control-client.mjs';
import {recordKafkaBatch, runCoordinator} from './coordinator.mjs';
import {investigateTrace} from './investigation.mjs';

const {Kafka, logLevel, CompressionTypes, CompressionCodecs} = kafkaModule;
CompressionCodecs[CompressionTypes.Snappy] = SnappyCodec;

async function secret(env, name) {
  const value = env[name + '_FILE'] ? (await readFile(env[name + '_FILE'], 'utf8')).trim() : env[name];
  if (!value || /[\r\n]/.test(value)) throw new Error('Missing or invalid ' + name);
  return value;
}

export async function runDaemon(env = process.env, signal) {
  const brokers = (env.OMEGA_KAFKA_BROKERS ?? '').split(',').map(item => item.trim());
  if (brokers.some(item => !item) || !env.OMEGA_ENGINE_VERSION || !env.OMEGA_REPORT_SPOOL) throw new Error('Kafka, engine version and mounted report spool are required');
  const concurrency = Number(env.OMEGA_CONCURRENCY ?? '4');
  if (!Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > 50) throw new Error('Invalid concurrency');
  const config = await gatewayConfig(env);
  const control = createControlClient({baseUrl: env.OMEGA_DJANGO_URL, token: await secret(env, 'OMEGA_INTERNAL_API_SECRET')});
  const clickhouse = {baseUrl: env.OMEGA_CLICKHOUSE_URL, database: env.OMEGA_CLICKHOUSE_DATABASE || 'default',
    username: env.OMEGA_CLICKHOUSE_USERNAME, password: await secret(env, 'OMEGA_CLICKHOUSE_PASSWORD')};
  if (!clickhouse.username || !clickhouse.baseUrl) throw new Error('Dedicated read-only ClickHouse credentials required');
  const stop = new AbortController();
  const combined = signal ? AbortSignal.any([signal, stop.signal]) : stop.signal;
  const kafka = new Kafka({clientId: 'omega-error-feed', brokers, logLevel: logLevel.NOTHING,
    ssl: env.OMEGA_KAFKA_TLS === 'true',
    ...(env.OMEGA_KAFKA_USERNAME ? {sasl: {mechanism: 'scram-sha-512', username: env.OMEGA_KAFKA_USERNAME,
      password: await secret(env, 'OMEGA_KAFKA_PASSWORD')}} : {}),
    connectionTimeout: 10000, requestTimeout: 15000,
    retry: {retries: 5, maxRetryTime: 15000}});
  const consumer = kafka.consumer({groupId: env.OMEGA_KAFKA_GROUP || 'omega-error-feed-v1',
    allowAutoTopicCreation: false, maxBytesPerPartition: 128 * 1024, maxBytes: 1024 * 1024,
    sessionTimeout: 45000, heartbeatInterval: 3000});
  consumer.on(consumer.events.CRASH, () => stop.abort());
  let running;
  try {
    await consumer.connect();
    await consumer.subscribe({topics: [env.OMEGA_KAFKA_TOPIC || 'error-feed.trace-available.v1'], fromBeginning: true});
    await consumer.run({eachBatchAutoResolve: false, autoCommit: true, autoCommitThreshold: 1,
      partitionsConsumedConcurrently: Math.min(concurrency, 4),
      eachBatch: async payload => {
        await recordKafkaBatch(payload, control, combined);
        await payload.commitOffsetsIfNecessary();
      }});
    running = runCoordinator({control, spool: env.OMEGA_REPORT_SPOOL, workerId: env.OMEGA_WORKER_ID || hostname(),
      engineVersion: env.OMEGA_ENGINE_VERSION, concurrency, signal: combined,
      investigate: (claim, {signal: runSignal}) => investigateTrace(claim, {gatewayConfig: config, clickhouse,
        scratchRoot: env.OMEGA_SCRATCH_DIR || '/tmp', signal: runSignal}),
      onError: (_error, attemptId) => process.stderr.write(JSON.stringify({event: 'omega_work_failed', attempt_id: attemptId ?? null}) + '\n')});
    await running;
    if (stop.signal.aborted && !signal?.aborted) throw new Error('Kafka consumer crashed');
  } finally {
    stop.abort();
    await consumer.disconnect();
    await running;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const stop = new AbortController();
  process.once('SIGTERM', () => stop.abort());
  process.once('SIGINT', () => stop.abort());
  runDaemon(process.env, stop.signal).catch(() => {
    process.stderr.write('Omega daemon stopped: configuration or dependency failure.\n');
    process.exitCode = 1;
  });
}
