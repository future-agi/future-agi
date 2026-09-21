import test from 'node:test';
import assert from 'node:assert/strict';
import {createWakeup} from './wakeup.mjs';
import {groupingConfig} from '../grouping-daemon.mjs';

test('Kafka hint waits for healthy poll acknowledgement and wakes sleeping poll',async()=>{
  const wake=createWakeup(),stop=new AbortController();
  let acknowledged=false;
  const waiting=wake.wait(10000,stop.signal);
  const hint=wake.request(stop.signal).then(()=>{acknowledged=true;});
  await waiting;
  assert.equal(acknowledged,false);
  wake.acknowledge();await hint;
  assert.equal(acknowledged,true);
});

test('shutdown cancels unacknowledged hints without committing them',async()=>{
  const wake=createWakeup(),stop=new AbortController();
  const hint=wake.request(stop.signal);
  stop.abort();
  await assert.rejects(()=>hint);
  wake.acknowledge();
});

test('grouping daemon has explicit model/cost and conservative pool settings',()=>{
  assert.throws(()=>groupingConfig({}),/reservation/);
  const env={GROUPING_CALL_RESERVATION_USD:'0.25',GROUPING_MODEL_ID:'google/gemini-3.8-flash',
    GROUPING_EMBEDDING_SERVING_RELEASE:'release-1'};
  const config=groupingConfig(env);
  assert.equal(config.groupingConcurrency,1);
  assert.equal(config.featureConcurrency,2);
  assert.throws(()=>groupingConfig({...env,GROUPING_CONCURRENCY:'100'}),/concurrency/);
});
