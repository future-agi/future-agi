import {readFile} from 'node:fs/promises';
import {hostname} from 'node:os';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
import {createControlClient} from './control-client.mjs';
import {createEmbeddingClient} from './grouping/embedding-client.mjs';
import {runGroupingCoordinator} from './grouping/coordinator.mjs';
import {createWakeup} from './grouping/wakeup.mjs';
import {runOutboxPublisher} from './grouping/outbox.mjs';

async function secret(env,name) {
  const value=env[name+'_FILE'] ? (await readFile(env[name+'_FILE'],'utf8')).trim() : env[name];
  if(!value || /[\r\n]/.test(value)) throw new Error('Missing or invalid '+name);
  return value;
}

export function groupingConfig(env) {
  const reserveUsd=Number(env.GROUPING_CALL_RESERVATION_USD);
  const featureConcurrency=Number(env.GROUPING_FEATURE_CONCURRENCY ?? 2);
  const groupingConcurrency=Number(env.GROUPING_CONCURRENCY ?? 1);
  if(!Number.isFinite(reserveUsd)||reserveUsd<=0||reserveUsd>100) throw new Error('Explicit per-call reservation required');
  for(const count of [featureConcurrency,groupingConcurrency]) {
    if(!Number.isSafeInteger(count)||count<1||count>10) throw new Error('Invalid grouping concurrency');
  }
  if(env.GROUPING_MODEL_ID!=='google/gemini-3.8-flash') throw new Error('Explicit F6 model required');
  return {reserveUsd,featureConcurrency,groupingConcurrency,
    model:{name:'all-MiniLM-L6-v2',dimension:384,servingRelease:env.GROUPING_EMBEDDING_SERVING_RELEASE}};
}

async function kafkaClient(env) {
  const kafkaModule=(await import('kafkajs')).default;
  const {Kafka,logLevel,CompressionTypes,CompressionCodecs}=kafkaModule;
  CompressionCodecs[CompressionTypes.Snappy]=(await import('kafkajs-snappy')).default;
  const brokers=env.OMEGA_KAFKA_BROKERS.split(',').map(value=>value.trim());
  if(brokers.some(value=>!value)) throw new Error('Invalid Kafka brokers');
  return new Kafka({clientId:'omega-grouping',brokers,logLevel:logLevel.NOTHING,
    ssl:env.OMEGA_KAFKA_TLS==='true',
    ...(env.OMEGA_KAFKA_USERNAME ? {sasl:{mechanism:'scram-sha-512',username:env.OMEGA_KAFKA_USERNAME,
      password:await secret(env,'OMEGA_KAFKA_PASSWORD')}}:{}),
    connectionTimeout:10000,requestTimeout:15000,retry:{retries:3,maxRetryTime:15000}});
}

async function consumeHints(env,wakeup,signal,onError) {
  const kafka=await kafkaClient(env);
  const consumer=kafka.consumer({groupId:env.GROUPING_KAFKA_GROUP||'omega-grouping-v1',
    allowAutoTopicCreation:false,maxBytesPerPartition:128*1024,maxBytes:1024*1024,
    sessionTimeout:45000,heartbeatInterval:3000});
  const stopped=new AbortController();
  const combined=AbortSignal.any([signal,stopped.signal]);
  consumer.on(consumer.events.CRASH,()=>{onError();stopped.abort();});
  try {
    await consumer.connect();
    await consumer.subscribe({topics:[env.GROUPING_KAFKA_TOPIC||'error-feed.grouping-ready.v1'],fromBeginning:true});
    await consumer.run({autoCommit:true,autoCommitThreshold:1,eachBatchAutoResolve:false,
      eachBatch:async({batch,resolveOffset,heartbeat,commitOffsetsIfNecessary,isRunning,isStale})=>{
        for(const message of batch.messages) {
          if(combined.aborted||!isRunning()||isStale()) return;
          // Payload carries no inference input. Even malformed hints only cause
          // a bounded poll; authoritative scope/input always comes from Django.
          let timer;
          try {
            timer=setInterval(()=>{heartbeat().catch(()=>stopped.abort());},3000);
            await wakeup.request(combined);
            if(combined.aborted||!isRunning()||isStale()) return;
            resolveOffset(message.offset);
            await commitOffsetsIfNecessary();
          } finally {clearInterval(timer);}
        }
      }});
    if(!combined.aborted) await new Promise(resolve=>combined.addEventListener('abort',resolve,{once:true}));
  } finally {stopped.abort();await consumer.disconnect();}
}

async function publishHints(env,control,signal,onError) {
  const kafka=await kafkaClient(env);
  const producer=kafka.producer({allowAutoTopicCreation:false});
  try {
    await producer.connect();
    await runOutboxPublisher({control,producer,signal,onError,
      topic:env.GROUPING_KAFKA_TOPIC||'error-feed.grouping-ready.v1'});
  } finally {await producer.disconnect();}
}

export async function runGroupingDaemon(env=process.env,signal) {
  if(!signal) throw new Error('Shutdown signal required');
  const config=groupingConfig(env);
  const {gatewayConfig}=await import('./gateway-provider.mjs');
  const gateway=await gatewayConfig({...env,OMEGA_MODEL_ID:env.GROUPING_MODEL_ID});
  const control=createControlClient({baseUrl:env.OMEGA_DJANGO_URL,token:await secret(env,'OMEGA_INTERNAL_API_SECRET')});
  const embedBatch=createEmbeddingClient({endpoint:env.GROUPING_EMBEDDING_URL,model:config.model,
    timeoutMs:60000,maxRequestBytes:64*1024,maxResponseBytes:8*1024*1024,maxBatchSize:16});
  const stop=new AbortController();
  const combined=AbortSignal.any([signal,stop.signal]);
  const wakeup=createWakeup();
  const log=(event,attemptId)=>process.stderr.write(JSON.stringify({event,attempt_id:attemptId??null})+'\n');
  // Kafka failure never erases durable jobs or stops polling recovery.
  const hints=env.OMEGA_KAFKA_BROKERS
    ? consumeHints(env,wakeup,combined,()=>log('grouping_kafka_unavailable'))
      .catch(()=>{if(!combined.aborted) log('grouping_kafka_unavailable');})
    : Promise.resolve();
  const outbox=env.OMEGA_KAFKA_BROKERS
    ? publishHints(env,control,combined,()=>log('grouping_outbox_retry'))
      .catch(()=>{if(!combined.aborted) log('grouping_outbox_unavailable');})
    : Promise.resolve();
  try {
    await runGroupingCoordinator({...config,control,embedBatch,gatewayConfig:gateway,wakeup,signal:combined,
      workerId:env.GROUPING_WORKER_ID||hostname(),onError:(_error,id)=>log('grouping_work_failed',id)});
  } finally {stop.abort();await Promise.all([hints,outbox]);}
}

if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href) {
  const stop=new AbortController();
  process.once('SIGTERM',()=>stop.abort());
  process.once('SIGINT',()=>stop.abort());
  runGroupingDaemon(process.env,stop.signal).catch(()=>{
    process.stderr.write('Grouping daemon stopped: configuration or dependency failure.\n');
    process.exitCode=1;
  });
}
