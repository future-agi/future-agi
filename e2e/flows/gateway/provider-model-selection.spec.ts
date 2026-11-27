import type { Locator, Page, Request, Route } from "@playwright/test";
import { test, expect } from "../../lib/fixtures";
import { E2E } from "../../lib/env";
import { flowAnnotation } from "../../lib/flow-meta";

// Pinned facts:
// - The dialog lists models through POST /agentcc/provider-credentials/fetch_models/
//   (frontend/src/utils/axios.js, useFetchProviderModels in
//   sections/gateway/providers/hooks/useGatewayConfig.js reads data.result.models).
//   The managed stack refuses private provider URLs, so mock-llm cannot be listed
//   from this dialog. The fix under test changes only how the picker commits
//   selections, so this flow serves that one call from a stub. The envelope is
//   GeneralMethods.success_response({"models": [...]}) from
//   agentcc/views/provider_credential.py fetch_models.
// - Add and Edit both save through POST /agentcc/gateways/default/update-provider/
//   (AddProviderDialog.jsx handleSave), and the gateway config API returns
//   result.providers[name].models from models_list (agentcc/views/gateway.py config).
// - The `.invalid` base URL never resolves, which ensure_provider_base_url_allowed
//   accepts on save, so nothing outside the stack is contacted.
const UI_READY = 60_000;
const FETCH_MODELS = "/agentcc/provider-credentials/fetch_models/";
const UPDATE_PROVIDER = "/agentcc/gateways/default/update-provider/";
const GATEWAY_CONFIG = "/agentcc/gateways/default/config/";

interface GatewayConfigResponse {
  result: { providers: Record<string, { models?: string[] }> };
}

