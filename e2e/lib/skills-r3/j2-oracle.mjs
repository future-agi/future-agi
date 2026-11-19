import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';

/** Independent J2 observation readers. They read the runtime's separate business database and the
 * model-fixture ledger directly; neither reads a Hub-graded value. */
export const sha256 = (value) => createHash('sha256').update(value).digest('hex');

/** The runtime's per-cell synthetic order identity (a documented contract of the container runtime). */
export const orderId = (attemptId, caseId, variant, repeat) =>
  `synthetic-${sha256(`${attemptId}/${caseId}/${variant}/${repeat}`).slice(0, 20)}`;

export function businessLedger(container) {
  const script = "import sqlite3,json;d=sqlite3.connect('file:/fi_business/business.sqlite?mode=ro',uri=True);print(json.dumps([json.loads(r[0]) for r in d.execute('select receipt from operations order by id')]))";
  return JSON.parse(execFileSync('docker', ['exec', container, 'python', '-c', script], { encoding: 'utf8' }));
}

/** Business operations for exactly this run's cells. */
export function runOperations(container, attemptId, cells) {
  const wanted = new Map(cells.map((cell) => [orderId(attemptId, cell.case_id, cell.variant, cell.repeat_index), cell]));
  return businessLedger(container).filter((row) => wanted.has(row.order_id))
    .map((row) => ({ ...row, cell: wanted.get(row.order_id) }));
}

/** Model-boundary view: first request of every cell, the delivered root bytes and catalog size. */
export function firstTurns(ledger) {
  return ledger.filter((row) => row.kind === 'model' && row.turn === 0).map((row) => ({
    system: row.messages[0].content,
    request: row.request,
    catalogMembers: (row.messages[0].content.match(/"id":/g) ?? []).length,
    toolCalls: row.tools.length,
  }));
}
