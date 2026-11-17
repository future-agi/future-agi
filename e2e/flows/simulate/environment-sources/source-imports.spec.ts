import type { Page } from "@playwright/test";
import { test, expect } from "../../../lib/fixtures";
import {
  type EnvironmentSourceSeed,
  seedEnvironmentForSources,
} from "../../../lib/environment-source-seed";
import { inspectEnvironmentSources } from "../../../lib/environment-source-inspect";
import { ApiError } from "../../../lib/api-client";
import { provisionActor } from "../../../lib/provisioning";
import { seedKnowledgeBaseForSources } from "../../../lib/kb-source-seed";
import {
  customerCsv,
  brokenOrdersJson,
  docxFixture,
  evaluatorOnlyText,
  policyPdf,
  rtfFixture,
  validOrdersJson,
} from "../../../lib/source-fixtures";

const READY = 120_000;

async function showStep(page: Page, milliseconds = 800) {
  if (process.env.E2E_RECORD_VIDEO === "1")
    await page.waitForTimeout(milliseconds);
}

async function openSources(page: Page, environmentId: string) {
  await page.goto(
    `/dashboard/simulate/environments/${environmentId}?tab=sources`,
  );
  await expect(
    page.getByRole("heading", { name: "Sources", exact: true }),
  ).toBeVisible({ timeout: READY });
  await expect(page.getByRole("button", { name: "Upload file" })).toBeEnabled({
    timeout: READY,
  });
  await expect(
    page.getByText(
      /Upload: PDF, TXT, CSV, JSON, up to 100 files, 25 MiB per file and 200 MiB total/,
    ),
  ).toBeVisible();
  await expect(
    page.getByText(
      /Knowledge Base: PDF, DOCX, RTF, TXT, up to 100 files, 25 MiB per file and 200 MiB total/,
    ),
  ).toBeVisible();
  await showStep(page);
}

async function addPreparedSource(page: Page) {
  const dialog = page.getByRole("dialog", { name: "Review source use" });
  await expect(dialog).toBeVisible({ timeout: READY });
  await showStep(page, 1_200);
  const accepted = page.waitForResponse(
    (response) =>
      response.url().includes("/sources/imports/accept/") && response.ok(),
  );
  await dialog.getByRole("button", { name: "Add to draft" }).click();
  const result = (await (await accepted).json()) as {
    validation: {
      status: string;
      affected_scenarios: Array<{ scenario_key: string; executed: boolean }>;
    };
  };
  expect(result.validation).toMatchObject({
    status: "structural_only",
    affected_scenarios: [
      { scenario_key: "source-policy-success", executed: false },
      { scenario_key: "source-policy-refusal", executed: false },
    ],
  });
  await expect(
    page.getByText(/Source added to the Environment draft/),
  ).toBeVisible({ timeout: READY });
  await showStep(page);
}

function expectScenariosPreserved(seed: EnvironmentSourceSeed) {
  const inspected = inspectEnvironmentSources(seed.environmentId);
  expect(inspected.suiteVersionId).toBe(seed.suiteVersionId);
  expect(inspected.scenarioKeys).toEqual(seed.scenarioKeys);
  expect(inspected.scenarioDigest).toMatch(/^[0-9a-f]{64}$/);
  return inspected;
}

async function saveDraft(page: Page) {
  const bar = page.getByTestId("draft-bar");
  await expect(bar).toBeVisible({ timeout: READY });
  await showStep(page);
  await bar.getByRole("button", { name: "Save" }).click();
  await expect(bar).toBeHidden({ timeout: READY });
  await showStep(page);
}

async function chooseWholeKnowledgeBase(page: Page, name: string) {
  await page.getByRole("button", { name: "Select Knowledge Base" }).click();
  const picker = page.getByRole("dialog", { name: "Select a Knowledge Base" });
  await expect(picker).toBeVisible({ timeout: READY });
  await picker.getByText(name, { exact: true }).click();
}

