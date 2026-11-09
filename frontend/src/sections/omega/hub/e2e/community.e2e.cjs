/* eslint-env node */
// The human-in-the-loop path, end to end, against the lane's real Hub backend
// and the real sandboxed checker (SPEC §3, §5, §16 step 4):
//   maintainer B approves the Official seeds in the review page;
//   user C (not a maintainer) gets no review queue;
//   C creates c-test, uploads csv-answers 1.0.0 in the Publish page;
//   the checks run; it waits in B's queue; B requests changes with a note;
//   C sees the reason and the note, uploads again; B approves;
//   the entry is on the Hub home with the Community reviewed badge.
const path = require("node:path");
const { expect, test } = require("playwright/test");

const { APP, registry, settle, shootAll, signIn } = require("./helpers.cjs");
const { approve, openReview, requestChanges, startReview } = require("./review-steps.cjs");

const REF = "c-test/csv-answers";
const state = {};

async function bundleFor({ version, shellDisclosed, badCover }) {
  const { REPO, packFolder } = await registry();
  const { communityEntryFiles } = await import("./lib/community-entry.mjs");
  const today = new Date().toISOString().slice(0, 10);
  let mainText = "";
  const data = packFolder(path.join(REPO, "futureagi/omega_hub/tests/fixtures/entries/first-agent"), (files) => {
    const out = communityEntryFiles(files, { version, shellDisclosed, today, badCover });
    mainText = out.find((f) => f.name === "entry/main.mjs").data.toString("utf8");
    return out;
  });
  const line = mainText.split("\n").findIndex((l) => l.includes("node:child_process")) + 1;
  return { buffer: Buffer.from(data), line };
}

async function uploadInPage(page, { version, shellDisclosed, notes, badCover }) {
  const { buffer, line } = await bundleFor({ version, shellDisclosed, badCover });
  await page.goto(`${APP}/dashboard/hub/publish`);
  await expect(page.getByTestId("hub-upload-card")).toBeVisible();
  await settle(page);
  await page.getByTestId("hub-upload-input").setInputFiles({ name: `csv-answers-${version}.tar.gz`, mimeType: "application/gzip", buffer });
  await page.getByTestId("hub-upload-notes").fill(notes);
  await page.getByTestId("hub-upload-submit").click();
  await expect(page).toHaveURL(/\/dashboard\/hub\/submissions\/[0-9a-f-]{36}$/, { timeout: 60_000 });
  return { id: page.url().split("/").pop(), line };
}

async function waitForStatus(page, status) {
  await expect(page.getByTestId("hub-status-chip")).toHaveAttribute("data-status", status, { timeout: 8 * 60_000 });
}

