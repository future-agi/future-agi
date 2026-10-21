import test from 'node:test';
import assert from 'node:assert/strict';
import {publishOutboxBatch} from './outbox.mjs';

const event={id:'11111111-1111-4111-8111-111111111111',scope_id:'scope',event_kind:'features_pending'};
test('outbox database acknowledgement follows durable Kafka acknowledgement',async()=>{
  const order=[];
  await publishOutboxBatch({signal:new AbortController().signal,topic:'grouping',
    control:async(path)=>{order.push(path);return {events:[event]};},
    producer:{send:async payload=>{order.push('broker');assert.equal(payload.acks,-1);}}});
  assert.deepEqual(order,['/grouping/outbox/','broker',`/grouping/outbox/${event.id}/ack/`]);
});
test('failed broker publication leaves the durable event unacknowledged',async()=>{
  const paths=[];
  await assert.rejects(()=>publishOutboxBatch({signal:new AbortController().signal,topic:'grouping',
    control:async path=>{paths.push(path);return {events:[event]};},
    producer:{send:async()=>{throw new Error('broker unavailable');}}}),/broker unavailable/);
  assert.deepEqual(paths,['/grouping/outbox/']);
});
