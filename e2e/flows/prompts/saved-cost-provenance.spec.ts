import { test, expect } from "../../lib/fixtures";
import { flowAnnotation } from "../../lib/flow-meta";

// PromptTemplateViewSet.retrieve/compare_versions and the E2E-image-only seeder.
const PROMPTS = "/model-hub/prompt-templates/";
const SEED = "/model-hub/e2e/saved-prompt/";
// OutputMeta.jsx and model-cost.js expose the amount and its source tooltip.
const UI_READY = 60_000;
// Declared available_models catalog: gpt-4o $5/M input + $15/M output.
// Default estimate: $0.15/M input + $0.60/M output. Never an invoice claim.
const EXPECTED = [
  {
    version: "v1",
    label: "estimate",
    amount: 0.00045,
    source: "default",
    tokens: [1000, 500],
  },
  {
    version: "v2",
    label: "zero",
    amount: 0,
    source: "available_models",
    tokens: [0, 0],
  },
  {
    version: "v3",
    label: "catalog",
    amount: 0.0125,
    source: "available_models",
    tokens: [1000, 500],
  },
];
type Metadata = {
  cost: { total_cost: number; pricing_source: string };
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
};
type Version = {
  id: string;
  template_version: string;
  output: string[];
  metadata: Metadata[];
};

test.use({ viewport: { width: 1440, height: 900 } });

