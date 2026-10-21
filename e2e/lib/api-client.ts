import type { APIRequestContext } from '@playwright/test';

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

  private async send<T>(method: 'get' | 'post' | 'patch' | 'delete', path: string,
                        opts: { params?: Record<string, string | number>; data?: unknown } = {}): Promise<T> {
    const res = await this.req[method](`${this.baseURL}${path}`, {
      headers: this.headers,
      params: opts.params,
      data: opts.data,
    });
    const raw = await res.text();
    let body: unknown;
    try { body = JSON.parse(raw); } catch { body = { raw }; }
    if (res.status() >= 400) throw new ApiError(res.status(), path, body);
    return body as T;
  }

  get<T>(path: string, params?: Record<string, string | number>) { return this.send<T>('get', path, { params }); }
  post<T>(path: string, data?: unknown) { return this.send<T>('post', path, { data }); }
  patch<T>(path: string, data?: unknown) { return this.send<T>('patch', path, { data }); }
  delete(path: string) { return this.send<void>('delete', path); }
}
