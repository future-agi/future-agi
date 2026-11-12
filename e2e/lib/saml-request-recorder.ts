import type { Page, Request } from '@playwright/test';

// Pinned by saml-tenant-isolation.spec.ts: both callback hops and SPA bootstrap.
const SAML_PATHS = new Set(['/saml2_auth/acs/', '/saml2_auth/complete/', '/accounts/user-info/']);

export interface RequestReceipt {
  flow: string;
  // Request emission, not header resolution: compare with the completion response.
  at: number;
  method: string;
  path: string;
  cookieNames: string[];
  cookieHeader: string | null;
  authorizationPrefix: string | null;
  authorizationValue: string | null;
  hasOrganizationHeader: boolean;
  hasWorkspaceHeader: boolean;
}

export function redactReceipts(receipts: readonly RequestReceipt[]): RequestReceipt[] {
  return receipts.map(({ cookieHeader, authorizationValue, ...receipt }) => ({
    ...receipt,
    cookieNames: [...receipt.cookieNames],
    cookieHeader: cookieHeader ? '[REDACTED]' : null,
    authorizationValue: authorizationValue ? '[REDACTED]' : null,
  }));
}

/** One page owns every header read until stop() settles, before page teardown. */
export class SamlRequestRecorder {
  private readonly receipts: RequestReceipt[] = [];
  private readonly pending = new Set<Promise<void>>();
  private readonly failures: Error[] = [];
  private stopped?: Promise<RequestReceipt[]>;
  private readonly page: Page;
  private readonly flow: string;

  constructor(page: Page, flow: string) {
    this.page = page;
    this.flow = flow;
    page.on('request', this.onRequest);
  }

  private readonly onRequest = (request: Request): void => {
    const pathname = new URL(request.url()).pathname;
    if (!SAML_PATHS.has(pathname)) return;
    const at = Date.now();
    const method = request.method();
    const recordFailure = (): void => {
      // Never retain the original error: Playwright errors may embed the full
      // callback URL, query parameters, cookies or bearer credentials.
      this.failures.push(new Error(`SAML recording failed: ${this.flow} ${method} ${pathname}`));
    };
    try {
      // headers() omits Cookie. Keep allHeaders() for the binder assertions.
      const work = request.allHeaders().then(headers => {
        const cookieNames = (headers.cookie ?? '')
          .split(';')
          .map(cookie => cookie.trim().split('=', 1)[0])
          .filter(Boolean)
          .sort();
        const authorization = headers.authorization;
        this.receipts.push({
          flow: this.flow,
          at,
          method,
          path: pathname,
          cookieNames,
          cookieHeader: headers.cookie ?? null,
          authorizationPrefix: authorization ? authorization.split(' ', 1)[0] : null,
          authorizationValue: authorization?.split(' ', 2)[1] ?? null,
          hasOrganizationHeader: 'x-organization-id' in headers,
          hasWorkspaceHeader: 'x-workspace-id' in headers,
        });
      }).catch(recordFailure).then(() => { this.pending.delete(work); });
      // The entire read/build chain already has a rejection handler. No rejecting
      // finally() derivative is left unobserved while the test does other work.
      this.pending.add(work);
    } catch {
      recordFailure();
    }
  };

  async drain(): Promise<RequestReceipt[]> {
    // New matching requests can arrive while a prior batch is resolving.
    while (this.pending.size) await Promise.all([...this.pending]);
    if (this.failures.length) {
      throw new AggregateError(this.failures, this.failures.map(error => error.message).join('; '));
    }
    return this.receipts.map(receipt => ({ ...receipt, cookieNames: [...receipt.cookieNames] }));
  }

  stop(): Promise<RequestReceipt[]> {
    if (!this.stopped) {
      // This must happen synchronously, even with a header read still pending.
      this.page.off('request', this.onRequest);
      this.stopped = this.drain();
    }
    return this.stopped;
  }

  /** Partial evidence remains available after a failed drain, but is always redacted. */
  redactedSnapshot(): RequestReceipt[] {
    return redactReceipts(this.receipts);
  }
}

interface RecordingEvidence {
  receipts: RequestReceipt[];
  errors: string[];
}

/** Scope the SAML spec's recorders, including both explicit X/Y contexts. */
export async function withSamlRecorders(
  options: {
    report: (evidence: RecordingEvidence) => Promise<void>;
    release: { name: string; run: () => Promise<unknown> | undefined }[];
  },
  run: (record: (page: Page, flow: string) => SamlRequestRecorder) => Promise<void>,
): Promise<void> {
  const recorders: SamlRequestRecorder[] = [];
  let failed = false;
  let primary: unknown;
  try {
    await run((page, flow) => {
      const recorder = new SamlRequestRecorder(page, flow);
      recorders.push(recorder);
      return recorder;
    });
  } catch (error) {
    failed = true;
    primary = error;
  }

  // map calls every stop synchronously. allSettled, not fail-fast Promise.all:
  // one failed X read must not let Y's context close over its pending reads.
  const stopped = await Promise.allSettled(recorders.map(recorder => recorder.stop()));
  const failures: Error[] = stopped.flatMap(result => result.status === 'rejected' ? [result.reason] : []);
  const receipts = recorders.flatMap(recorder => recorder.redactedSnapshot());
  const released = await Promise.allSettled(options.release.map(async resource => {
    try {
      await resource.run();
    } catch {
      // Resource errors can also carry credentials; report only the safe label.
      throw new Error(`SAML cleanup failed: ${resource.name}`);
    }
  }));
  for (const result of released) {
    if (result.status === 'rejected') failures.push(result.reason);
  }
  try {
    await options.report({ receipts, errors: failures.map(error => error.message) });
  } catch {
    failures.push(new Error('SAML cleanup failed: receipt attachment'));
  }
  if (failed && !failures.length) throw primary;
  if (failures.length) {
    throw new AggregateError(
      failed ? [primary, ...failures] : failures,
      `SAML recording/cleanup failed: ${failures.map(error => error.message).join('; ')}`,
      failed ? { cause: primary } : undefined,
    );
  }
}
