// Source observations, NOT accepted runtime bindings. Read-only 708J-early
// at da4c81cc623f909ce38f53e01b557ee8374d5adb; provenance is in README.md.
export const F09_CANDIDATE = Object.freeze({
  head: 'da4c81cc623f909ce38f53e01b557ee8374d5adb',
  runtimeReady: false,
  keyIssuePath: '/accounts/key/generate_secret_key/',
  keyIssueMethod: 'POST',
  observedBodyFields: Object.freeze(['key_name']),
  forbiddenBootstrapPath: '/accounts/keys/',
  blockers: Object.freeze([
    'Signup still automatically creates system keys.',
    'The key-create serializer has no personal scoped-key contract.',
    'Enable/disable lookup is organization-bound, not same-person-bound.',
    'FA-733-COMPOSE is DEFERRED in the early-candidate manifest.',
  ]),
});

/** Mandatory precondition for any future runtime adapter, before signup. There
 * is deliberately NO override, callback, environment flag or future-head bypass.
 * Rebinding needs the actual composed source and review, not a passing mock. */
export function requireF09Runtime(candidateHead: unknown): never {
  if (candidateHead !== F09_CANDIDATE.head) throw new Error('F09-CANDIDATE-UNBOUND');
  throw new Error('F09-CANDIDATE-733-ABSENT');
}
