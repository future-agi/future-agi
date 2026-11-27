import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { mintUnsubscribeLink } from '../../lib/email-link-seed';

// futureagi/tfc/utils/email_unsubscribe.py: INTEGRATION_ERRORS, CATEGORY_LABELS and
// USER_CONFIG_KEY (the opt-out list in accounts_user.config).
const CATEGORY = 'integration_errors';
const LABEL = 'integration error emails';
// frontend/src/utils/axios.js endpoints.organizations.switch and .current;
// accounts/views/organization_selection.py:SwitchOrganizationView saves user.config.
const SWITCH_ORG = '/accounts/organizations/switch/';
const CURRENT_ORG = '/accounts/organizations/current/';
const UI_READY = 60_000; // README Writing a flow: browser action/first-paint budget.

test('AUTH-E2E-018: an emailed unsubscribe link turns one notification category off and back on', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'AUTH-E2E-018', area: 'auth',
    userGoal: 'A user who gets integration error emails opens the unsubscribe link from one, stops that category, keeps it stopped while using the app, and resubscribes from the same page.',
    steps: [
      'mint the signed integration-errors link an email carries for the actor',
      'open the link and see the Unsubscribe? confirmation naming the category and address',
      'press Unsubscribe and see Unsubscribed with a Resubscribe button',
      'switch organization in the already signed-in app session',
      'press Resubscribe and see Resubscribed',
    ],
    backendChecks: [
      'opening the link alone leaves the actor\'s email opt-outs in PG accounts_user.config unchanged',
      'Unsubscribe adds exactly the integration_errors opt-out to the actor in PG accounts_user.config',
      'an organization switch in a session signed in before the unsubscribe keeps the opt-out in PG and the session stays signed in',
      'Resubscribe removes exactly that opt-out again, back to the list the actor started with',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(300_000); // four UI_READY steps plus seeding.
  const [user] = await probe.pg<{ id: string }>(
    'SELECT id FROM accounts_user WHERE email = $1', [actor.email]);
  const link = mintUnsubscribeLink({ email: actor.email, category: CATEGORY, userId: user.id });
  await testInfo.attach('seeded', {
    body: JSON.stringify({ email: actor.email, userId: user.id, organizationId: actor.organizationId, link }, null, 2),
    contentType: 'application/json',
  });
  // actor is shared by every spec in the worker: compare with the opt-outs it
  // started with, which must not already include this flow's category.
  const optOuts = async () => ((await probe.pg<{ o: string[] | null }>(
    "SELECT config->'email_optouts' AS o FROM accounts_user WHERE id = $1", [user.id]))[0].o ?? []).sort();
  const before = await optOuts();
  expect(before).not.toContain(CATEGORY);
  const withCategory = [...before, CATEGORY].sort();

  await test.step('open the link: it only asks', async () => {
    await page.goto(link);
    await expect(page.getByRole('heading')).toHaveText(['Unsubscribe?'], { timeout: UI_READY });
    await expect(page.locator('main p')).toHaveText(`Stop sending ${LABEL} to ${actor.email}?`);
    await expect(page.getByRole('button')).toHaveText(['Unsubscribe']);
    expect(await optOuts()).toEqual(before);
  }, { timeout: UI_READY });

  await test.step('Unsubscribe', async () => {
    await page.getByRole('button', { name: 'Unsubscribe' }).click();
    await expect(page.getByRole('heading')).toHaveText(['Unsubscribed'], { timeout: UI_READY });
    await expect(page.locator('main p')).toHaveText(`${actor.email} will no longer receive ${LABEL}.`);
    await expect(page.getByRole('button')).toHaveText(['Resubscribe']);
    expect(await optOuts()).toEqual(withCategory);
  }, { timeout: UI_READY });

  await test.step('switch organization in the signed-in session', async () => {
    // actor.api's session was signed in, and its user cached, during provisioning.
    await actor.api.post(SWITCH_ORG, { organization_id: actor.organizationId });
    expect(await optOuts()).toEqual(withCategory);
    const current = await actor.api.get<{ result: { organization: { id: string } } }>(CURRENT_ORG);
    expect(current.result.organization.id).toBe(actor.organizationId);
  }, { timeout: UI_READY });

  await test.step('Resubscribe', async () => {
    await page.getByRole('button', { name: 'Resubscribe' }).click();
    await expect(page.getByRole('heading')).toHaveText(['Resubscribed'], { timeout: UI_READY });
    await expect(page.locator('main p')).toHaveText(`${actor.email} will receive ${LABEL} again.`);
    expect(await optOuts()).toEqual(before);
  }, { timeout: UI_READY });
});
