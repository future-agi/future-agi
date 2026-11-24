import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { mintUnsubscribeLink } from '../../lib/email-link-seed';

// futureagi/tfc/utils/email_unsubscribe.py: AUTOMATION_RULE_RUNS and LINK_MAX_AGE (30 days).
const CATEGORY = 'automation_rule_runs';
// futureagi/accounts/views/annotation_notifications.py: _invalid_link and the RFC 8058 body.
const INVALID = 'This unsubscribe link is invalid or older than 30 days. Use the Unsubscribe link in a recent email instead.';
const ONE_CLICK = { 'List-Unsubscribe': 'One-Click' };
const UI_READY = 60_000; // README Writing a flow: browser action/first-paint budget.

test('AUTH-E2E-019: unsubscribe links act only when valid, recent and pressed, and mail clients can unsubscribe in one click', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'AUTH-E2E-019', area: 'auth',
    userGoal: 'A recipient whose unsubscribe link was altered or is over 30 days old is told so and nothing changes, while their mail client\'s one-click unsubscribe button works without opening a page.',
    steps: [
      'open an unsubscribe link whose signature was altered and see Link invalid',
      'open a link signed 31 days ago and see Link invalid',
      'send a POST to a valid link with no button pressed and no one-click marker',
      'send the mail client\'s RFC 8058 one-click POST to the valid link',
      'open the valid link and see that the category is already off',
    ],
    backendChecks: [
      'an altered link, a 31-day-old link and an unpressed POST each leave the actor\'s email opt-outs in PG accounts_user.config unchanged',
      'the one-click POST adds exactly the automation_rule_runs opt-out to the actor in PG accounts_user.config',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(300_000); // four UI_READY steps plus three seeds.
  const [user] = await probe.pg<{ id: string }>(
    'SELECT id FROM accounts_user WHERE email = $1', [actor.email]);
  const valid = mintUnsubscribeLink({ email: actor.email, category: CATEGORY, userId: user.id });
  const expired = mintUnsubscribeLink({ email: actor.email, category: CATEGORY, userId: user.id, ageDays: 31 });
  // Change one character of the payload, before the signature: the link still parses, the signature fails.
  const url = new URL(valid);
  const token = url.searchParams.get('token')!;
  url.searchParams.set('token', `${token[0] === 'A' ? 'B' : 'A'}${token.slice(1)}`);
  const altered = url.toString();
  await testInfo.attach('seeded', {
    body: JSON.stringify({ email: actor.email, userId: user.id, valid, expired, altered }, null, 2),
    contentType: 'application/json',
  });
  // actor is shared by every spec in the worker: compare with the opt-outs it
  // started with, which must not already include this flow's category.
  const optOuts = async () => ((await probe.pg<{ o: string[] | null }>(
    "SELECT config->'email_optouts' AS o FROM accounts_user WHERE id = $1", [user.id]))[0].o ?? []).sort();
  const before = await optOuts();
  expect(before).not.toContain(CATEGORY);
  const withCategory = [...before, CATEGORY].sort();

  for (const [label, link] of [['altered', altered], ['31 days old', expired]] as const) {
    await test.step(`open the ${label} link`, async () => {
      await page.goto(link);
      await expect(page.getByRole('heading')).toHaveText(['Link invalid'], { timeout: UI_READY });
      await expect(page.locator('main p')).toHaveText(INVALID);
      await expect(page.getByRole('button')).toHaveCount(0);
      // The page's own buttons are the only way to act; a one-click POST is refused too.
      const post = await page.request.post(link, { form: ONE_CLICK });
      expect(post.status()).toBe(400);
      expect(await optOuts()).toEqual(before);
    }, { timeout: UI_READY });
  }

  await test.step('a POST with no button pressed changes nothing', async () => {
    const post = await page.request.post(valid, { form: {} });
    expect(post.status()).toBe(400);
    expect(await optOuts()).toEqual(before);
  }, { timeout: UI_READY });

  await test.step('the mail client\'s one-click POST unsubscribes', async () => {
    const post = await page.request.post(valid, { form: ONE_CLICK });
    expect(post.status()).toBe(200);
    expect(await optOuts()).toEqual(withCategory);
    await page.goto(valid);
    await expect(page.getByRole('heading')).toHaveText(['You\'re unsubscribed'], { timeout: UI_READY });
    await expect(page.locator('main p')).toHaveText(`${actor.email} doesn't receive automation rule run emails.`);
    await expect(page.getByRole('button')).toHaveText(['Resubscribe']);
  }, { timeout: UI_READY });
});
