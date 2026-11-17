// HR-JRN-01 child (child.config.ts; spawned by harness/value-free-live.spec.ts, with
// E2E_VALUE_FREE unset). The explicit option alone must be refused before anything is
// provisioned: one switch, one salt. lib/scope-actors (not the walk fixture), so the
// refusal is the first thing that runs and no browser starts.
import { test } from '../../lib/scope-actors';

test.use({ scopeValueFree: true, trace: 'off', screenshot: 'off', video: 'off' });

test('value-free child: the explicit option without the run switch is refused', async ({ scopeActors }) => {
  throw new Error(`unreachable: the fixture provisioned ${scopeActors.prefix ? 'actors' : 'nothing'}`);
});
