import type { APIRequestContext, Request } from '@playwright/test';

type Headers = Record<string, string>;

// First hop only: Playwright owns redirects and may forward this nonsecret header.
// A different port (notably OTLP) is a different pool. Never set a browser default.
export function firstHopOptions<T extends { headers?: Headers }>(target: string | Request, backendURL: string,
  options: T, baseURL?: string) {
  const url = URL.parse(typeof target === 'string' ? target : target.url(), baseURL);
  if (!url || url.protocol !== 'http:' || !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
    || url.origin !== new URL(backendURL).origin) return options;
  // Match Playwright's fetch(Request) precedence, then own Connection regardless of spelling.
  const original = options.headers ?? (typeof target === 'string' ? {} : target.headers());
  const headers = Object.fromEntries(Object.entries(original).filter(([name]) => name.toLowerCase() !== 'connection'));
  return { ...options, headers: { ...headers, Connection: 'close' } };
}

// A view over one public request context, not a new jar, retry layer or disposal owner.
export function backendRequestContext(req: APIRequestContext, backendURL: string, baseURL?: string): APIRequestContext {
  const options = (url: string | Request, opts: Parameters<APIRequestContext['fetch']>[1] = {}) =>
    firstHopOptions(url, backendURL, opts, baseURL);
  return {
    get: (url, opts) => req.get(url, options(url, opts)),
    head: (url, opts) => req.head(url, options(url, opts)),
    post: (url, opts) => req.post(url, options(url, opts)),
    put: (url, opts) => req.put(url, options(url, opts)),
    patch: (url, opts) => req.patch(url, options(url, opts)),
    delete: (url, opts) => req.delete(url, options(url, opts)),
    fetch: (url, opts) => req.fetch(url, options(url, opts)),
    storageState: opts => req.storageState(opts),
    dispose: opts => req.dispose(opts),
    [Symbol.asyncDispose]: () => req[Symbol.asyncDispose](),
    tracing: req.tracing,
  };
}