test(
  "GW-E2E-024: Add/Edit Provider saves only the models the user picked or typed",
  {
    tag: ["@flow"],
    annotation: flowAnnotation({
      id: "GW-E2E-024",
      area: "gateway",
      userGoal:
        "A platform engineer adds and then edits a provider, and only the models they deliberately picked or typed are saved: hovering a listed model and clicking away neither selects it nor drops a selected one, and a typed model ID is kept, trimmed and once, whether they click away or press Save directly",
      steps: [
        "open Gateway providers and start adding a custom provider with a base URL and API key",
        "pick one listed model, hover another listed model and click away",
        "type a model ID with surrounding spaces and click away",
        "add the provider",
        "edit the provider, hover a selected and an unselected listed model and click away",
        "type another model ID and press Save Changes directly",
        "reopen the provider and see exactly the picked and typed models",
        "in the isolated legacy case, edit only the model list of a provider with stored negative concurrency, then exercise positive, typed-negative and zero controls",
      ],
      backendChecks: [
        "the Add save posts config.models exactly [listedA, typedOne] to update-provider (typed ID trimmed, hovered listedB absent) and gets 200",
        "the gateway config API returns the added provider with models exactly [listedA, typedOne]",
        "the Edit direct save posts and persists models exactly [listedA, typedOne, typedTwo]: update-provider body and gateway config API agree",
        "legacy concurrency is read without mutation, saved as explicit zero on the wire, persisted in PG, and inherited in the pushed tenant config; a new negative returns a field error without changing positive nine",
      ],
    }),
  },
  async ({ page, actor }, testInfo) => {
    // Seven UI_READY waits (page and card, the Add listing, the Add save, the card
    // after save, the Edit hydration and listing, the Edit save, the reopened
    // chips) = 7 × 60 s = 420 s, plus 30 s for navigation, the stub and two
    // config reads. Above the 120 s default, so the failing wait reports itself.
    test.setTimeout(450_000);
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const providerName = `e2e-gw12-${suffix}`;
    const baseUrl = `https://e2e-gw12-${suffix}.invalid`;
    const apiKey = `e2e-key-${suffix}`;
    const listedA = `e2e-gw12-listed-a-${suffix}`;
    const listedB = `e2e-gw12-listed-b-${suffix}`;
    const typedOne = `e2e-gw12-typed-one-${suffix}`;
    const typedTwo = `e2e-gw12-typed-two-${suffix}`;
    await testInfo.attach("minted-names", {
      contentType: "application/json",
      body: JSON.stringify({
        organizationId: actor.organizationId,
        providerName,
        baseUrl,
        listedA,
        listedB,
        typedOne,
        typedTwo,
      }),
    });

    const listingRequests: Record<string, unknown>[] = [];
    await page.route(
      (url) => url.pathname.endsWith(FETCH_MODELS),
      async (route: Route) => {
        listingRequests.push(
          route.request().postDataJSON() as Record<string, unknown>,
        );
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            status: true,
            result: { models: [listedA, listedB] },
          }),
        });
      },
    );

    const dialog = page.getByRole("dialog");
    const chips = dialog.locator(".MuiAutocomplete-tag");
    const picker = dialog.locator(".MuiAutocomplete-root input");
    const baseUrlField = dialog.getByLabel(/^Base URL/);
    const listed = (name: string) => page.getByRole("option", { name });
    const savedProvider = async (p: Page, buttonName: string) => {
      const response = p.waitForResponse(
        (res) =>
          res.url().includes(UPDATE_PROVIDER) &&
          res.request().method() === "POST",
        { timeout: UI_READY },
      );
      await dialog
        .getByRole("button", { name: buttonName, exact: true })
        .click();
      const res = await response;
      expect(res.status()).toBe(200);
      return (res.request() as Request).postDataJSON() as {
        name: string;
        config: { models?: string[] };
      };
    };
    const configModels = async () => {
      const config = await actor.api.get<GatewayConfigResponse>(GATEWAY_CONFIG);
      return config.result.providers[providerName]?.models;
    };

    // Add.
    await page.goto("/dashboard/gateway/providers/config");
    await page.getByRole("button", { name: "Add Provider" }).first().click();
    await expect(
      dialog.getByText("Add Provider", { exact: true }).first(),
    ).toBeVisible({ timeout: UI_READY });
    await dialog.getByRole("combobox", { name: /^Provider/ }).click();
    await page.getByRole("option", { name: "Custom / Self-hosted" }).click();
    await dialog.getByLabel(/^Name/).fill(providerName);
    await baseUrlField.fill(baseUrl);
    await dialog.getByLabel(/^API Key/).fill(apiKey);
    await expect(dialog.getByText("(2 available)")).toBeVisible({
      timeout: UI_READY,
    });

    await picker.click();
    await listed(listedA).click();
    await listed(listedB).hover();
    await baseUrlField.click();
    await expect(chips).toHaveText([listedA], { timeout: UI_READY });

    await picker.fill(`  ${typedOne}  `);
    await baseUrlField.click();
    await expect(chips).toHaveText([listedA, typedOne], { timeout: UI_READY });

    // Check 1: what the UI sends on Add.
    const added = await savedProvider(page, "Add Provider");
    expect(added.name).toBe(providerName);
    expect(added.config.models).toEqual([listedA, typedOne]);
    await expect(
      page.getByText(`Provider "${providerName}" added`),
    ).toBeVisible({ timeout: UI_READY });
    // Check 2: what the gateway config API stored.
    expect(await configModels()).toEqual([listedA, typedOne]);

    // Edit: hover a selected and an unselected option, then click away.
    const providerCard: Locator = page
      .locator("div")
      .filter({ hasText: providerName })
      .filter({ has: page.getByTitle("Edit provider") })
      .last();
    await expect(providerCard).toBeVisible({ timeout: UI_READY });
    await providerCard.getByTitle("Edit provider").click();
    await expect(chips).toHaveText([listedA, typedOne], { timeout: UI_READY });
    await expect(dialog.getByText("(2 available)")).toBeVisible({
      timeout: UI_READY,
    });
    await picker.click();
    await listed(listedA).hover();
    await baseUrlField.click();
    await expect(chips).toHaveText([listedA, typedOne], { timeout: UI_READY });
    await picker.click();
    await listed(listedB).hover();
    await baseUrlField.click();
    await expect(chips).toHaveText([listedA, typedOne], { timeout: UI_READY });

    // Type and press Save Changes directly, without clicking away first.
    await picker.fill(typedTwo);
    // Check 3: the direct save posts and persists the typed model.
    const edited = await savedProvider(page, "Save Changes");
    expect(edited.config.models).toEqual([listedA, typedOne, typedTwo]);
    expect(await configModels()).toEqual([listedA, typedOne, typedTwo]);

    await providerCard.getByTitle("Edit provider").click();
    await expect(chips).toHaveText([listedA, typedOne, typedTwo], {
      timeout: UI_READY,
    });
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();

    // The stub served both the typed-key listing (Add) and the stored-credential
    // listing (Edit); a guard that the stub, not a leftover, fed the picker.
    await testInfo.attach("listing-requests", {
      contentType: "application/json",
      body: JSON.stringify(listingRequests),
    });
    expect(listingRequests).toContainEqual(
      expect.objectContaining({
        base_url: baseUrl,
        api_key: apiKey,
        api_format: "openai",
        api_path_prefix: "/v1",
      }),
    );
    expect(listingRequests).toContainEqual(
      expect.objectContaining({
        provider_name: providerName,
        api_path_prefix: "/v1",
      }),
    );
  },
);

