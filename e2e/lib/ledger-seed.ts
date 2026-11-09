// H4: managed-credit fixtures for the money walks (W01-W04, W06, W19). Every
// entry this writes is labelled FIXTURE_NOTE: it is a walk fixture, not a
// purchase, and a walk's receipt says so. No stack can walk a real purchase:
// checkout imports the private ee.cloud billing adapter, which is never read,
// shipped or created here.
//   grant / adjust      POST /agentcc/managed-credits/grant|adjust/ with the stack's
//                       dev admin token (agentcc/views/managed_credits.py _StaffView:
//                       the token names the org; idempotency key per fixture label)
//   grantViaCommand     `manage.py agentcc_managed_credits <org> --grant <usd> --note`
//                       inside the API container (bin/e2e compose exec)
//   topupFixture        one `topup` entry with stripe_ref = a synthetic Checkout
//                       session id, through the ledger's own public seam
//                       (ee/usage/services/managed_credits.py fulfil_topup with a
//                       test-mode TopupPayment), run by `manage.py shell` from a
//                       script on stdin: W02's "credited exactly once" branch.
// Local stacks only: the admin token is the compose files' published dev token.
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { request, type APIRequestContext } from '@playwright/test';
import { E2E } from './env';
import { djangoFixtureCommand } from './django-fixture';
import { GATEWAY_ADMIN_TOKEN } from './gateway-admin';

const root = fileURLToPath(new URL('../../', import.meta.url));
export const FIXTURE_NOTE = 'walk fixture, not a purchase';
const LABEL = /^[A-Za-z0-9_.-]{1,60}$/;
const ORG = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const USD = /^-?\d{1,6}(\.\d{1,6})?$/;
const check = (value: string, re: RegExp, what: string) => {
  if (!re.test(value)) throw new Error(`ledger fixture: ${what} ${JSON.stringify(value)} is not valid`);
  return value;
};

export interface FixtureEntry { status: number; body: unknown; label: string; note: string }

/** The API container: Standalone's `app`, Distributed's `backend`. */
const apiService = () => (process.env.E2E_STACK === 'standalone' ? 'app' : 'backend');

const composeExec = (args: string[], input?: string): string => {
  const env = { ...process.env };
  delete env.DOCKER_CONTEXT;
  return execFileSync(`${root}bin/e2e`, ['compose', 'exec', '-T', apiService(), 'env', 'NO_STARTUP_DB_MUTATIONS=false', ...args], { env, encoding: 'utf8',
    timeout: 120_000, input, maxBuffer: 4 * 1024 * 1024, stdio: [input === undefined ? 'ignore' : 'pipe', 'pipe', 'pipe'] });
};

export class LedgerSeeder {
  private constructor(private readonly ctx: APIRequestContext) {}

  /** The admin token is sent to the local API only. */
  static async open(token = GATEWAY_ADMIN_TOKEN, baseURL = E2E.apiUrl): Promise<LedgerSeeder> {
    if (!['localhost', '127.0.0.1'].includes(new URL(baseURL).hostname)) throw new Error('LedgerSeeder talks to a local API only');
    return new LedgerSeeder(await request.newContext({ baseURL, extraHTTPHeaders: { Authorization: `Bearer ${token}` } }));
  }

  private async write(path: string, org: string, usd: string, label: string, text: 'note' | 'reason'): Promise<FixtureEntry> {
    const note = `${FIXTURE_NOTE} (${check(label, LABEL, 'label')})`;
    const res = await this.ctx.post(path, { data: { org_id: check(org, ORG, 'org'), amount_usd: check(usd, USD, 'amount'),
      [text]: note, idempotency_key: `walk-fixture-${label}` } });
    const raw = await res.text();
    let body: unknown;
    try { body = JSON.parse(raw); } catch { body = { raw: raw.slice(0, 300) }; }
    return { status: res.status(), body, label, note };
  }

  /** A fixture grant: 201 when written, 200 when the same label repeats it. */
  grant(org: string, usd: string, label: string): Promise<FixtureEntry> {
    return this.write('/agentcc/managed-credits/grant/', org, usd, label, 'note');
  }

  /** A fixture adjustment (positive adds, negative removes). */
  adjust(org: string, usd: string, label: string): Promise<FixtureEntry> {
    return this.write('/agentcc/managed-credits/adjust/', org, usd, label, 'reason');
  }

  dispose(): Promise<void> { return this.ctx.dispose(); }
}

/** The operator command's grant, as W01's seed names it. Returns its output. */
export function grantViaCommand(org: string, usd: string, label: string): string {
  return composeExec(['python', 'manage.py', 'agentcc_managed_credits', check(org, ORG, 'org'),
    '--grant', check(usd, USD, 'amount'), '--note', `${FIXTURE_NOTE} (${check(label, LABEL, 'label')})`]);
}

/** A Checkout-session id of the fixture's own (Stripe's cs_test_ shape; never a real one). */
export const syntheticSession = (label: string) => `cs_test_e2e_walk_${check(label, LABEL, 'label').replace(/[.-]/g, '_')}`;

// Runs inside `manage.py shell`; the values arrive as JSON on its first line.
const TOPUP_SCRIPT = `
import json, sys
from accounts.models import Organization
from ee.usage.services import managed_credits as mc
args = json.loads(sys.stdin.readline())
org = Organization.objects.get(pk=args["org"])
payment = mc.TopupPayment(source=mc.TopupSource.CHECKOUT_SESSION, object_id=args["session"],
    amount_micros=int(args["micros"]), mode=mc.StripeMode.TEST, payment_intent_id=args["pi"], intent_ref=args["note"])
result = mc.fulfil_topup(org, payment=payment)
# Complete the ordinary coalesced push synchronously before exiting this fixture interpreter.
mc.push_granted(org)
print("E2E-TOPUP " + json.dumps({"created": bool(result.created), "entry": str(result.entry.pk),
    "stripe_ref": result.entry.stripe_ref, "note": result.entry.note}))
`;

/** One test-mode top-up for `session` through fulfil_topup; repeated, it credits nothing more. */
export function topupFixture(org: string, session: string, usd: string): { created: boolean; entry: string; stripe_ref: string; note: string } {
  if (!/^cs_test_e2e_walk_[A-Za-z0-9_]{1,60}$/.test(session)) throw new Error('topup fixture needs a syntheticSession() id');
  const micros = Math.round(Number(check(usd, USD, 'amount')) * 1_000_000);
  if (!(micros > 0)) throw new Error('topup fixture amount must be positive');
  const input = `${JSON.stringify({ org: check(org, ORG, 'org'), session, micros, note: FIXTURE_NOTE, pi: `pi_${session.slice(3)}` })}\n`;
  // `manage.py shell -c` reads the script; the JSON line is the script's own stdin.
  const out = composeExec(['python', 'manage.py', 'shell', '-c', djangoFixtureCommand(TOPUP_SCRIPT)], input);
  const line = out.split('\n').find((l) => l.startsWith('E2E-TOPUP '));
  if (!line) throw new Error('topup fixture: the ledger printed no result');
  return JSON.parse(line.slice('E2E-TOPUP '.length));
}
