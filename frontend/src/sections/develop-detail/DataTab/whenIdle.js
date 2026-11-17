// Run work when the browser is idle (perf #6), with a deadline so it still
// runs on a busy page; setTimeout where requestIdleCallback is missing.
// Returns a cancel function. Pure apart from the scheduler it is given.
export function whenIdle(fn, { timeout = 1500, scope = globalThis } = {}) {
  if (typeof scope?.requestIdleCallback === "function") {
    const id = scope.requestIdleCallback(() => fn(), { timeout });
    return () => scope.cancelIdleCallback?.(id);
  }
  const id = setTimeout(fn, Math.min(timeout, 300));
  return () => clearTimeout(id);
}

export default whenIdle;
