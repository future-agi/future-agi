import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  createAuthenticatedContext,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const { test, expect } = require("@playwright/test");

const APP_BASE = process.env.APP_BASE || "http://localhost:3032";

test.describe("Gateway browser deep smoke", () => {
  test("loads primary gateway routes and round-trips custom properties, webhooks, and sessions", async ({
    browser,
  }) => {
    test.setTimeout(180000);

    const auth = await createAuthenticatedContext();
    const context = await browser.newContext();
    await context.addInitScript(
      ({ tokens, organizationId, workspaceId, user }) => {
        localStorage.setItem("accessToken", tokens.access);
        localStorage.setItem("refreshToken", tokens.refresh || "");
        localStorage.setItem("rememberMe", "true");
        localStorage.setItem("initial-render", "done");
        if (organizationId) sessionStorage.setItem("organizationId", organizationId);
        if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
        if (user?.id) sessionStorage.setItem("futureagi-current-user-id", user.id);
      },
      {
        tokens: auth.tokens,
        organizationId: auth.organizationId,
        workspaceId: auth.workspaceId,
        user: auth.user,
      },
    );

    const page = await context.newPage();
    const apiFailures = [];
    const pageErrors = [];
    page.on("response", (response) => {
      const url = response.url();
      if (url.includes("/agentcc/") && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
    const propertyName = `browser_smoke_property_${suffix}`;
    const webhookName = `browser_smoke_webhook_${suffix}`;
    const sessionId = `browser_smoke_session_${suffix}`;
    let createdPropertyId = null;
    let createdWebhookId = null;
    let createdSessionUuid = null;

    async function cleanupDisposableGatewayData() {
      const [properties, webhooks, sessions] = await Promise.all([
        auth.client.get(apiPath("/agentcc/custom-properties/")),
        auth.client.get(apiPath("/agentcc/webhooks/")),
        auth.client.get(apiPath("/agentcc/sessions/"), { query: { limit: 100 } }),
      ]);
      for (const property of asArray(properties)) {
        if (String(property?.name || "").startsWith("browser_smoke_property_")) {
          await auth.client.delete(
            apiPath("/agentcc/custom-properties/{id}/", { id: property.id }),
            { okStatuses: [200, 204, 404] },
          );
        }
      }
      for (const webhook of asArray(webhooks)) {
        if (String(webhook?.name || "").startsWith("browser_smoke_webhook_")) {
          await auth.client.delete(apiPath("/agentcc/webhooks/{id}/", { id: webhook.id }), {
            okStatuses: [200, 204, 404],
          });
        }
      }
      for (const session of asArray(sessions)) {
        if (String(session?.session_id || "").startsWith("browser_smoke_session_")) {
          await auth.client.delete(apiPath("/agentcc/sessions/{id}/", { id: session.id }), {
            okStatuses: [200, 204, 404],
          });
        }
      }
    }

    async function waitForGatewayPage(path, heading) {
      await page.goto(`${APP_BASE}${path}`, { waitUntil: "domcontentloaded" });
      await expect(page.getByRole("heading", { name: heading }).first()).toBeVisible({
        timeout: 45000,
      });
    }

    async function findTableRow(text) {
      await expect(page.getByText(text).first()).toBeVisible({ timeout: 15000 });
      return page.locator("tr", { hasText: text }).first();
    }

    await cleanupDisposableGatewayData();

    try {
      const gateways = asArray(await auth.client.get(apiPath("/agentcc/gateways/")));
      const gatewayName = gateways[0]?.name || "Gateway";
      const routeChecks = [
        ["/dashboard/gateway", gatewayName],
        ["/dashboard/gateway/providers", "Providers"],
        ["/dashboard/gateway/providers/config", "Providers"],
        ["/dashboard/gateway/logs", "Request Logs"],
        ["/dashboard/gateway/analytics", "Analytics"],
        ["/dashboard/gateway/webhooks", "Webhooks"],
        ["/dashboard/gateway/webhooks/delivery", "Webhooks"],
        ["/dashboard/gateway/sessions", "Sessions"],
        ["/dashboard/gateway/custom-properties", "Custom Properties"],
        ["/dashboard/gateway/fallbacks", "Fallbacks & Reliability"],
        ["/dashboard/gateway/mcp", "MCP Tools"],
        ["/dashboard/gateway/settings", "Settings"],
        ["/dashboard/gateway/guardrails", "Guardrails"],
        ["/dashboard/gateway/budgets", "Budgets"],
        ["/dashboard/gateway/monitoring", "Monitoring"],
      ];
      for (const [path, heading] of routeChecks) {
        await waitForGatewayPage(path, heading);
      }

      await waitForGatewayPage("/dashboard/gateway/logs", "Request Logs");
      await page.getByRole("button", { name: "Filters" }).click();
      const filterDrawer = page.locator(".MuiDrawer-paper", { hasText: "Filters" }).last();
      await expect(filterDrawer).toBeVisible();
      await filterDrawer.getByLabel("Min", { exact: true }).first().fill("400");
      await filterDrawer.getByLabel("Max", { exact: true }).first().fill("499");
      await filterDrawer.getByRole("button", { name: "Apply" }).click();
      await expect(page).toHaveURL(/min_status_code=400/);
      await expect(page).toHaveURL(/max_status_code=499/);
      await page.screenshot({
        path: "/tmp/gateway-logs-filters-smoke.png",
        fullPage: true,
      });
      await page.getByRole("button", { name: "Filters" }).click();
      const clearDrawer = page.locator(".MuiDrawer-paper", { hasText: "Filters" }).last();
      await clearDrawer.getByRole("button", { name: "Clear All" }).click();
      await clearDrawer.getByRole("button", { name: "Apply" }).click();

      const sampleRequestLogs = asArray(
        await auth.client.get(apiPath("/agentcc/request-logs/"), {
          query: { limit: 1 },
        }),
      );
      if (sampleRequestLogs.length > 0) {
        const sampleLog = sampleRequestLogs[0];
        const searchTerm = sampleLog.request_id || sampleLog.model || sampleLog.provider;
        await page.getByPlaceholder("Search model, provider, request ID...").fill(searchTerm);
        await expect(page.getByText(sampleLog.model || sampleLog.provider).first()).toBeVisible({
          timeout: 15000,
        });
        const requestLogRow = page.locator("tr", {
          hasText: sampleLog.model || sampleLog.provider,
        }).first();
        await requestLogRow.click();
        await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible({
          timeout: 15000,
        });
        await expect(page.getByRole("tab", { name: "Request" })).toBeVisible();
        await expect(page.getByRole("tab", { name: "Response" })).toBeVisible();
        await expect(page.getByText(`${sampleLog.latency_ms}ms`).first()).toBeVisible();
        await page.getByRole("tab", { name: "Request" }).click();
        await expect(page.getByText("Request Body")).toBeVisible();
        await page.screenshot({
          path: "/tmp/gateway-request-log-detail-smoke.png",
          fullPage: true,
        });
        await page.keyboard.press("Escape");
        await expect(page.getByRole("tab", { name: "Overview" })).toBeHidden({
          timeout: 15000,
        });
      }

      await page.getByRole("tab", { name: "Sessions" }).click();
      const sortedSessionsResponse = page.waitForResponse(
        (response) =>
          response.url().includes("/agentcc/request-logs/sessions/") &&
          response.url().includes("ordering=-request_count") &&
          response.status() < 400,
      );
      await page.getByRole("button", { name: "Most Requests" }).click();
      await sortedSessionsResponse;

      await waitForGatewayPage("/dashboard/gateway/custom-properties", "Custom Properties");
      await page.getByRole("button", { name: "Add Property" }).click();
      await expect(page.getByRole("heading", { name: "Create Property Schema" })).toBeVisible();
      await page.getByLabel("Property Name").fill(propertyName);
      await page.getByLabel("Description").fill("Browser smoke enum property");
      await page.locator('[role="combobox"]').filter({ hasText: "String" }).click();
      await page.getByRole("option", { name: "Enum" }).click();
      await page.getByPlaceholder("Add enum value").fill("alpha");
      await page.getByRole("button", { name: "Add" }).click();
      await page.getByPlaceholder("Add enum value").fill("beta");
      await page.getByRole("button", { name: "Add" }).click();
      await page.getByLabel("Default Value (optional)").fill("alpha");
      await page.getByRole("button", { name: "Create" }).click();
      await expect(page.getByText("Property schema created")).toBeVisible({ timeout: 15000 });
      const propertiesAfterCreate = asArray(
        await auth.client.get(apiPath("/agentcc/custom-properties/")),
      );
      createdPropertyId = propertiesAfterCreate.find((row) => row.name === propertyName)?.id;
      expect(createdPropertyId).toBeTruthy();
      await page.getByPlaceholder("Search properties...").fill(propertyName);
      const propertyRow = await findTableRow(propertyName);
      await expect(propertyRow.getByText("enum", { exact: true })).toBeVisible();
      await expect(propertyRow.getByText("alpha").first()).toBeVisible();
      await expect(propertyRow.getByText("beta")).toBeVisible();
      await page.screenshot({
        path: "/tmp/gateway-custom-property-smoke.png",
        fullPage: true,
      });

      await propertyRow.locator('button[title="Edit"]').click();
      await expect(page.getByRole("heading", { name: "Edit Property Schema" })).toBeVisible();
      await page.getByLabel("Description").fill("Browser smoke enum property updated");
      await page.getByRole("button", { name: "Update" }).click();
      await expect(page.getByText("Property schema updated")).toBeVisible({ timeout: 15000 });
      const updatedProperty = await auth.client.get(
        apiPath("/agentcc/custom-properties/{id}/", { id: createdPropertyId }),
      );
      expect(updatedProperty.description).toBe("Browser smoke enum property updated");
      const updatedPropertyRow = await findTableRow(propertyName);
      await updatedPropertyRow.locator('button[title="Delete"]').click();
      await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
      await expect(page.getByText("Property schema deleted")).toBeVisible({ timeout: 15000 });
      createdPropertyId = null;

      await waitForGatewayPage("/dashboard/gateway/webhooks", "Webhooks");
      await page.getByRole("button", { name: "Create Webhook" }).click();
      await expect(page.getByRole("heading", { name: "Create Webhook" })).toBeVisible();
      await page.getByLabel("Name").fill(webhookName);
      await page.getByLabel("URL").fill("https://example.com/futureagi-browser-smoke");
      await page.getByLabel("Description").fill("Browser smoke webhook");
      await page.getByLabel("Request Completed").check();
      await page.getByRole("button", { name: "Create" }).click();
      await expect(page.getByText("Webhook created")).toBeVisible({ timeout: 15000 });
      const webhooksAfterCreate = asArray(await auth.client.get(apiPath("/agentcc/webhooks/")));
      createdWebhookId = webhooksAfterCreate.find((row) => row.name === webhookName)?.id;
      expect(createdWebhookId).toBeTruthy();
      await page.getByPlaceholder("Search by name or URL...").fill(webhookName);
      const webhookRow = await findTableRow(webhookName);
      await expect(webhookRow.getByText("https://example.com/futureagi-browser-smoke")).toBeVisible();
      await expect(webhookRow.getByText("request.completed")).toBeVisible();
      await page.screenshot({
        path: "/tmp/gateway-webhooks-smoke.png",
        fullPage: true,
      });

      await webhookRow.getByRole("button", { name: "Edit" }).click();
      await expect(page.getByRole("heading", { name: "Edit Webhook" })).toBeVisible();
      await page.getByLabel("Description").fill("Browser smoke webhook updated");
      await page.getByRole("button", { name: "Update" }).click();
      await expect(page.getByText("Webhook updated")).toBeVisible({ timeout: 15000 });
      await expect
        .poll(
          async () => {
            const updatedWebhook = await auth.client.get(
              apiPath("/agentcc/webhooks/{id}/", { id: createdWebhookId }),
            );
            return updatedWebhook.description;
          },
          { timeout: 10000 },
        )
        .toBe("Browser smoke webhook updated");
      const updatedWebhookRow = await findTableRow(webhookName);
      await updatedWebhookRow.getByRole("button", { name: "View events" }).click();
      await expect(page.getByRole("tab", { name: "Delivery Log" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await waitForGatewayPage("/dashboard/gateway/webhooks", "Webhooks");
      await page.getByPlaceholder("Search by name or URL...").fill(webhookName);
      const rowForDelete = await findTableRow(webhookName);
      await rowForDelete.getByRole("button", { name: "Delete" }).click();
      await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
      await expect(page.getByText("Webhook deleted")).toBeVisible({ timeout: 15000 });
      createdWebhookId = null;

      const createdSession = await auth.client.post(apiPath("/agentcc/sessions/"), {
        session_id: sessionId,
        name: "Browser smoke session",
        status: "active",
        metadata: { source: "browser-smoke" },
      });
      createdSessionUuid = createdSession.id;
      await waitForGatewayPage("/dashboard/gateway/sessions", "Sessions");
      await page.getByPlaceholder("Search by session ID or name...").fill(sessionId);
      const sessionRow = await findTableRow("Browser smoke session");
      await expect(sessionRow.getByText("Browser smoke session")).toBeVisible();
      await expect(sessionRow.getByText("active")).toBeVisible();
      await page.screenshot({
        path: "/tmp/gateway-sessions-smoke.png",
        fullPage: true,
      });
      await sessionRow.click();
      await expect(page.getByText("Session Detail")).toBeVisible({ timeout: 15000 });
      await expect(page.getByText("ID: " + sessionId)).toBeVisible();
      await expect(page.getByText("No requests in this session")).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(page.getByText("Session Detail")).toBeHidden({ timeout: 15000 });
      const sessionRowAfterDrawer = await findTableRow("Browser smoke session");
      await sessionRowAfterDrawer.locator('button[title="Close session"]').click();
      await expect(page.getByText("Session closed")).toBeVisible({ timeout: 15000 });
      await expect
        .poll(
          async () => {
            const detail = await auth.client.get(
              apiPath("/agentcc/sessions/{id}/", { id: createdSessionUuid }),
            );
            return detail.status;
          },
          { timeout: 10000 },
        )
        .toBe("closed");
      await auth.client.delete(
        apiPath("/agentcc/sessions/{id}/", { id: createdSessionUuid }),
        { okStatuses: [200, 204, 404] },
      );
      createdSessionUuid = null;

      expect(apiFailures).toEqual([]);
      expect(pageErrors).toEqual([]);
    } finally {
      if (createdPropertyId) {
        await auth.client.delete(
          apiPath("/agentcc/custom-properties/{id}/", { id: createdPropertyId }),
          { okStatuses: [200, 204, 404] },
        );
      }
      if (createdWebhookId) {
        await auth.client.delete(apiPath("/agentcc/webhooks/{id}/", { id: createdWebhookId }), {
          okStatuses: [200, 204, 404],
        });
      }
      if (createdSessionUuid) {
        await auth.client.delete(
          apiPath("/agentcc/sessions/{id}/", { id: createdSessionUuid }),
          { okStatuses: [200, 204, 404] },
        );
      }
      await cleanupDisposableGatewayData();
      await context.close();
    }
  });
});
