import type { PaymentScope } from './contract.ts';
import { reference, requireScope } from './contract.ts';
import { cents, fail, localPath, record, usd } from './validation.ts';

function plan<Body extends string | null>(s: PaymentScope, method: string, path: string, bodyJson: Body) {
  return Object.freeze({ dispatchable: false, authority: 'isolated-person-session-required',
    origin: s.apiOrigin, method, path, bodyJson });
}
/** Decimal JSON lexemes are built with BigInt, never binary floating-point money. */
export function checkoutPlan(s: PaymentScope, input: unknown) {
  requireScope(s); const r = record(input, ['amountMicros', 'successPath', 'cancelPath']);
  const n = cents(r.amountMicros);
  const decimal = `${n / 1000000n}.${String((n % 1000000n) / 10000n).padStart(2, '0')}`;
  const success = JSON.stringify(s.appOrigin + localPath(r.successPath));
  const cancel = JSON.stringify(s.appOrigin + localPath(r.cancelPath));
  return plan(s, 'POST', '/agentcc/managed-credits/checkout/',
    `{"amount_usd":${decimal},"success_url":${success},"cancel_url":${cancel}}`);
}
/** Readback may fulfil a checkout: GET here is NOT a read-only financial operation. */
export function checkoutReturnPlan(s: PaymentScope, sessionId: unknown) {
  requireScope(s);
  return plan(s, 'GET', `/agentcc/managed-credits/checkout/${reference(s, 'session', sessionId)}/`, null);
}
export function cardPlan(s: PaymentScope, input: unknown) {
  requireScope(s);
  // Inspect a data descriptor before using the discriminant, never invoke a getter.
  const op = input && typeof input === 'object' ? Object.getOwnPropertyDescriptor(input, 'operation') : undefined;
  if (!op || !Object.hasOwn(op, 'value')) return fail();
  const root = '/usage/v2/payment-methods/';
  if (op.value === 'collect' || op.value === 'list') {
    record(input, ['operation']);
    return plan(s, op.value === 'collect' ? 'POST' : 'GET', root, op.value === 'collect' ? '{}' : null);
  }
  if (op.value === 'confirm') {
    const r = record(input, ['operation', 'sessionId']);
    return plan(s, 'PUT', root, JSON.stringify({ session_id: reference(s, 'session', r.sessionId) }));
  }
  if (op.value === 'default' || op.value === 'remove') {
    const r = record(input, ['operation', 'cardId']); const id = reference(s, 'card', r.cardId);
    return plan(s, op.value === 'default' ? 'POST' : 'DELETE',
      `${root}${id}/${op.value === 'default' ? 'default/' : ''}`, op.value === 'default' ? '{}' : null);
  }
  return fail();
}
export function settingsPlan(s: PaymentScope, input: unknown) {
  requireScope(s); const r = record(input, ['lowBalanceMicros', 'autoReload']);
  const d = r.autoReload && typeof r.autoReload === 'object'
    ? Object.getOwnPropertyDescriptor(r.autoReload, 'enabled') : undefined;
  if (!d || !Object.hasOwn(d, 'value') || typeof d.value !== 'boolean') return fail();
  const low = r.lowBalanceMicros === null ? 'null' : usd(r.lowBalanceMicros);
  let auto: string;
  if (!d.value) { record(r.autoReload, ['enabled']); auto = '{"enabled":false}'; }
  else {
    const a = record(r.autoReload, ['enabled', 'consentVersion', 'thresholdMicros', 'amountMicros']);
    if (a.consentVersion !== 'auto-reload-v1') return fail('CONSENT');
    auto = `{"enabled":true,"consent_version":"auto-reload-v1","threshold_usd":${usd(a.thresholdMicros)},"amount_usd":${usd(a.amountMicros)}}`;
  }
  return plan(s, 'PUT', '/agentcc/managed-credits/settings/', `{"low_balance_threshold_usd":${low},"auto_reload":${auto}}`);
}
