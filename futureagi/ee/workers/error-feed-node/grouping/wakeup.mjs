// Notifications are hints for durable database work, never the work itself.
// Kafka offsets advance only after a successful coordinator polling round.
export function createWakeup() {
  let pulse = null;
  const acknowledgements = new Set();
  return {
    request(signal) {
      signal?.throwIfAborted();
      return new Promise((resolve,reject)=>{
        const entry={resolve:()=>{cleanup();resolve();}};
        const aborted=()=>{cleanup();reject(signal.reason ?? new Error('Cancelled'));};
        const cleanup=()=>{acknowledgements.delete(entry);signal?.removeEventListener('abort',aborted);};
        acknowledgements.add(entry);
        signal?.addEventListener('abort',aborted,{once:true});
        pulse?.();
      });
    },
    acknowledge() { for(const entry of [...acknowledgements]) entry.resolve(); },
    wait(ms,signal) {
      signal.throwIfAborted();
      return new Promise((resolve,reject)=>{
        const cleanup=()=>{clearTimeout(timer);signal.removeEventListener('abort',aborted);pulse=null;};
        const done=()=>{cleanup();resolve();};
        const aborted=()=>{cleanup();reject(signal.reason ?? new Error('Cancelled'));};
        const timer=setTimeout(done,ms);
        pulse=done;
        signal.addEventListener('abort',aborted,{once:true});
      });
    },
  };
}