// Public synthetic fixture credential used only by the local managed stack.
const LOCAL_FIXTURE_TOKEN = "local-dev-only-admin-token-replace-me";

// A companion case of the existing catalog entry, not a second @flow ID.
test("GW-E2E-024: model-only edit of stored negative concurrency preserves inheritance", async ({
  page,
  actor,
  probe,
}, testInfo) => {
  // 6 × 60s UI stages, 6 × 30s storage/sync polls, 120s setup and cleanup.
  test.setTimeout(660_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const name = `e2e-gw24l-${suffix}`;
  const first = `initial-${suffix}`,
    second = `extra-${suffix}`;
  const models = [first, second];
  const scope = [actor.organizationId, name];
  const where =
    "organization_id = $1 AND provider_name = $2 AND deleted = false";
  const table = "agentcc_provider_credential"; // AgentccProviderCredential.Meta.db_table
  type Limits = {
    max_concurrent?: number;
    timeout?: number;
    default_timeout?: number;
    models?: string[];
  };
  const readPG = () =>
    probe.pg<{ limit: number; timeout: number; models: string[] }>(
      `SELECT max_concurrent AS limit, default_timeout_seconds AS timeout, models_list AS models FROM ${table} WHERE ${where}`,
      scope,
    );
  const readConfig = async () =>
    (
      await actor.api.get<{ result: { providers: Record<string, Limits> } }>(
        GATEWAY_CONFIG,
      )
    ).result.providers[name];
  const reload = async () =>
    expect(
      (
        await actor.api.post<{ result: { gateway_synced: boolean } }>(
          "/agentcc/gateways/default/reload/",
          {},
        )
      ).result.gateway_synced,
    ).toBe(true);
  // Same managed fixture as key-guardrail-policy.spec.ts. Use native fetch so a
  // Playwright failure trace cannot capture the admin header or full org list.
  const tenant = async () => {
    const res = await fetch(new URL("/-/orgs/configs", E2E.gatewayUrl), {
      headers: { Authorization: `Bearer ${LOCAL_FIXTURE_TOKEN}` },
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
    });
    expect(res.status).toBe(200);
    const data = (await res.json()) as {
      data: Record<string, { providers?: Record<string, Limits> }>;
    };
    const provider = data.data[actor.organizationId]?.providers?.[name];
    return provider
      ? {
          limit: provider.max_concurrent ?? null,
          timeout: provider.timeout,
          models: provider.models,
        }
      : null;
  };
  const receipt = (label: string, value: unknown) =>
    testInfo.attach(label, {
      contentType: "application/json",
      body: JSON.stringify(value),
    });
  const checkState = async (
    limit: number,
    expectedModels: string[],
    label: string,
  ) => {
    const expectedPG = [{ limit, timeout: 60, models: expectedModels }];
    const expectedTenant = {
      limit: limit === 0 ? null : limit,
      timeout: 60,
      models: expectedModels,
    };
    // PG + pushed state from actual reads, one bounded poll per transition.
    const snapshot = async () => ({
      pg: await readPG(),
      tenant: await tenant(),
    });
    let last: Awaited<ReturnType<typeof snapshot>> | undefined;
    await expect
      .poll(async () => (last = await snapshot()), { timeout: 30_000 })
      .toEqual({ pg: expectedPG, tenant: expectedTenant });
    await receipt(label, last);
  };
  const dialog = page.getByRole("dialog");
  const edit = async () => {
    const card = page
      .locator("div")
      .filter({ hasText: name })
      .filter({ has: page.getByTitle("Edit provider") })
      .last();
    await card.getByTitle("Edit provider").click({ timeout: UI_READY });
    await expect(dialog.getByText("(2 available)")).toBeVisible({
      timeout: UI_READY,
    });
  };
  const field = dialog.getByLabel("Max Concurrent");
  const picker = dialog.locator(".MuiAutocomplete-root input");
  const save = async (
    button: string,
    limit: number,
    expectedModels: string[],
    status = 200,
    keyboard = false,
  ) => {
    const submit = dialog.getByRole("button", { name: button, exact: true });
    const [r] = await Promise.all([
      page.waitForResponse(
        (r) =>
          new URL(r.url()).pathname.endsWith(UPDATE_PROVIDER) &&
          r.request().method() === "POST",
        { timeout: UI_READY },
      ),
      keyboard ? submit.press("Enter") : submit.click(),
    ]);
    const wire = r.request().postDataJSON() as { name: string; config: Limits };
    const safe = {
      name: wire.name,
      limit: wire.config.max_concurrent,
      timeout: wire.config.default_timeout,
      models: wire.config.models,
      status: r.status(),
    };
    await receipt(`save-${limit}-${status}`, safe);
    expect(safe).toEqual({
      name,
      limit,
      timeout: 60,
      models: expectedModels,
      status,
    });
    if (status === 400)
      expect((await r.json()).details).toEqual({
        config: ["max_concurrent: Must be greater than or equal to zero."],
      });
    else {
      expect((await r.json()).result.gateway_synced).toBe(true);
      await expect(dialog).toBeHidden({ timeout: UI_READY });
    }
  };
  await page.route(
    (url) => url.pathname.endsWith(FETCH_MODELS),
    (route) =>
      route.fulfill({
        json: { status: true, result: { models: [first, second] } },
      }),
  );
  try {
    await test.step(
      "Add a scoped provider at seven",
      async () => {
        await page.goto("/dashboard/gateway/providers/config");
        await page
          .getByRole("button", { name: "Add Provider" })
          .first()
          .click();
        await dialog.getByRole("combobox", { name: /^Provider/ }).click();
        await page
          .getByRole("option", { name: "Custom / Self-hosted" })
          .click();
        await dialog.getByLabel(/^Name/).fill(name);
        await dialog.getByLabel(/^Base URL/).fill(`https://${name}.invalid`);
        await dialog.getByLabel(/^API Key/).fill(`synthetic-${suffix}`);
        await expect(dialog.getByText("(2 available)")).toBeVisible();
        await picker.click();
        await page.getByRole("option", { name: first, exact: true }).click();
        await dialog.getByLabel("Timeout").fill("60");
        await field.fill("7");
        await save("Add Provider", 7, [first]);
      },
      { timeout: UI_READY },
    );
    await checkState(7, [first], "positive-create");
    const seeded = await probe.pg<{ id: string; max_concurrent: number }>(
      `UPDATE ${table} SET max_concurrent = -1 WHERE ${where} RETURNING id,max_concurrent`,
      scope,
    );
    expect(seeded).toHaveLength(1);
    expect(seeded[0].max_concurrent).toBe(-1);
    await receipt("legacy-id", {
      org: actor.organizationId,
      provider: name,
      ...seeded[0],
    });
    await reload();
    await checkState(-1, [first], "legacy-reloaded");
    expect((await readConfig()).max_concurrent).toBe(-1);
    expect((await readPG())[0].limit).toBe(-1);
    await test.step(
      "Save the actual model-only edit",
      async () => {
        await page.reload();
        await edit();
        await picker.fill(second);
        await save("Save Changes", 0, models);
      },
      { timeout: UI_READY },
    );
    await checkState(0, models, "legacy-saved");
    expect(await readConfig()).toMatchObject({
      max_concurrent: 0,
      default_timeout: 60,
      models,
    });
    await test.step(
      "Reopen zero; establish nine before rejection",
      async () => {
        await edit();
        await expect(field).toHaveValue("0");
        await field.fill("9");
        await save("Save Changes", 9, models);
      },
      { timeout: UI_READY },
    );
    await checkState(9, models, "positive-before-rejection");
    await test.step(
      "Negative submission gives a field-specific error",
      async () => {
        await edit();
        await field.fill("-5");
        await save("Save Changes", -5, models, 400);
      },
      { timeout: UI_READY },
    );
    await checkState(9, models, "still-nine");
    await test.step(
      "Explicit zero persists",
      async () => {
        await field.fill("0");
        // The rejection toast can cover the button; keyboard activation is still available.
        await save("Save Changes", 0, models, 200, true);
      },
      { timeout: UI_READY },
    );
    await checkState(0, models, "explicit-zero");
  } finally {
    if ((await readPG()).length) {
      const result = await actor.api.request(
        "post",
        "/agentcc/gateways/default/remove-provider/",
        { data: { name } },
      );
      if (result.status !== 200)
        await probe.pg(
          `UPDATE ${table} SET deleted = true, deleted_at = now() WHERE ${where}`,
          scope,
        );
    }
    await reload();
    expect(await readPG()).toEqual([]);
    expect(await tenant()).toBeNull();
  }
});
