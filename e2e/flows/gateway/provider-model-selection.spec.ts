import type { Locator, Page, Request, Route } from "@playwright/test";
import { test, expect } from "../../lib/fixtures";
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
      ],
      backendChecks: [
        "the Add save posts config.models exactly [listedA, typedOne] to update-provider (typed ID trimmed, hovered listedB absent) and gets 200",
        "the gateway config API returns the added provider with models exactly [listedA, typedOne]",
        "the Edit direct save posts and persists models exactly [listedA, typedOne, typedTwo]: update-provider body and gateway config API agree",
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
