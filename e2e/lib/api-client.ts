import type { APIRequestContext } from '@playwright/test';

export interface Tokens { access: string; refresh: string }

export class ApiError extends Error {
  constructor(readonly status: number, readonly path: string, readonly body: unknown) {
    super(`API ${status} on ${path}: ${JSON.stringify(body).slice(0, 500)}`);
  }
}

type Method = 'get' | 'post' | 'put' | 'patch' | 'delete';
interface RequestOptions {
  params?: Record<string, string | number>;
  data?: unknown;
  headers?: Record<string, string>;
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

  /** The status and body whatever the status, for a flow that asserts a refusal or a 202. */
  async request<T = unknown>(method: Method, path: string, opts: RequestOptions = {}): Promise<{ status: number; body: T }> {
    const res = await this.req[method](`${this.baseURL}${path}`, {
      headers: { ...this.headers, ...opts.headers },
      params: opts.params,
      data: opts.data,
    });
    const raw = await res.text();
    let body: unknown;
    try { body = JSON.parse(raw); } catch { body = { raw }; }
    return { status: res.status(), body: body as T };
  }

  private async send<T>(method: Method, path: string, opts: RequestOptions = {}): Promise<T> {
    const { status, body } = await this.request<T>(method, path, opts);
    if (status >= 400) throw new ApiError(status, path, body);
    return body;
  }

  get<T>(path: string, params?: Record<string, string | number>) { return this.send<T>('get', path, { params }); }
  post<T>(path: string, data?: unknown) { return this.send<T>('post', path, { data }); }
  put<T>(path: string, data?: unknown) { return this.send<T>('put', path, { data }); }
  patch<T>(path: string, data?: unknown) { return this.send<T>('patch', path, { data }); }
  delete(path: string) { return this.send<void>('delete', path); }
}
