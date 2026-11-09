/* eslint-env node */
// Reviewer actions in the real review page, as a maintainer does them.
const { expect } = require("playwright/test");

const { APP, settle } = require("./helpers.cjs");

const EMPTY = /^\s*(none\.?)?\s*$/i;

async function openReview(page, ref) {
  await page.goto(`${APP}/dashboard/hub/review`);
  const row = page.getByTestId("hub-queue-row").filter({ hasText: ref }).first();
  await expect(row).toBeVisible({ timeout: 60_000 });
  await row.click();
  await expect(page.getByTestId("hub-review-ref")).toContainText(ref);
  await settle(page);
}

async function startReview(page) {
  const start = page.getByTestId("hub-start-review");
  if (await start.isVisible().catch(() => false)) {
    await start.click();
    await expect(page.getByText(/You started this review/)).toBeVisible();
  }
}

// Approve: acknowledge each flag, tick the checklist of the current rules,
// and write any disclosure part the checks observed facts for. `rewrite`
// maps a part to the reviewer's own wording.
async function approve(page, { rewrite = {} } = {}) {
  await page.getByTestId("hub-decision-approve-tab").click();
  const approveButton = page.getByTestId("hub-decision-submit");
  await expect(approveButton).toBeDisabled();
  // The checklist renders once GET /rules answers.
  await expect(page.getByTestId("hub-checklist-item").first()).toBeVisible();
  for (const box of await page.getByTestId("hub-flag-ack").all()) await box.check();
  for (const box of await page.getByTestId("hub-checklist-item").all()) await box.check();
  for (const part of ["summary", "network", "files", "shell", "background", "credentials"]) {
    const input = page.getByTestId(`hub-disclosure-${part}-input`);
    if (rewrite[part]) {
      await input.fill(rewrite[part]);
      continue;
    }
    const observed = page.getByTestId(`hub-observed-${part}`);
    if ((await observed.count()) && EMPTY.test(await input.inputValue())) {
      const facts = (await observed.innerText()).replace(/^Observed:\s*/, "");
      const limit = Number(await input.getAttribute("maxlength"));
      await input.fill(`Confirmed in review: ${facts}.`.slice(0, limit));
    }
  }
  await expect(approveButton).toBeEnabled();
  await approveButton.click();
  await expect(page).toHaveURL(/\/dashboard\/hub\/review$/, { timeout: 60_000 });
}

async function requestChanges(page, { reason, file, line, note }) {
  await page.getByTestId("hub-decision-changes-tab").click();
  await page.getByTestId("hub-decision-reason-input").fill(reason);
  await page.getByTestId("hub-change-note-file").locator("..").click();
  await page.getByRole("option", { name: file, exact: true }).click();
  await page.getByTestId("hub-change-note-line").fill(String(line));
  await page.getByTestId("hub-change-note-text").fill(note);
  await page.getByTestId("hub-change-note-add").click();
  await expect(page.getByTestId("hub-change-note-draft")).toHaveCount(1);
  await page.getByTestId("hub-decision-submit").click();
  await expect(page).toHaveURL(/\/dashboard\/hub\/review$/, { timeout: 60_000 });
}

module.exports = { approve, openReview, requestChanges, startReview };
