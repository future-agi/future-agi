import {setTimeout as delay} from 'node:timers/promises';

export async function publishOutboxBatch({control,producer,topic,signal}) {
  const {events}=await control('/grouping/outbox/',{limit:20},{signal});
  if(!Array.isArray(events)||events.length>20) throw new Error('Invalid grouping outbox batch');
  for(const event of events) {
    signal.throwIfAborted();
    if(!/^[a-f0-9-]{36}$/.test(event.id??'')||Buffer.byteLength(JSON.stringify(event))>4096) {
      throw new Error('Invalid grouping outbox event');
    }
    await producer.send({topic,acks:-1,messages:[{key:event.scope_id,value:JSON.stringify(event)}]});
    // A lost DB acknowledgement causes a duplicate notification, never loss.
    await control(`/grouping/outbox/${event.id}/ack/`,{},{signal});
  }
}

export async function runOutboxPublisher({control,producer,topic,signal,onError}) {
  try {
    while(!signal.aborted) {
      try {await publishOutboxBatch({control,producer,topic,signal});}
      catch(error) {if(!signal.aborted) onError(error);}
      await delay(5000,null,{signal});
    }
  } catch(error) {if(!signal.aborted) throw error;}
}
