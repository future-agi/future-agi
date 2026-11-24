import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { EMAIL_LINK_PATHS, mintDigestToken } from '../../lib/email-link-seed';

// model_hub/utils/annotation_digest.py: the realtime digest's snooze_url (days=7).
const SNOOZE_DAYS = 7;
// futureagi/tfc/utils/email_unsubscribe.py: CATEGORY_LABELS[ANNOTATION_DIGEST].
const LABEL = 'annotation digest emails (daily and realtime)';
// model_hub/models/annotation_queues.py:AnnotationNotificationState (default table name).
const STATE = 'model_hub_annotationnotificationstate';
const UI_READY = 60_000; // README Writing a flow: browser action/first-paint budget.

test('ANNOT-E2E-008: the annotation digest\'s snooze and older unsubscribe links ask before they act', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'ANNOT-E2E-008', area: 'annotations',
    userGoal: 'An annotator pauses realtime annotation emails for a week from the digest\'s snooze link, and later stops the digest from the unsubscribe link in an older digest email.',
    steps: [
      'mint the signed digest token the realtime digest\'s links carry for the actor',
      'open the snooze link and see Pause realtime emails? for 7 days',
      'press Pause and see Snoozed',
      'open the older digest unsubscribe link and see the Unsubscribe? confirmation',
      'press Unsubscribe and see Unsubscribed',
      'open a snooze link signed 31 days ago and see Link expired',
    ],
    backendChecks: [
      'opening the snooze link alone leaves the actor without a realtime snooze in PG model_hub_annotationnotificationstate',
      'Pause sets realtime_snoozed_until to about 7 days from now and keeps the digest enabled',
      'opening the older unsubscribe link alone keeps the digest enabled',
      'Unsubscribe turns digest_enabled off for the actor',
      'a 31-day-old snooze link leaves the state unchanged',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(420_000); // six UI_READY steps plus seeding.
  const [user] = await probe.pg<{ id: string }>(
    'SELECT id FROM accounts_user WHERE email = $1', [actor.email]);
  const token = encodeURIComponent(mintDigestToken({ userId: user.id }));
  const old = encodeURIComponent(mintDigestToken({ userId: user.id, ageDays: 31 }));
  const snooze = `${E2E.apiUrl}${EMAIL_LINK_PATHS.digestSnooze}?token=${token}&days=${SNOOZE_DAYS}`;
  const expiredSnooze = `${E2E.apiUrl}${EMAIL_LINK_PATHS.digestSnooze}?token=${old}&days=${SNOOZE_DAYS}`;
  const unsubscribe = `${E2E.apiUrl}${EMAIL_LINK_PATHS.digestUnsubscribe}?token=${token}`;
  await testInfo.attach('seeded', {
    body: JSON.stringify({ email: actor.email, userId: user.id, snooze, unsubscribe, expiredSnooze }, null, 2),
    contentType: 'application/json',
  });
  // The row may not exist until the first change; a missing row reads as the defaults.
  type State = { enabled: boolean; snoozedAWeek: boolean; snoozed: boolean };
  const state = async (): Promise<State> => {
    const rows = await probe.pg<State>(
      `SELECT digest_enabled AS enabled,
              realtime_snoozed_until IS NOT NULL AS snoozed,
              COALESCE(realtime_snoozed_until BETWEEN now() + interval '${SNOOZE_DAYS} days' - interval '10 minutes'
                                              AND now() + interval '${SNOOZE_DAYS} days', false) AS "snoozedAWeek"
         FROM ${STATE} WHERE user_id = $1`, [user.id]);
    return rows[0] ?? { enabled: true, snoozed: false, snoozedAWeek: false };
  };
  const snoozedUntil = async () => (await probe.pg<{ t: string | null }>(
    `SELECT realtime_snoozed_until::text AS t FROM ${STATE} WHERE user_id = $1`, [user.id]))[0]?.t ?? null;

  await test.step('open the snooze link: it only asks', async () => {
    await page.goto(snooze);
    await expect(page.getByRole('heading')).toHaveText(['Pause realtime emails?'], { timeout: UI_READY });
    await expect(page.locator('main p')).toHaveText(
      `Pause realtime annotation emails to ${actor.email} for ${SNOOZE_DAYS} days? The daily digest keeps coming.`);
    await expect(page.getByRole('button')).toHaveText(['Pause']);
    expect(await state()).toEqual({ enabled: true, snoozed: false, snoozedAWeek: false });
  }, { timeout: UI_READY });

  await test.step('Pause', async () => {
    await page.getByRole('button', { name: 'Pause' }).click();
    await expect(page.getByRole('heading')).toHaveText(['Snoozed'], { timeout: UI_READY });
    await expect(page.locator('main p')).toHaveText(
      `Realtime emails paused for ${SNOOZE_DAYS} days. Daily digest will continue.`);
    expect(await state()).toEqual({ enabled: true, snoozed: true, snoozedAWeek: true });
  }, { timeout: UI_READY });

  await test.step('open the older digest unsubscribe link: it only asks', async () => {
    await page.goto(unsubscribe);
    await expect(page.getByRole('heading')).toHaveText(['Unsubscribe?'], { timeout: UI_READY });
    await expect(page.locator('main p')).toHaveText(`Stop sending ${LABEL} to ${actor.email}?`);
    await expect(page.getByRole('button')).toHaveText(['Unsubscribe']);
    expect((await state()).enabled).toBe(true);
  }, { timeout: UI_READY });

  await test.step('Unsubscribe', async () => {
    await page.getByRole('button', { name: 'Unsubscribe' }).click();
    await expect(page.getByRole('heading')).toHaveText(['Unsubscribed'], { timeout: UI_READY });
    await expect(page.getByRole('button')).toHaveText(['Resubscribe']);
    expect(await state()).toEqual({ enabled: false, snoozed: true, snoozedAWeek: true });
  }, { timeout: UI_READY });

  await test.step('a 31-day-old snooze link has expired', async () => {
    const before = await snoozedUntil();
    await page.goto(expiredSnooze);
    await expect(page.getByRole('heading')).toHaveText(['Link expired'], { timeout: UI_READY });
    await expect(page.getByRole('button')).toHaveCount(0);
    const post = await page.request.post(expiredSnooze, { form: { action: 'snooze' } });
    expect(await post.text()).toContain('<h1>Link expired</h1>');
    expect(before).not.toBeNull();
    expect(await snoozedUntil()).toBe(before);
    expect((await state()).enabled).toBe(false);
  }, { timeout: UI_READY });
});