test("T20-E2E-01 policy PDF is reviewed, versioned, and visible in the saved source summary", async ({
  page,
  actor,
}) => {
  test.setTimeout(360_000);
  const seeded = seedEnvironmentForSources(actor);
  await openSources(page, seeded.environmentId);

  await page.getByLabel("Upload purpose").click();
  await page.getByRole("option", { name: "Policy PDF" }).click();
  await page.locator("input[type=file]").setInputFiles({
    name: "support-policy.pdf",
    mimeType: "application/pdf",
    buffer: policyPdf(),
  });

  const dialog = page.getByRole("dialog", { name: "Review source use" });
  await expect(dialog.getByText("Policy rules", { exact: true })).toBeVisible({
    timeout: READY,
  });
  await dialog.getByRole("checkbox").first().check();
  await addPreparedSource(page);
  await saveDraft(page);

  const summary = await actor.api.get<{
    draft: boolean;
    validation_status: string;
    sources: Array<{ purpose: string }>;
  }>(`/simulate/api/harness-environments/${seeded.environmentId}/sources/`);
  expect(summary).toMatchObject({
    draft: false,
    validation_status: "accepted",
    sources: [{ purpose: "policy" }],
  });
  expectScenariosPreserved(seeded);
  await expect(page.getByText("policy", { exact: true })).toBeVisible();
});

