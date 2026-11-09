/* eslint-env node */
// Moderation on the real backend, after community.e2e.cjs (SPEC §3.3, §5.5,
// §6.4-§6.6): C reports an entry through the private dialog and asks to be
// verified; B sees the report, unranks the entry, picks one for the week,
// blocks a digest, removes an entry (the page becomes a tombstone) and reads
// the audit log.
const { expect, test } = require("playwright/test");

const { APP, settle, shootAll, signIn } = require("./helpers.cjs");

const REF = "c-test/csv-answers";

test.describe.serial("Omega Hub moderation, real backend", () => {
  test("C reports an entry privately and asks to be a verified publisher", async ({ page, context }) => {
    await signIn(context, "c");
    await page.goto(`${APP}/dashboard/hub/future-agi/engineering-team?report=1`);
    const dialog = page.getByTestId("hub-report-dialog");
    await expect(dialog).toBeVisible();
    await page.getByTestId("hub-report-details").fill("The README says it survives a kill -9, but the second helper never resumes for me.");
    await page.getByTestId("hub-report-send").click();
    await expect(page.getByTestId("hub-report-received")).toBeVisible();
    await shootAll(page, "report-dialog", { fullPage: false });
    await page.keyboard.press("Escape");

    await page.goto(`${APP}/dashboard/hub/settings`);
    await settle(page);
    const profile = page.getByTestId("hub-publisher-profile").filter({ hasText: "@c-test" });
    await expect(profile).toBeVisible();
    const evidence = profile.getByLabel("Evidence");
    if (await evidence.isVisible().catch(() => false)) {
      await evidence.fill("C Test is the lane's community publisher; the e2e run owns the handle c-test.");
      await profile.getByRole("button", { name: "Request verification" }).click();
    }
    await expect(profile.getByText("Requested. A maintainer will verify or decline it.")).toBeVisible();
  });

  test("B resolves the report by unranking the entry", async ({ page, context }) => {
    await signIn(context, "b");
    await page.goto(`${APP}/dashboard/hub/review/reports`);
    const report = page.getByTestId("hub-report-row").filter({ hasText: "future-agi/engineering-team" });
    await expect(report).toBeVisible();
    await shootAll(page, "review-reports");
    await report.getByLabel("Action").click();
    await page.getByRole("option", { name: "Unrank the entry" }).click();
    await report.getByLabel("Reason").click();
    await page.getByRole("option", { name: "quality", exact: true }).click();
    await report.getByLabel("Note (private)").fill("Reproduced: the second helper does not resume. Unranked until a fixed version.");
    await report.getByRole("button", { name: "Unrank entry" }).click();
    const confirmation = page.getByRole("dialog");
    await expect(confirmation).toContainText("Unrank entry future-agi/engineering-team?");
    await confirmation.getByRole("button", { name: "Unrank entry" }).click();
    await expect(report).toHaveCount(0);
    await page.goto(`${APP}/dashboard/hub/future-agi/engineering-team`);
    await expect(page.getByTestId("hub-unranked-notice")).toContainText("Not ranked: quality");
    await shootAll(page, "entry-unranked");
    await page.goto(`${APP}/dashboard/hub`);
    await expect(page.getByTestId("hub-entry-card").filter({ hasText: "future-agi/engineering-team" })).toHaveCount(0);
  });

  test("B picks an entry for this week and verifies C", async ({ page, context }) => {
    await signIn(context, "b");
    await page.goto(`${APP}/dashboard/hub/review/picks`);
    await expect(page.getByTestId("hub-picks-admin")).toBeVisible();
    await settle(page);
    await page.getByLabel("Entry (publisher/name)").fill(REF);
    await page.getByLabel("Why (shown on the card)").fill("A clear first remix: same loop, a CSV data source, and a disclosed git mode.");
    await page.getByRole("button", { name: "Add pick" }).click();
    await expect(page.getByText(REF).first()).toBeVisible();
    await shootAll(page, "review-picks");
    await page.goto(`${APP}/dashboard/hub`);
    await expect(page.getByTestId("hub-picks")).toContainText("A clear first remix");
    await shootAll(page, "browse-with-picks");

    await page.goto(`${APP}/dashboard/hub/review/publishers`);
    const row = page.getByTestId("hub-publisher-review-row").filter({ hasText: "@c-test" });
    await expect(row).toBeVisible();
    await shootAll(page, "review-publishers");
    await row.getByLabel("Note").fill("Evidence checked against the lane's e2e owner of c-test.");
    await row.getByRole("button", { name: "Verify" }).click();
    await expect(row).toHaveCount(0);
    await page.goto(`${APP}/dashboard/hub/publishers/c-test`);
    await expect(page.getByTestId("hub-tier-badge").first()).toHaveAttribute("data-tier", "verified");
    await shootAll(page, "publisher-verified");
  });

  test("B blocks a digest, removes an entry, and reads the audit log", async ({ page, context }) => {
    await signIn(context, "b");
    await page.goto(`${APP}/dashboard/hub/review/blocklist`);
    await expect(page.getByTestId("hub-blocklist")).toBeVisible();
    await settle(page);
    await page.getByLabel("Digest (sha256:…) or bundle SHA-256").fill(`sha256:${"ab".repeat(32)}`);
    await page.getByLabel("Private note").fill("A copy of an entry with a payload, found outside the Hub.");
    await page.getByRole("button", { name: "Block" }).click();
    await expect(page.getByText("sha256:abababababababab").first()).toBeVisible();

    await page.goto(`${APP}/dashboard/hub/future-agi/notes-plugin`);
    await page.getByTestId("hub-moderate-button").click();
    await page.getByRole("menuitem", { name: "Remove" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Category").click();
    await page.getByRole("option", { name: "publisher's request" }).click();
    await dialog.getByLabel("Private note").fill("Withdrawn by Future AGI for the lane's moderation proof.");
    await dialog.getByRole("button", { name: "Remove" }).click();
    await expect(page.getByTestId("hub-tombstone")).toContainText("Removed at the publisher's request.");
    await shootAll(page, "entry-tombstone");

    await page.goto(`${APP}/dashboard/hub/review/blocklist`);
    await expect(page.getByText("future-agi/notes-plugin@1.0.0")).toBeVisible();
    await shootAll(page, "review-blocklist");

    await page.goto(`${APP}/dashboard/hub/review/audit`);
    await expect(page.getByTestId("hub-audit-rows")).toContainText("entry.removed");
    await settle(page);
    await shootAll(page, "review-audit", { fullPage: false });
    // Each decision of the community path is in the log once, with its actor.
    for (const action of ["review.changes_requested", "review.approved", "report.resolved", "entry.unranked"]) {
      await page.goto(`${APP}/dashboard/hub/review/audit?action=${action}`);
      await expect(page.getByTestId("hub-audit-rows")).toContainText("omega-hub-b@futureagi.local");
    }

    await page.goto(`${APP}/dashboard/hub/c-test/csv-answers/versions`);
    await expect(page.getByTestId("hub-version-row")).toHaveCount(1);
    await shootAll(page, "entry-versions");
  });
});
