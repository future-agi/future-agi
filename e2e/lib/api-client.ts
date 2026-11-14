import { test, type APIRequestContext } from '@playwright/test';

type Method = 'get' | 'head' | 'post' | 'put' | 'patch' | 'delete';

function errorSummary(error: unknown): string {
  // Playwright appends request/response headers to its Call log. Do not copy
  // credentials from that log into annotations or warnings.
  return error instanceof Error ? error.message.split('\n')[0] : String(error);
}

function isResponseLessReset(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  if (('status' in error && error.status != null) || ('statusCode' in error && error.statusCode != null)) return false;
  // Playwright buffers the body before resolving the request, so rejection
  // alone does NOT mean no response arrived. Its pinned fetch.ts logs each
  // received status as "← <status>" before reading the body (also redirects).
  if (/(?:^|\n)\s*(?:-\s*)?←\s+\d{3}\b/.test(error.message)) return false;
  const summary = errorSummary(error);
  return !/timeout|timed out/i.test(summary) && /socket hang up|\bECONNRESET\b|\bEPIPE\b/i.test(summary);
}

function annotateRetry(method: Method, path: string, error: unknown): void {
  const description = `${method.toUpperCase()} ${path}: ${errorSummary(error)}`;
  let info;
  try { info = test.info(); } catch {
    // Worker fixtures and standalone harness scripts have no current test.
    console.warn(`[api-transport-retry] ${description}`);
    return;
  }
  info.annotations.push({ type: 'api-transport-retry', description });
}

export interface Tokens { access: string; refresh: string }

export class ApiError extends Error {
  constructor(readonly status: number, readonly path: string, readonly body: unknown) {
    super(`API ${status} on ${path}: ${JSON.stringify(body).slice(0, 500)}`);
  }
}

export class ApiClient {
  constructor(
    private req: APIRequestContext,
    readonly baseURL: string,
    private headers: Record<string, string> = {},
  ) {}

  withAuth(tokens: Tokens, organizationId?: string, workspaceId?: string): ApiClient {
    return new ApiClient(this.req, this.baseURL, {
      Authorization: `Bearer ${tokens.access}`,
      ...(organizationId ? { 'X-Organization-Id': organizationId } : {}),
      ...(workspaceId ? { 'X-Workspace-Id': workspaceId } : {}),
    });
  }

  private async send<T>(method: Method, path: string,
                        opts: { params?: Record<string, string | number>; data?: unknown } = {}): Promise<T> {
    const request = () => this.req[method](`${this.baseURL}${path}`, {
      headers: this.headers,
      params: opts.params,
      data: opts.data,
      maxRetries: 0, // This wrapper owns the one retry; never nest Playwright retries.
    });
    let res;
    try {
      res = await request();
    } catch (firstError) {
      // RFC 9110 §9.2.2 permits replaying an idempotent request after a closed
      // connection. Deliberately narrower here: GET/HEAD, no received status,
      // a recognized reset only, and exactly one retry (no delay or polling).
      if ((method !== 'get' && method !== 'head') || !isResponseLessReset(firstError)) throw firstError;
      annotateRetry(method, path, firstError);
      try {
        res = await request();
      } catch (retryError) {
        throw new AggregateError([firstError, retryError],
          `API ${method.toUpperCase()} ${path} transport retry failed; first: ${errorSummary(firstError)}; retry: ${errorSummary(retryError)}`);
      }
    }
    // Keep body parsing, HTTP errors and callers' assertions outside the retry.
    const raw = await res.text();
    let body: unknown;
    try { body = JSON.parse(raw); } catch { body = { raw }; }
    if (res.status() >= 400) throw new ApiError(res.status(), path, body);
    return body as T;
  }

  get<T>(path: string, params?: Record<string, string | number>) { return this.send<T>('get', path, { params }); }
  head(path: string, params?: Record<string, string | number>) { return this.send<void>('head', path, { params }); }
  post<T>(path: string, data?: unknown) { return this.send<T>('post', path, { data }); }
  put<T>(path: string, data?: unknown) { return this.send<T>('put', path, { data }); }
  patch<T>(path: string, data?: unknown) { return this.send<T>('patch', path, { data }); }
  delete(path: string) { return this.send<void>('delete', path); }
}