test("T20-E2E-02 CSV and JSON records are reviewed together with synthetic PII and a relationship", async ({
  page,
  actor,
}) => {
  test.setTimeout(360_000);
  const seeded = seedEnvironmentForSources(actor);
  await openSources(page, seeded.environmentId);

  await page.locator("input[type=file]").setInputFiles([
    { name: "customers.csv", mimeType: "text/csv", buffer: customerCsv },
    {
      name: "orders.json",
      mimeType: "application/json",
      buffer: validOrdersJson,
    },
  ]);

  const dialog = page.getByRole("dialog", { name: "Review source use" });
  await expect(dialog.getByText("Proposed purpose: seed_records")).toHaveCount(
    2,
    { timeout: READY },
  );
  await expect(
    dialog.getByText("Field mappings", { exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByText(/Replace personal data .* synthetic equivalents/),
  ).toHaveCount(2);
  await dialog.getByRole("checkbox", { name: /buyer_id.*customer_id/ }).check();
  await dialog.getByLabel(/Evaluator-only/).check();
  await addPreparedSource(page);
  await saveDraft(page);

  const summary = await actor.api.get<{
    draft: boolean;
    validation_status: string;
    sources: Array<{ purpose: string }>;
  }>(`/simulate/api/harness-environments/${seeded.environmentId}/sources/`);
  expect(summary.draft).toBe(false);
  expect(summary.validation_status).toBe("accepted");
  expect(summary.sources).toHaveLength(2);
  expect(
    summary.sources.every((source) => source.purpose === "seed_records"),
  ).toBe(true);
  expectScenariosPreserved(seeded);
  await expect(page.getByText("seed_records", { exact: true })).toHaveCount(2);
});

test("T20-E2E-03 one selected Knowledge Base PDF is reviewed as a reference and saved", async ({
  page,
  actor,
}) => {
  test.setTimeout(360_000);
  const seeded = seedEnvironmentForSources(actor);
  const name = `T20 single PDF ${Date.now()}`;
  seedKnowledgeBaseForSources({
    ...actor,
    name,
    documents: [
      {
        filename: "guide.pdf",
        mimeType: "application/pdf",
        buffer: policyPdf(),
      },
    ],
  });
  await openSources(page, seeded.environmentId);

  await chooseWholeKnowledgeBase(page, name);
  const dialog = page.getByRole("dialog", { name: "Review source use" });
  await expect(dialog.getByText("source-1.pdf", { exact: true })).toBeVisible({
    timeout: READY,
  });
  await expect(dialog.getByText("Proposed purpose: reference")).toBeVisible();
  await dialog.getByLabel(/Evaluator-only/).check();
  await addPreparedSource(page);
  await saveDraft(page);

  const summary = await actor.api.get<{
    draft: boolean;
    sources: Array<{ purpose: string }>;
  }>(`/simulate/api/harness-environments/${seeded.environmentId}/sources/`);
  expect(summary).toMatchObject({
    draft: false,
    sources: [{ purpose: "reference" }],
  });
  expectScenariosPreserved(seeded);
});

test("T20-E2E-04 a whole mixed-format Knowledge Base is reviewed and saved atomically", async ({
  page,
  actor,
}) => {
  test.setTimeout(360_000);
  const seeded = seedEnvironmentForSources(actor);
  const name = `T20 mixed KB ${Date.now()}`;
  seedKnowledgeBaseForSources({
    ...actor,
    name,
    documents: [
      {
        filename: "guide.pdf",
        mimeType: "application/pdf",
        buffer: policyPdf(),
      },
      {
        filename: "manual.docx",
        mimeType:
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        buffer: docxFixture(),
      },
      {
        filename: "legacy.rtf",
        mimeType: "application/rtf",
        buffer: rtfFixture,
      },
      {
        filename: "notes.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("TXT KB SENTINEL"),
      },
    ],
  });
  await openSources(page, seeded.environmentId);

  await chooseWholeKnowledgeBase(page, name);
  const dialog = page.getByRole("dialog", { name: "Review source use" });
  await expect(dialog.getByText("Proposed purpose: reference")).toHaveCount(4, {
    timeout: READY,
  });
  await addPreparedSource(page);
  await saveDraft(page);

  const summary = await actor.api.get<{
    draft: boolean;
    sources: Array<{ purpose: string }>;
  }>(`/simulate/api/harness-environments/${seeded.environmentId}/sources/`);
  expect(summary.draft).toBe(false);
  expect(summary.sources).toHaveLength(4);
  expect(
    summary.sources.every((source) => source.purpose === "reference"),
  ).toBe(true);
  expectScenariosPreserved(seeded);
});

test("T20-E2E-05 exact files selected on the Knowledge Base page flow into Environment review", async ({
  page,
  actor,
}) => {
  test.setTimeout(360_000);
  const seeded = seedEnvironmentForSources(actor);
  const kb = seedKnowledgeBaseForSources({
    ...actor,
    name: `T20 exact files ${Date.now()}`,
    documents: [
      {
        filename: "first.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("FIRST EXACT SENTINEL"),
      },
      {
        filename: "second.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("SECOND EXACT SENTINEL"),
      },
      {
        filename: "excluded.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("MUST NOT IMPORT"),
      },
    ],
  });
  await page.goto(`/dashboard/knowledge/${kb.kbId}`);
  for (const filename of ["first.txt", "second.txt"]) {
    const row = page.getByRole("row").filter({ hasText: filename });
    await expect(row).toBeVisible({ timeout: READY });
    await row.getByRole("checkbox").check();
  }
  await page.getByRole("button", { name: "Use in Environment" }).click();
  const chooser = page.getByRole("dialog", { name: "Use in Environment" });
  await expect(chooser).toBeVisible({ timeout: READY });
  await chooser.getByText(seeded.environmentName, { exact: true }).click();

  const review = page.getByRole("dialog", { name: "Review source use" });
  await expect(review.getByText("Proposed purpose: reference")).toHaveCount(2, {
    timeout: READY,
  });
  await expect(review.getByText("source-3.txt", { exact: true })).toHaveCount(
    0,
  );
  await addPreparedSource(page);
  await saveDraft(page);
  const summary = await actor.api.get<{ sources: unknown[] }>(
    `/simulate/api/harness-environments/${seeded.environmentId}/sources/`,
  );
  expect(summary.sources).toHaveLength(2);
  expectScenariosPreserved(seeded);
});

test("T20-E2E-08 evaluator-only data reaches the evaluator but never the tested-agent projection", async ({
  page,
  actor,
}) => {
  test.setTimeout(360_000);
  const seeded = seedEnvironmentForSources(actor);
  await openSources(page, seeded.environmentId);
  await page.locator("input[type=file]").setInputFiles({
    name: "evaluator-only.txt",
    mimeType: "text/plain",
    buffer: evaluatorOnlyText,
  });
  const dialog = page.getByRole("dialog", { name: "Review source use" });
  await expect(
    dialog.getByText("Evaluator-only (recommended for sensitive data)"),
  ).toBeVisible({ timeout: READY });
  await addPreparedSource(page);
  await saveDraft(page);

  const inspected = expectScenariosPreserved(seeded);
  expect(JSON.stringify(inspected.evaluator)).toContain(
    "EVALUATOR_ONLY_T20_SENTINEL",
  );
  expect(JSON.stringify(inspected.agent)).not.toContain(
    "EVALUATOR_ONLY_T20_SENTINEL",
  );
  await expect(page.getByText("reference", { exact: true })).toBeVisible();
});

test("T20-E2E-06 cancelling a whole Knowledge Base review writes no draft or source", async ({
  page,
  actor,
}) => {
  test.setTimeout(360_000);
  const seeded = seedEnvironmentForSources(actor);
  const name = `T20 cancel KB ${Date.now()}`;
  seedKnowledgeBaseForSources({
    ...actor,
    name,
    documents: [
      {
        filename: "cancel.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("CANCELLED SENTINEL"),
      },
    ],
  });
  await openSources(page, seeded.environmentId);
  await chooseWholeKnowledgeBase(page, name);
  const dialog = page.getByRole("dialog", { name: "Review source use" });
  await expect(dialog).toBeVisible({ timeout: READY });
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("draft-bar")).toBeHidden();
  const summary = await actor.api.get<{ draft: boolean; sources: unknown[] }>(
    `/simulate/api/harness-environments/${seeded.environmentId}/sources/`,
  );
  expect(summary).toMatchObject({ draft: false, sources: [] });
  expectScenariosPreserved(seeded);
});

test("T20-E2E-07 a broken relationship is rejected, then corrected files save successfully", async ({
  page,
  actor,
}) => {
  test.setTimeout(360_000);
  const seeded = seedEnvironmentForSources(actor);
  await openSources(page, seeded.environmentId);
  const upload = page.locator("input[type=file]");
  await upload.setInputFiles([
    { name: "customers.csv", mimeType: "text/csv", buffer: customerCsv },
    {
      name: "orders.json",
      mimeType: "application/json",
      buffer: brokenOrdersJson,
    },
  ]);
  let dialog = page.getByRole("dialog", { name: "Review source use" });
  await dialog
    .getByRole("checkbox", { name: /buyer_id.*customer_id/ })
    .check({ timeout: READY });
  await dialog.getByRole("button", { name: "Add to draft" }).click();
  await expect(dialog.getByText(/missing|relationship/i).last()).toBeVisible({
    timeout: READY,
  });
  expectScenariosPreserved(seeded);
  await showStep(page, 1_500);
  await dialog.getByRole("button", { name: "Cancel" }).click();

  await upload.setInputFiles([
    { name: "customers.csv", mimeType: "text/csv", buffer: customerCsv },
    {
      name: "orders.json",
      mimeType: "application/json",
      buffer: validOrdersJson,
    },
  ]);
  dialog = page.getByRole("dialog", { name: "Review source use" });
  await dialog
    .getByRole("checkbox", { name: /buyer_id.*customer_id/ })
    .check({ timeout: READY });
  await addPreparedSource(page);
  await saveDraft(page);
  const summary = await actor.api.get<{ draft: boolean; sources: unknown[] }>(
    `/simulate/api/harness-environments/${seeded.environmentId}/sources/`,
  );
  expect(summary.draft).toBe(false);
  expect(summary.sources).toHaveLength(2);
  expectScenariosPreserved(seeded);
});

test("T20-E2E-09 a Knowledge Base from another workspace is hidden and denied by the API", async ({
  page,
  actor,
  request,
}) => {
  test.setTimeout(360_000);
  const seeded = seedEnvironmentForSources(actor);
  const outsider = await provisionActor(request, `t20-foreign-${Date.now()}`);
  const foreignName = `FOREIGN KB ${Date.now()}`;
  const foreign = seedKnowledgeBaseForSources({
    ...outsider,
    name: foreignName,
    documents: [
      {
        filename: "foreign.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("FOREIGN SECRET"),
      },
    ],
  });
  await openSources(page, seeded.environmentId);
  await page.getByRole("button", { name: "Select Knowledge Base" }).click();
  const picker = page.getByRole("dialog", { name: "Select a Knowledge Base" });
  await expect(picker.getByText(foreignName, { exact: true })).toHaveCount(0);
  await showStep(page, 2_000);
  await picker.getByRole("button", { name: "Cancel", exact: true }).click();

  const capabilities = await actor.api.get<{
    environment_version_id: string;
    draft: { id: string | null; revision: number };
  }>(
    `/simulate/api/harness-environments/${seeded.environmentId}/sources/capabilities/`,
  );
  const denied = actor.api.post(
    `/simulate/api/harness-environments/${seeded.environmentId}/sources/imports/prepare/`,
    {
      expected_environment_version_id: capabilities.environment_version_id,
      expected_draft: capabilities.draft,
      selection: {
        kind: "knowledge_base",
        kb_id: foreign.kbId,
        whole_collection: true,
      },
    },
  );
  await expect(denied).rejects.toMatchObject({
    status: 403,
  } satisfies Partial<ApiError>);
  await expect(page.getByTestId("draft-bar")).toBeHidden();
  expectScenariosPreserved(seeded);
});

test("T20-E2E-10 an old review is rejected after another tab changes the draft", async ({
  page,
  actor,
}) => {
  test.setTimeout(360_000);
  const seeded = seedEnvironmentForSources(actor);
  await openSources(page, seeded.environmentId);
  await page.locator("input[type=file]").setInputFiles({
    name: "old-review.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("OLD REVIEW"),
  });
  const oldDialog = page.getByRole("dialog", { name: "Review source use" });
  await expect(oldDialog).toBeVisible({ timeout: READY });

  const other = await page.context().newPage();
  await openSources(other, seeded.environmentId);
  await other.locator("input[type=file]").setInputFiles({
    name: "newer-review.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("NEWER REVIEW"),
  });
  await addPreparedSource(other);
  await saveDraft(other);
  await other.close();

  await oldDialog.getByRole("button", { name: "Add to draft" }).click();
  await expect(
    oldDialog.getByText(/no longer matches|changed|stale/i).last(),
  ).toBeVisible({ timeout: READY });
  await showStep(page, 1_500);
  await oldDialog.getByRole("button", { name: "Cancel" }).click();
  const summary = await actor.api.get<{ draft: boolean; sources: unknown[] }>(
    `/simulate/api/harness-environments/${seeded.environmentId}/sources/`,
  );
  expect(summary).toMatchObject({ draft: false });
  expect(summary.sources).toHaveLength(1);
  expectScenariosPreserved(seeded);
});

test("T20-E2E-11 saving a newer source version does not mutate the older version snapshot", async ({
  page,
  actor,
}) => {
  test.setTimeout(360_000);
  const seeded = seedEnvironmentForSources(actor);
  await openSources(page, seeded.environmentId);
  await page.locator("input[type=file]").setInputFiles({
    name: "version-one.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("VERSION_ONE_SENTINEL"),
  });
  await addPreparedSource(page);
  await saveDraft(page);
  const first = await actor.api.get<{ environment_version_id: string }>(
    `/simulate/api/harness-environments/${seeded.environmentId}/sources/capabilities/`,
  );

  await page.locator("input[type=file]").setInputFiles({
    name: "version-two.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("VERSION_TWO_SENTINEL"),
  });
  await addPreparedSource(page);
  await saveDraft(page);
  const oldSnapshot = inspectEnvironmentSources(
    seeded.environmentId,
    first.environment_version_id,
  );
  const currentSnapshot = inspectEnvironmentSources(seeded.environmentId);
  expect(JSON.stringify(oldSnapshot.evaluator)).toContain(
    "VERSION_ONE_SENTINEL",
  );
  expect(JSON.stringify(oldSnapshot.evaluator)).not.toContain(
    "VERSION_TWO_SENTINEL",
  );
  expect(JSON.stringify(currentSnapshot.evaluator)).toContain(
    "VERSION_ONE_SENTINEL",
  );
  expect(JSON.stringify(currentSnapshot.evaluator)).toContain(
    "VERSION_TWO_SENTINEL",
  );
  expectScenariosPreserved(seeded);
  await page.getByRole("tab", { name: /Versions/ }).click();
  await expect(page.getByText(/env v3|v3/i).first()).toBeVisible({
    timeout: READY,
  });
});
