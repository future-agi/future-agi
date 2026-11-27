import { execFileSync } from 'node:child_process';
import { E2E } from './env';
import { resolveBackendContainer } from './simulate-seed';

/**
 * Mints the signed links a notification email carries, with the platform's own
 * signers, inside the selected stack's backend container.
 *
 * The e2e stack has no mail inbox, so a flow cannot read a delivered email. The
 * backend tests cover delivery (the List-Unsubscribe header and the footer link);
 * these links let a flow drive the pages those emails open. `ageDays` signs the
 * token as if it were minted that many days ago, so a flow can open an expired
 * link without waiting for one.
 */
const MINT = `
import json, os, time
from unittest import mock
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django
django.setup()
args = json.loads(os.environ["SEED_ARGS"])
signed_at = time.time() - args.get("ageDays", 0) * 86400
with mock.patch("django.core.signing.time.time", return_value=signed_at):
    if args["kind"] == "email":
        from tfc.utils.email_unsubscribe import make_token
        token = make_token(args["email"], args["category"], scope=args.get("scope"), user_id=args.get("userId"))
    else:
        from model_hub.utils.annotation_digest import _unsubscribe_token
        token = _unsubscribe_token(args["userId"])
print(json.dumps({"token": token}))
`;

// futureagi/accounts/urls.py: the pages the emails link to.
export const EMAIL_LINK_PATHS = {
  unsubscribe: '/accounts/notifications/email/unsubscribe/',
  digestUnsubscribe: '/accounts/notifications/unsubscribe/',
  digestSnooze: '/accounts/notifications/snooze/',
} as const;

function mint(args: Record<string, unknown>): string {
  const stdout = execFileSync('docker', ['exec', '-e', `SEED_ARGS=${JSON.stringify(args)}`,
    resolveBackendContainer(), 'sh', '-c', `cd /app/backend && PYTHONPATH=/app/backend python -c '${MINT}'`,
  ], { encoding: 'utf-8' });
  return (JSON.parse(stdout.trim().split('\n').pop() ?? '') as { token: string }).token;
}

/** The emailed unsubscribe link for one notification category (tfc.utils.email_unsubscribe). */
export function mintUnsubscribeLink(opts: {
  email: string; category: string; userId?: string; scope?: string; ageDays?: number;
}): string {
  const token = mint({ kind: 'email', ...opts });
  return `${E2E.apiUrl}${EMAIL_LINK_PATHS.unsubscribe}?token=${encodeURIComponent(token)}`;
}

/** The annotation digest's signed user token, used by its snooze and older unsubscribe links. */
export function mintDigestToken(opts: { userId: string; ageDays?: number }): string {
  return mint({ kind: 'digest', ...opts });
}
