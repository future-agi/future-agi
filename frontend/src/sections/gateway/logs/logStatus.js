// How a request's outcome reads in the Logs table and drawer: a tone on the
// Future AGI status chip and the HTTP status as its label. A 403 is a
// guardrail block only when the log says a guardrail triggered; any other 4xx
// (a rate limit, a refused budget, a bad request) is a warning, 5xx an error.
// A block is "withheld" when a guardrail on the answer refused an answer the
// model gave (the gateway marks it: metadata answer_withheld "true"); its
// cost is recorded as for an answered request. Any other block, and a row
// logged before the gateway set the mark, reads as a block. The cost is never
// read for this.

import { EMPTY } from "../utils/formatters";

export function statusOf(log) {
  const code = Number(log?.status_code);
  if (!log || log.status_code == null || !Number.isFinite(code)) {
    return { tone: "neutral", label: EMPTY, blocked: false, withheld: false };
  }
  const label = String(code);
  const blocked =
    Boolean(log.guardrail_triggered) && (code === 403 || code === 446);
  const withheld = blocked && log.metadata?.answer_withheld === "true";
  if (blocked) return { tone: "error", label, blocked: true, withheld };
  if (code >= 200 && code < 400)
    return { tone: "healthy", label, blocked, withheld };
  if (code >= 400 && code < 500)
    return { tone: "warning", label, blocked, withheld };
  if (code >= 500) return { tone: "error", label, blocked, withheld };
  return { tone: "neutral", label, blocked, withheld };
}

/** A latency in ms as "8 ms" / "1.24 s". */
export function formatMs(ms) {
  if (ms == null || ms === "") return EMPTY;
  const n = Number(ms);
  if (!Number.isFinite(n)) return EMPTY;
  if (n >= 1000) return `${(n / 1000).toFixed(2)} s`;
  return `${Math.round(n)} ms`;
}
