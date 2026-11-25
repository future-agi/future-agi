import type { Page, Request, Response } from '@playwright/test';
import { readJsonWithin } from './response-body';

export type Receipt<T> = {
  path: string; method: string; input: Record<string, unknown>;
  scope: { organizationId: string; workspaceId: string }; authorized: boolean;
  status: number; body?: T; error?: string; requestId?: string; contentType?: string;
  startedAt: number; endedAt: number; settled: boolean;
};

// Pinned to scores.js, annotation_queues.py and WidgetEditorView requests.
const BULK = '/model-hub/scores/bulk/';
const PATHS = [BULK, '/tracer/trace/list_traces_of_session/', '/model-hub/annotation-queues/for-source/',
  '/tracer/dashboard/metrics/', '/tracer/dashboard/filter_values/', '/tracer/dashboard/query/', '/tracer/dashboard/'];

export function captureAnnotationHttp(
  pages: Page[], apiUrl: string, attach: (name: string, body: unknown) => Promise<void>,
): { receipts: Receipt<unknown>[]; pending: Set<Promise<void>>; stop: () => void } {
  const origin = new URL(apiUrl).origin;
  const receipts: Receipt<unknown>[] = [], pending = new Set<Promise<void>>();
  const requests = new Map<Request, Receipt<unknown>>();
  const onRequest = (outgoing: Request) => {
    const url = new URL(outgoing.url()), path = url.pathname, method = outgoing.method();
    if (url.origin !== origin || !['GET', 'POST', 'PATCH', 'PUT'].includes(method) ||
      !(PATHS.includes(path) || /^\/tracer\/trace\/[0-9a-f-]+\/$/.test(path) ||
        /^\/tracer\/dashboard\/[0-9a-f-]+\/(?:widgets\/(?:[0-9a-f-]+\/)?)?$/.test(path))) return;
    const headers = outgoing.headers();
    const receipt: Receipt<unknown> = { path, method, input: {}, status: 0, startedAt: Date.now(), endedAt: 0, settled: false,
      scope: { organizationId: headers['x-organization-id'], workspaceId: headers['x-workspace-id'] },
      authorized: /^Bearer \S+$/.test(headers.authorization ?? '') };
    try { receipt.input = method === 'GET' ? Object.fromEntries(url.searchParams) : outgoing.postDataJSON(); }
    catch { receipt.error = 'request_json_unreadable'; }
    requests.set(outgoing, receipt); receipts.push(receipt);
  };
  const onResponse = (response: Response) => {
    const receipt = requests.get(response.request()); if (!receipt) return;
    Object.assign(receipt, { status: response.status(), endedAt: Date.now(),
      requestId: response.headers()['x-request-id'], contentType: response.headers()['content-type'] ?? '' });
    const capture = (async () => {
      try {
        await attach('native-http-head', { ...receipt });
        if (!/\bapplication\/(?:[\w.-]+\+)?json\b/i.test(receipt.contentType!)) { receipt.error = 'non_json_body_omitted'; return; }
        receipt.body = await readJsonWithin(response);
        // HTTP 200 can contain failed score saves; record those before settling.
        const body = receipt.body as { status?: boolean; result?: { errors?: unknown } } | null;
        if (body?.status === false) receipt.error ??= 'application_error';
        if (receipt.path === BULK && (!Array.isArray(body?.result?.errors) || body.result.errors.length !== 0)) {
          receipt.error ??= 'bulk_score_errors';
        }
      } catch { receipt.error = 'response_json_unreadable'; }
      finally { receipt.endedAt = Date.now(); receipt.settled = true; }
    })();
    pending.add(capture); void capture.then(() => pending.delete(capture));
  };
  const onFailed = (outgoing: Request) => {
    const receipt = requests.get(outgoing);
    if (receipt) Object.assign(receipt, { error: 'request_failed', settled: true, endedAt: Date.now() });
  };
  for (const page of pages) { page.on('request', onRequest); page.on('response', onResponse); page.on('requestfailed', onFailed); }
  return { receipts, pending, stop() {
    for (const page of pages) { page.off('request', onRequest); page.off('response', onResponse); page.off('requestfailed', onFailed); }
  } };
}
