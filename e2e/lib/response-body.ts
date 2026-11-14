import type { Response } from '@playwright/test';

/**
 * A body whose response has already arrived is read in milliseconds; this is a
 * ceiling for the read that never completes, and it must sit well inside the
 * UI_READY (60 s) step that awaits the harness's pending reads, so a lost body is
 * recorded as an error instead of becoming that step's timeout.
 */
export const BODY_READ_MS = 10_000;

/**
 * Read a response's JSON body within `ms` (default BODY_READ_MS), or reject with `body-timeout`.
 *
 * A flow's capture harness reads the body of every catalog/query response the
 * page receives and awaits those reads before it judges the receipts. A body
 * that never arrives — a response cut by the navigation that reopened the page
 * (CI run 34912956841: one `Response.body` started at +31.6 s and was still
 * open at the 660 s test ceiling) — must record an error the flow can attach,
 * not hold the flow until its ceiling with no attribution.
 */
// Defaults to `any`, exactly like Response.json(), so it is a drop-in replacement.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function readJsonWithin<T = any>(response: Response, ms: number = BODY_READ_MS): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(
      `body-timeout after ${ms} ms: ${response.request().method()} ${new URL(response.url()).pathname}`)), ms);
  });
  try {
    return await Promise.race([response.json() as Promise<T>, deadline]);
  } finally {
    clearTimeout(timer);
  }
}
