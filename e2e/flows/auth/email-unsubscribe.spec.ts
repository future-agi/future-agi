import { execFileSync } from 'node:child_process';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { resolveBackendContainer } from '../../lib/simulate-seed';

// futureagi/tfc/utils/email_unsubscribe.py: PAGE_PATH, INTEGRATION_ERRORS,
// CATEGORY_LABELS and USER_CONFIG_KEY (the opt-out list in accounts_user.config).
const PAGE = '/accounts/notifications/email/unsubscribe/';
const CATEGORY = 'integration_errors';
const LABEL = 'integration error emails';
// futureagi/accounts/views/annotation_notifications.py: EmailUnsubscribeView pages.
const UI_READY = 60_000; // README Writing a flow: browser action/first-paint budget.

// The e2e stack has no mail inbox, so the flow mints the link the email would
// carry with the platform's own signer, inside the stack's backend container.
// Delivery (the List-Unsubscribe header and the footer link) is covered by
// accounts/tests/test_email_unsubscribe*.py.
const MINT = `
import json, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django
django.setup()
from tfc.utils.email_unsubscribe import make_token
print(json.dumps({"token": make_token(os.environ["SEED_EMAIL"], os.environ["SEED_CATEGORY"], user_id=os.environ["SEED_USER_ID"])}))
`;

function mintLink(email: string, userId: string, category: string): string {
  const stdout = execFileSync('docker', ['exec',
    '-e', `SEED_EMAIL=${email}`, '-e', `SEED_USER_ID=${userId}`, '-e', `SEED_CATEGORY=${category}`,
    resolveBackendContainer(), 'sh', '-c', `cd /app/backend && PYTHONPATH=/app/backend python -c '${MINT}'`,
  ], { encoding: 'utf-8' });
  const { token } = JSON.parse(stdout.trim().split('\n').pop() ?? '') as { token: string };
  return `${E2E.apiUrl}${PAGE}?token=${encodeURIComponent(token)}`;
}

test('AUTH-E2E-018: an emailed unsubscribe link turns one notification category off and back on', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'AUTH-E2E-018', area: 'auth',
    userGoal: 'A user who gets integration error emails opens the unsubscribe link from one, stops that category, and resubscribes from the same page.',
    steps: [
      'mint the signed integration-errors link an email carries for the actor',
      'open the link and see the Unsubscribe? confirmation naming the category and address',
      'press Unsubscribe and see Unsubscribed with a Resubscribe button',
      'press Resubscribe and see Resubscribed',
    ],
    backendChecks: [
      'opening the link alone leaves the actor with no email opt-outs in PG accounts_user.config',
      'Unsubscribe records exactly the integration_errors opt-out on the actor in PG accounts_user.config',
      'Resubscribe leaves the actor with an empty opt-out list in PG accounts_user.config',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(240_000); // three UI_READY steps plus seeding.
  const [user] = await probe.pg<{ id: string }>(
    'SELECT id FROM accounts_user WHERE email = $1', [actor.email]);
  const link = mintLink(actor.email, user.id, CATEGORY);
  await testInfo.attach('seeded', {
    body: JSON.stringify({ email: actor.email, userId: user.id, link }, null, 2),
    contentType: 'application/json',
  });
  const optOuts = async () => (await probe.pg<{ o: string[] | null }>(
    "SELECT config->'email_optouts' AS o FROM accounts_user WHERE id = $1", [user.id]))[0].o;

  await test.step('open the link: it only asks', async () => {
    await page.goto(link);
    await expect(page.getByRole('heading')).toHaveText(['Unsubscribe?'], { timeout: UI_READY });
    await expect(page.locator('main p')).toHaveText(`Stop sending ${LABEL} to ${actor.email}?`);
    await expect(page.getByRole('button')).toHaveText(['Unsubscribe']);
    expect(await optOuts()).toBeNull();
  }, { timeout: UI_READY });

  await test.step('Unsubscribe', async () => {
    await page.getByRole('button', { name: 'Unsubscribe' }).click();
    await expect(page.getByRole('heading')).toHaveText(['Unsubscribed'], { timeout: UI_READY });
    await expect(page.locator('main p')).toHaveText(`${actor.email} will no longer receive ${LABEL}.`);
    await expect(page.getByRole('button')).toHaveText(['Resubscribe']);
    expect(await optOuts()).toEqual([CATEGORY]);
  }, { timeout: UI_READY });

  await test.step('Resubscribe', async () => {
    await page.getByRole('button', { name: 'Resubscribe' }).click();
    await expect(page.getByRole('heading')).toHaveText(['Resubscribed'], { timeout: UI_READY });
    await expect(page.locator('main p')).toHaveText(`${actor.email} will receive ${LABEL} again.`);
    expect(await optOuts()).toEqual([]);
  }, { timeout: UI_READY });
});
