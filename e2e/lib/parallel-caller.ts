// Start all calls behind one microtask barrier; retain every response/error so
// scope-race walks can assert the complete set, not just the first rejection.
export async function parallelCalls<T>(calls: readonly (() => Promise<T>)[]): Promise<PromiseSettledResult<T>[]> {
  if (!calls.length || calls.length > 64) throw new Error('parallelCalls needs 1..64 calls');
  let release!: () => void;
  const barrier = new Promise<void>(resolve => { release = resolve; });
  const started = calls.map(async call => { await barrier; return call(); });
  release();
  return Promise.allSettled(started);
}
