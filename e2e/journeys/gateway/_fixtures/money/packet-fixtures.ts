import { validateAttempt, type AttemptPlan, type Dialect } from './wire';

export interface MoneyProgram {
  id: string; relatedRows: string[]; controls: string[]; attempts: AttemptPlan[];
  productRowsCovered: never[];
}
function attempt(dialect: Dialect, model: string, stream: boolean, changes: Partial<AttemptPlan> = {}): AttemptPlan {
  const value: AttemptPlan = { target: 'primary', dialect, model, stream, inputTokens: 7, outputTokens: 7,
    usage: 'complete', outcome: 'finish', ...changes };
  validateAttempt(value); return value;
}
function program(id: string, rows: string[], controls: string[], attempts: AttemptPlan[]): MoneyProgram {
  return { id, relatedRows: rows, controls, attempts, productRowsCovered: [] };
}
/** J11 PACKET W01-04/05/06 fixtures: explicit caller-admitted native model IDs,
 * no invented dated launch model or managed router. The 7/7 counters come from
 * PACKET W01-05 and the existing e2e mock, NOT a catalogue price or ledger grant.
 * The consumer must join each native program to the same F09 personal key.
 * 10-route client_ip inventory, price version/half-micro case and monetary rows
 * remain OWED; these six calls must never be labelled complete W01-06 coverage.
 */
export function j11NativePrograms(models: { openai: string; anthropic: string; gemini: string }): MoneyProgram[] {
  return [
    program('openai-json', ['W01-04'], ['C-USAGE'], [attempt('openai', models.openai, false)]),
    program('openai-stream', ['W01-04', 'W01-06'], ['C-USAGE'], [attempt('openai', models.openai, true)]),
    program('messages-json', ['W01-04'], ['C-USAGE'], [attempt('anthropic', models.anthropic, false)]),
    program('messages-stream', ['W01-04', 'W01-06'], ['C-USAGE'], [attempt('anthropic', models.anthropic, true)]),
    program('gemini-native-json', ['W01-04', 'W01-06'], ['C-USAGE'], [attempt('gemini', models.gemini, false)]),
    program('caller-fallback', ['W01-04', 'W01-06'], ['C-FALLBACK'], [
      attempt('openai', models.openai, false, { outcome: 'http-503' }),
      attempt('openai', models.openai, false, { target: 'fallback' }),
    ]),
  ];
}
/** J12 PACKET S01-S06 stimulus side only. Never fabricate F=1000/H=100/U=40
 * unit conversion or a money outcome from HTTP status. Real F02/F05 state and
 * worker barriers are still mandatory, particularly after transport closure.
 * Absent-usage is NOT claimed to be the original v5 scenario; retry/fallback
 * is NOT claimed as the mapped 95b6881f9c mutant. Main supplies those bindings.
 * No Redis stop/start or outbox replay: those belong to accepted runtime hooks.
 */
export function j12FaultPrograms(model: string): MoneyProgram[] {
  const stream = (changes: Partial<AttemptPlan>) => attempt('openai', model, true, changes);
  return [
    program('midstream-disconnect', ['A02', 'A10'], ['C02'], [stream({ usage: 'absent', outcome: 'disconnect-after-output' })]),
    program('delayed-usage', ['A03', 'A10'], ['C02', 'C04'], [stream({ usage: 'delayed' })]),
    program('duplicate-usage-frame', ['A03', 'A10'], ['C04'], [stream({ usage: 'duplicate' })]),
    program('partial-usage', ['A04', 'A10'], ['C02'], [stream({ usage: 'partial' })]),
    program('absent-usage', ['A05', 'A10'], ['C02'], [stream({ usage: 'absent' })]),
    program('retry-fallback', ['A06', 'A10'], ['C03', 'C04'], [
      attempt('openai', model, false, { outcome: 'http-503' }),
      attempt('openai', model, false, { outcome: 'http-503' }),
      attempt('openai', model, false, { target: 'fallback' }),
    ]),
    program('cancel-before-usage', ['A07', 'A10'], ['C02', 'C04'], [stream({ usage: 'absent', outcome: 'cancel-before-usage' })]),
    program('cancel-after-usage', ['A07', 'A10'], ['C02', 'C04'], [stream({ outcome: 'cancel-after-usage' })]),
  ];
}