test.describe.serial("Omega Hub community path, real backend", () => {
  test.beforeAll(async () => {
    const { ensureUser } = await import("./lib/django.mjs");
    ensureUser({ email: "omega-hub-c@futureagi.local", name: "C Test", org: "C Test Org (hub e2e)" });
  });

  test("B approves the Official seeds in the review page", async ({ page, context }) => {
    const session = await signIn(context, "b");
    // Which seeds wait for review, from the API: a DOM count could run before the queue loads.
    const { call } = await registry();
    const queue = await call("/review/queue", { headers: session.headers });
    const refs = (queue.results || []).map((row) => row.ref).filter((ref) => ref.startsWith("future-agi/"));
    test.skip(refs.length === 0, "the Official seeds are already approved");
    for (const ref of refs.sort((a, b) => (a === "future-agi/first-agent" ? -1 : b === "future-agi/first-agent" ? 1 : 0))) {
      await openReview(page, ref);
      await startReview(page);
      if (ref === "future-agi/first-agent") {
        await shootAll(page, "review-official-flags");
        await approve(page, {
          rewrite: { shell: "None at run time. Its test runs node main.mjs as a child process to check the printed answer." },
        });
      } else {
        await approve(page);
      }
    }
    await page.goto(`${APP}/dashboard/hub`);
    await expect(page.getByTestId("hub-entry-card").filter({ hasText: "future-agi/first-agent" })).toBeVisible();
    const listed = await call("/entries?publisher=future-agi");
    expect(listed.count).toBe(3);
  });

  test("C, not a maintainer, sees no review queue", async ({ page, context }) => {
    await signIn(context, "c");
    await page.goto(`${APP}/dashboard/hub`);
    await expect(page.getByTestId("hub-nav-main")).toBeVisible();
    await expect(page.getByTestId("hub-nav-review")).toHaveCount(0);
    await page.goto(`${APP}/dashboard/hub/review`);
    await expect(page.getByTestId("hub-forbidden")).toBeVisible();
    await expect(page.getByTestId("hub-queue-row")).toHaveCount(0);
    await shootAll(page, "review-forbidden");
  });

  test("C creates the publisher c-test", async ({ page, context }) => {
    await signIn(context, "c");
    await page.goto(`${APP}/dashboard/hub/settings`);
    await expect(page.getByTestId("hub-settings")).toBeVisible();
    await settle(page);
    if (await page.getByTestId("hub-create-publisher").isVisible().catch(() => false)) {
      await shootAll(page, "settings-new-user");
      await page.getByTestId("hub-publisher-handle").fill("c-test");
      await page.getByTestId("hub-publisher-display-name").fill("C Test");
      await expect(page.getByTestId("hub-publisher-handle")).toHaveValue("c-test");
      await expect(page.getByTestId("hub-publisher-create")).toBeEnabled();
      await page.getByTestId("hub-publisher-create").click();
    }
    await expect(page.getByTestId("hub-publisher-profile").filter({ hasText: "@c-test" })).toBeVisible();
  });

  test("a broken upload stops at the automated checks, with each finding", async ({ page, context }) => {
    await signIn(context, "c");
    await uploadInPage(page, { version: "1.0.0", shellDisclosed: false, badCover: true, notes: "A first try." });
    await waitForStatus(page, "checks_failed");
    const findings = page.getByTestId("hub-findings");
    await expect(findings).toContainText("cover.from");
    await expect(findings).toContainText("not sent to maintainers");
    await expect(findings).toContainText("Fix: pick a color");
    await shootAll(page, "submission-checks-failed");
  });

  test("C uploads csv-answers 1.0.0 and follows it through the checks", async ({ page, context }) => {
    await signIn(context, "c");
    const { id, line } = await uploadInPage(page, {
      version: "1.0.0",
      shellDisclosed: false,
      notes: "First version: forecasts from a CSV table, and an optional --git mode.",
    });
    Object.assign(state, { first: id, line });
    await expect(page.getByTestId("hub-step-tracker")).toBeVisible();
    await shootAll(page, "submission-checking", { sizes: [{ width: 1280, height: 900 }], modes: ["light"] });
    await waitForStatus(page, "in_review");
    await expect(page.getByTestId("hub-status-text")).toContainText("Waiting for a Future AGI maintainer");
    await shootAll(page, "submission-in-review");
    // Nothing is public before a maintainer's approval (SPEC §3.2).
    const { call } = await registry();
    const listed = await call(`/entries?q=csv-answers`);
    expect((listed.results || []).map((r) => r.ref)).not.toContain(REF);
  });

  test("B requests changes with a reason and a note on the spawning line", async ({ page, context }) => {
    await signIn(context, "b");
    await page.goto(`${APP}/dashboard/hub/review`);
    await expect(page.getByTestId("hub-queue-row").filter({ hasText: REF })).toBeVisible({ timeout: 60_000 });
    await shootAll(page, "review-queue");
    await openReview(page, REF);
    await startReview(page);
    await expect(page.getByText(/imports node:child_process/).first()).toBeVisible();
    await expect(page.getByText(/disclosure\.shell says nothing/).first()).toBeVisible();
    await shootAll(page, "review-page");
    await page.getByTestId("hub-review-tab-files").click();
    await expect(page.getByTestId("hub-file-viewer")).toBeVisible();
    await shootAll(page, "review-files", { sizes: [{ width: 1280, height: 900 }] });
    await page.getByTestId("hub-review-tab-report").click();
    await requestChanges(page, {
      reason: "The disclosure says the entry runs no shell commands, but main.mjs runs git through execFileSync. Disclose it under Shell.",
      file: "main.mjs",
      line: state.line,
      note: "execFileSync runs git here: say so in disclosure.shell.",
    });
  });

  test("C sees the reason and the note, then uploads a new version", async ({ page, context }) => {
    await signIn(context, "c");
    await page.goto(`${APP}/dashboard/hub/submissions/${state.first}`);
    await waitForStatus(page, "changes_requested");
    await expect(page.getByTestId("hub-decision-reason")).toContainText("Disclose it under Shell");
    await expect(page.getByTestId("hub-change-note")).toContainText("execFileSync runs git here");
    await expect(page.getByTestId("hub-change-notes")).toContainText("main.mjs");
    await shootAll(page, "submission-changes-requested");
    await page.getByTestId("hub-resubmit").click();
    await expect(page).toHaveURL(/\/dashboard\/hub\/publish$/);
    const { id } = await uploadInPage(page, {
      version: "1.0.0",
      shellDisclosed: true,
      notes: "Discloses the --git mode under Shell, as asked in review.",
    });
    state.second = id;
    await waitForStatus(page, "in_review");
  });

  test("B approves the new upload with a tightened disclosure", async ({ page, context }) => {
    await signIn(context, "b");
    await openReview(page, REF);
    await startReview(page);
    await approve(page, {
      rewrite: { shell: "Runs `git --version` only when started with --git. Its test runs node main.mjs as a child process." },
    });
  });

  test("the entry is published, on the home page with the Community reviewed badge", async ({ page, context }) => {
    await signIn(context, "c");
    await page.goto(`${APP}/dashboard/hub/submissions/${state.second}`);
    await waitForStatus(page, "approved");
    await expect(page.getByTestId("hub-published")).toContainText(`${REF}@1.0.0`);
    await shootAll(page, "submission-approved");

    await page.goto(`${APP}/dashboard/hub?sort=new`);
    const card = page.getByTestId("hub-entry-card").filter({ hasText: REF });
    await expect(card).toBeVisible();
    await expect(card.getByTestId("hub-tier-badge")).toHaveAttribute("data-tier", "community");
    await page.goto(`${APP}/dashboard/hub`);
    await shootAll(page, "browse");

    await card.click().catch(() => page.goto(`${APP}/dashboard/hub/c-test/csv-answers`));
    await expect(page.getByTestId("hub-entry-header")).toBeVisible();
    await expect(page.getByTestId("hub-derived-from")).toContainText("future-agi/first-agent");
    await expect(page.getByTestId("hub-disclosure-confirmed")).toHaveText("Confirmed in review");
    await expect(page.getByTestId("hub-disclosure")).toContainText("Runs `git --version` only when started with --git");
    await expect(page.getByTestId("hub-discord-link")).toHaveAttribute("href", "https://discord.com/invite/n2tCUKBkAw");
    await shootAll(page, "entry-community");
    await page.getByTestId("hub-tab-files").click();
    await expect(page.getByTestId("hub-file-viewer")).toBeVisible();
    await shootAll(page, "entry-files", { sizes: [{ width: 1280, height: 900 }] });
    await page.getByTestId("hub-tab-versions").click();
    await expect(page.getByTestId("hub-version-row")).toHaveCount(1);

    await page.goto(`${APP}/dashboard/hub/future-agi/first-agent`);
    await expect(page.getByTestId("hub-remix-count")).toHaveText("Remixes (1)");
    const like = page.getByTestId("hub-like-button");
    if ((await like.getAttribute("aria-pressed")) !== "true") await like.click();
    await expect(like).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("hub-like-count")).toHaveText("1");
    await shootAll(page, "entry-official");
  });

  test("C's lists and profile show the published entry", async ({ page, context }) => {
    await signIn(context, "c");
    for (const [route, name, testId] of [
      ["/dashboard/hub/submissions", "submissions", "hub-submissions"],
      ["/dashboard/hub/publishers/c-test", "publisher", "hub-publisher"],
      ["/dashboard/hub/publishers", "publishers", "hub-publishers"],
      ["/dashboard/hub/publish", "publish", "hub-publish"],
      ["/dashboard/hub/settings", "settings", "hub-settings"],
    ]) {
      await page.goto(`${APP}${route}`);
      await expect(page.getByTestId(testId)).toBeVisible();
      await settle(page);
      await shootAll(page, name);
    }
    await page.goto(`${APP}/dashboard/hub/submissions`);
    const rows = page.getByTestId("hub-submission-row").filter({ hasText: REF });
    await expect(rows.first()).toBeVisible();
    // The blocked try, the change request and the approved upload.
    await expect(rows).toHaveCount(3);
    await expect(page.locator('[data-testid="hub-submission-row"][data-status="approved"]')).toHaveCount(1);
  });
});
