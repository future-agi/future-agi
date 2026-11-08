import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { requireValue, sha256 } from './canonical.mjs';

const execute = promisify(execFile);

/** Primitive instrumentation used by an approved Playwright/CLI driver. Never logs field
 * values, command arguments, command output, credentials, or browser URLs. */
export class MeasuredOperator {
  constructor({ ledger, actor, step, phase, signal }) {
    this.ledger = ledger; this.actor = actor; this.step = step; this.phase = phase; this.signal = signal;
  }
  #measure(dimension, primitive, target, effect, extra = {}) {
    if (this.signal?.aborted) throw new Error('Operator cancelled');
    return this.ledger.measure({ dimension, primitive, actor: this.actor, step: this.step,
      phase: this.phase, target, ...extra }, effect);
  }
  click(locator, target) { return this.#measure('A', 'click', target, () => locator.click()); }
  fill(locator, value, target) { return this.#measure('A', 'field_edit', target, () => locator.fill(value)); }
  review(target, items, exceptionItems, effect) {
    return this.#measure('A', 'review', target, effect, { items, exceptionItems });
  }
  approve(target, items, effect) { return this.#measure('A', 'approve', target, effect, { items }); }
  recoveryDecision(target, effect) { return this.#measure('A', 'recovery_decision', target, effect); }
  configurationEdit(target, effect) { return this.#measure('A', 'configuration_edit', target, effect); }
  transition(target, effect) { return this.#measure('S', 'transition', target, effect); }
  handoff(recipient, effect) { return this.#measure('H', 'handoff', recipient, effect); }
  reconcile(target, effect) { return this.#measure('R', 'reconcile', target, effect); }

  command(target, executable, args, { cwd, env, timeoutMs = 60_000 } = {}) {
    requireValue(process.platform === 'linux', 'CLI journeys execute only on approved cloud Linux');
    requireValue(typeof executable === 'string' && Array.isArray(args) && args.every((arg) => typeof arg === 'string'), 'Explicit executable and argument vector required');
    requireValue(Number.isInteger(timeoutMs) && timeoutMs > 0 && timeoutMs <= 300_000, 'Bounded command timeout required');
    return this.#measure('A', 'command', target, async () => {
      // No shell or fixture-supplied commands. A qualified driver supplies its packaged CLI.
      const result = await execute(executable, args, { cwd, env, timeout: timeoutMs,
        signal: this.signal, maxBuffer: 2 * 1024 * 1024, encoding: 'utf8', shell: false });
      this.ledger.observation({ step: this.step, id: `${target}/output`, artifactHash: sha256(result.stdout) });
      return result;
    });
  }
}