test(
  "PROMPT-E2E-002: saved prompt costs retain their amount and source after reload and comparison",
  {
    tag: ["@flow"],
    annotation: flowAnnotation({
      id: "PROMPT-E2E-002",
      area: "prompts",
      userGoal:
        "Inspect saved prompt cost estimates, genuine zero usage and catalog amounts without losing their source on reload or comparison.",
      steps: [
        "seed three synthetic saved provider results through the test-image-only fixture using the real calculator",
        "open the saved default result and inspect its exact estimate and source tooltip",
        "reload and inspect the same saved amount and source",
        "select both other saved versions in History and restore all three in comparison",
        "inspect exact estimate, zero and catalog amounts and source tooltips in comparison",
      ],
      backendChecks: [
        "The actor-scoped prompt versions, detail API and comparison API retain the exact synthetic outputs, token usage, calculator amounts and pricing sources.",
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(600_000); // 3 * (60 persistence + 2 * 60 UI) + 60 navigation headroom.
    page.setDefaultTimeout(UI_READY);
    const name = `e2e-prompt2-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    // Pinned from SavedPromptRequest: only a unique name is accepted, never a price/source.
    const seeded = await actor.api.post<{
      prompt_id: string;
      versions: { id: string; version: string }[];
    }>(SEED, { name });
    await testInfo.attach("saved-prompt-ids", {
      contentType: "application/json",
      body: JSON.stringify({
        ...seeded,
        name,
        organizationId: actor.organizationId,
        workspaceId: actor.workspaceId,
        fixture: "Synthetic saved provider results, not live inference",
      }),
    });
    const expectedOutputs = EXPECTED.map(
      (e) => `${name} synthetic ${e.label} result`,
    );
    const assertVersions = (versions: Version[]) => {
      expect(versions.map((v) => v.template_version)).toEqual(
        EXPECTED.map((e) => e.version),
      );
      versions.forEach((v, i) => {
        const e = EXPECTED[i];
        expect(v.id).toBe(seeded.versions[i].id);
        expect(v.output).toEqual([expectedOutputs[i]]);
        expect(v.metadata).toHaveLength(1);
        expect(v.metadata[0].cost.total_cost).toBeCloseTo(e.amount, 12);
        expect(v.metadata[0].cost.pricing_source).toBe(e.source);
        expect(v.metadata[0].usage).toEqual({
          prompt_tokens: e.tokens[0],
          completion_tokens: e.tokens[1],
          total_tokens: e.tokens[0] + e.tokens[1],
        });
      });
    };
    await test.step("backend: exact scoped stored results and detail API", async () => {
      // The fixture transaction has committed before POST returns: no CDC wait is needed.
      const rows = await probe.pg<
        Version & {
          organization_id: string;
          workspace_id: string;
          name: string;
        }
      >(
        `SELECT v.id,v.template_version,v.output,v.metadata,p.organization_id,p.workspace_id,p.name
       FROM model_hub_promptversion v JOIN model_hub_prompttemplate p ON p.id=v.original_template_id
       WHERE p.id=$1 AND v.deleted=false ORDER BY v.template_version`,
        [seeded.prompt_id],
      );
      assertVersions(rows);
      rows.forEach((row) =>
        expect(row).toMatchObject({
          organization_id: actor.organizationId,
          workspace_id: actor.workspaceId,
          name,
        }),
      );
      const detail = await actor.api.get<{
        version: string;
        output: string[];
        metadata: Metadata[];
      }>(`${PROMPTS}${seeded.prompt_id}/`);
      expect(detail.version).toBe("v1");
      expect(detail.output).toEqual(rows[0].output);
      expect(detail.metadata).toEqual(rows[0].metadata);
      await testInfo.attach("stored-and-detail", {
        contentType: "application/json",
        body: JSON.stringify({ rows, detail }),
      });
    });
    const estimate = page.getByText("0.00045 (estimated)", { exact: true });
    const source = page.getByRole("tooltip");
    await test.step("UI: initial saved estimate and source", async () => {
      await page.goto(`/dashboard/workbench/create/${seeded.prompt_id}`, {
        waitUntil: "domcontentloaded",
      });
      await expect(
        page.getByText(expectedOutputs[0], { exact: true }),
      ).toBeVisible({ timeout: UI_READY });
      await expect(estimate).toBeVisible({ timeout: UI_READY });
      await estimate.hover();
      await expect(source).toHaveText(
        "Default estimate, not a verified provider charge",
        { timeout: UI_READY },
      );
      await testInfo.attach("initial-source", {
        contentType: "image/png",
        body: await page.screenshot({ animations: "disabled" }),
      });
    });
    await test.step("UI: reload retains exact estimate and source", async () => {
      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(estimate).toBeVisible({ timeout: UI_READY });
      await estimate.hover();
      await expect(source).toHaveText(
        "Default estimate, not a verified provider charge",
        { timeout: UI_READY },
      );
      await testInfo.attach("reload-source", {
        contentType: "image/png",
        body: await page.screenshot({ animations: "disabled" }),
      });
    });
    await test.step("UI and API: restore all three saved versions in comparison", async () => {
      // MoreActions.jsx initially collapses History behind the Expand control.
      await page.getByRole("button", { name: "Expand", exact: true }).click();
      await page.getByRole("button", { name: "History", exact: true }).click();
      const drawer = page.locator(".MuiDrawer-paper:visible");
      await drawer
        .getByRole("button", { name: "Select to compare", exact: true })
        .click();
      // VersionHistoryDrawer: the current v1 is checked/disabled; the two others are selectable.
      await expect(drawer.getByRole("checkbox")).toHaveCount(3, {
        timeout: UI_READY,
      });
      const choices = drawer.locator('input[type="checkbox"]:not(:disabled)');
      await expect(choices).toHaveCount(2, { timeout: UI_READY });
      await choices.nth(0).check();
      await choices.nth(1).check();
      await drawer
        .getByRole("button", { name: "Compare", exact: true })
        .click();
      // applyCompare restores the history payload locally; it does not call this
      // endpoint. Independently check the normal compare_versions API as well.
      const response = await actor.api.post<{ result: { data: Version[] } }>(
        `${PROMPTS}${seeded.prompt_id}/compare-versions/`,
        { versions: EXPECTED.map((e) => e.version) },
      );
      const comparison = response.result.data;
      assertVersions(
        [...comparison].sort((a, b) =>
          a.template_version.localeCompare(b.template_version),
        ),
      );
      for (const output of expectedOutputs)
        await expect(page.getByText(output, { exact: true })).toBeVisible({
          timeout: UI_READY,
        });
      await expect(estimate).toBeVisible({ timeout: UI_READY });
      await estimate.hover();
      await expect(source).toHaveText(
        "Default estimate, not a verified provider charge",
        { timeout: UI_READY },
      );
      // Zero tokens is also displayed as 0. The cost is the element carrying the source tooltip.
      const zero = page
        .locator('span[aria-label="Catalog pricing"]')
        .filter({ hasText: /^0$/ });
      await expect(zero).toHaveCount(1, { timeout: UI_READY });
      // MUI keeps the outgoing tooltip mounted during its exit transition.
      await page.mouse.move(0, 0);
      await expect(source).toHaveCount(0, { timeout: UI_READY });
      await zero.hover();
      await expect(source).toHaveText("Catalog pricing", { timeout: UI_READY });
      const catalog = page.getByText("0.0125", { exact: true });
      await expect(catalog).toBeVisible({ timeout: UI_READY });
      await page.mouse.move(0, 0);
      await expect(source).toHaveCount(0, { timeout: UI_READY });
      await catalog.hover();
      await expect(source).toHaveText("Catalog pricing", { timeout: UI_READY });
      await expect(
        page.getByText("0 (estimated)", { exact: true }),
      ).toHaveCount(0);
      await testInfo.attach("comparison-source", {
        contentType: "image/png",
        body: await page.screenshot({ animations: "disabled" }),
      });
      await testInfo.attach("comparison", {
        contentType: "application/json",
        body: JSON.stringify(comparison),
      });
    });
  },
);
