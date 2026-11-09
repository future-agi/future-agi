import { request } from "@playwright/test";
import { test, expect } from "../../../lib/scope-actors";
import { E2E } from "../../../lib/env";
import { RevisionAgent } from "../../../lib/mcp-revision-agent";
import { POLL } from "../../../lib/state-probe";
import { flowAnnotation } from "../../../lib/flow-meta";
import { profileTag } from "../../../lib/profile";
import { MCP_MOCK } from "../../../lib/public-network";
import { GatewayAdmin } from "../../../lib/gateway-admin";
import { assertRevisionUiAbsent } from "../../../journeys/gateway/J14/revision-absence";

// W09 steps 5-6, G13b: AUTHORING ONLY. Run in both Chromium and Firefox on
// the guarded mcp-signin walk stack, never against a provider or production.
// OFF is the checked-in HIDDEN_MCP_TABS.trust entry. The ON walk requires an
// owner-enabled product build (that entry removed through release review)
// AND E2E_MCP_REVISIONS_ON=true. This selector does not enable product code.
// The default OFF walk below still asserts absence, including deep links.
// NOT a walk-harness receipt: before acceptance the walk runner must port this
// flow to walk.open / walk.receipt / walk.boundary, using its Admin roster and
// OAuth-authenticated agent, and run serially in both browsers. The test-only
// /mcp-next/__received route observes the actual next-release mock instance.
const ON = process.env.E2E_MCP_REVISIONS_ON === "true";
const PATH = "/dashboard/gateway/mcp?tab=trust";
const REVISIONS = "/agentcc/mcp/revisions/";
const CONFIG = "/agentcc/gateways/default/config/";
const UPDATE_CONFIG = "/agentcc/gateways/default/update-config/";
const UPDATE_SERVER = "/agentcc/gateways/default/update-mcp-server/";
const NEXT_URL = MCP_MOCK.url.replace(/\/mcp$/, "/mcp-next");
const BEFORE = "Returns its arguments as text.";
const AFTER =
  "Returns its arguments as text, and keeps a copy of everything it is sent.";
const UI_READY = 60_000;
// Existing 780s budget plus five agent-observation polls (5*75) and 45s for
// the added Admin invitation and mock receipts = 1200s. No per-check timeout
// is widened; only the authored flow now covers these additional boundaries.
const FLOW_TIMEOUT = 1_200_000;
interface Envelope<T> {
  result: T;
}
interface Revision {
  id: number;
  server_id: string;
  tool: string;
  revision: string;
  status: string;
}
interface Config {
  mcp?: {
    pinning?: { pins?: Record<string, string>; auto_baseline?: boolean };
  };
}

test(
  "GW-E2E-035: W09 keeps tool revision review absent with the release switch OFF",
  {
    tag: ["@flow", profileTag("mcp-signin")],
    annotation: flowAnnotation({
      id: "GW-E2E-035",
      area: "gateway",
      userGoal:
        "The release does not advertise or fetch an unapproved tool-revision screen",
      steps: [
        "open the MCP page with a tool-review deep link on the OFF build",
        "verify the link returns to Overview and no review requests occur",
        "repeat the exact served-tab and hidden-link checks through the J14 helper; Resources/Prompts stay disabled and Guardrails remains enabled",
      ],
      backendChecks: [
        "no MCP revision API request is sent by the hidden screen",
        "the J14 helper sees a successful MCP status and zero attempted Resources/Prompts list requests while preserving the trust redirect",
      ],
    }),
  },
  async ({ scopeActors: scopes, browser }, testInfo) => {
    test.skip(ON, "OFF-build assertion; the explicit ON walk is GW-E2E-036");
    // Original 240s plus J14's two page-load steps, 2 x (UI_READY + 15s).
    test.setTimeout(390_000);
    const context = await scopes.openContext(browser, scopes.ownerA);
    const page = await context.newPage();
    const requests: string[] = [];
    page.on("request", (req) => {
      if (new URL(req.url()).pathname.startsWith(REVISIONS))
        requests.push(req.method());
    });
    try {
      await page.goto(PATH);
      await expect(
        page.getByRole("tab", { name: "Overview", exact: true }),
      ).toBeVisible({ timeout: UI_READY });
      await expect(page).not.toHaveURL(/[?&]tab=trust/);
      await expect(
        page.getByRole("tab", { name: /^(Trust|Tool revisions)$/ }),
      ).toHaveCount(0);
      await expect(
        page.getByRole("heading", { name: "Tool revisions", exact: true }),
      ).toHaveCount(0);
      expect(requests).toEqual([]);
      // Exercise the updated helper in a real existing flow, not just an unused
      // tab array. This still does not qualify the broader J14 journey packet.
      await assertRevisionUiAbsent(page, testInfo);
      expect(requests).toEqual([]);
    } finally {
      await context.close();
    }
  },
);

test(
  "GW-E2E-036: W09 steps 5-6 review, approve, reject and manage tool pins on an explicitly ON build",
  {
    tag: ["@flow", profileTag("mcp-signin")],
    annotation: flowAnnotation({
      id: "GW-E2E-036",
      area: "gateway",
      userGoal:
        "An Admin reviews held tool changes, approves echo and rejects lookup_order, and verifies what agents can list and call before and after each pin decision",
      steps: [
        "seed one mock MCP server, a virtual server, a person-owned gateway key and the accepted baseline; Admin moves the server to the mock next release",
        "W09 step 5: agents cannot list echo or lookup_order and calls return tool_quarantined without reaching the next-release mock; an unchanged tool still works",
        "W09 step 6: Admin reads description/schema differences and approves echo and rejects lookup_order in the UI; Member approval gets 403; agents can list and call only the approved tool",
        "unpin echo and re-pin its recorded accepted definition through the UI; confirm other pins are unchanged",
        "open the same screen as another organization and verify no server revisions or pins leak",
      ],
      backendChecks: [
        "the baseline has exactly three server pins and offered tools; the next release has pending echo and lookup_order",
        "held echo and lookup_order are absent from tools/list and tools/call returns tool_quarantined with changed/new reasons; neither call reaches the actual next-release mock",
        "approve and reject requests are empty-body POSTs from the Admin UI to the selected revision IDs",
        "approved echo is offered with its new description and answered upstream; rejected lookup_order remains absent and refused; unpin and re-pin also change agent-visible state",
        "the gateway applies the exact approved echo hash; the rejected new tool is not pinned",
        "unpin deletes only echo, re-pin restores its accepted hash, and the gateway receives both config versions",
        "a Member approval returns 403 and another organization receives no revisions for the seeded server",
      ],
    }),
  },
  async ({ scopeActors: scopes, browser }, testInfo) => {
    test.skip(
      !ON,
      "Requires the owner-enabled G13b build and E2E_MCP_REVISIONS_ON=true; not launch acceptance",
    );
    test.setTimeout(FLOW_TIMEOUT);
    const reviewer = await scopes.provisionAdmin();
    const serverId = `e2e-gw36-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const pinKey = `${serverId}/echo`;
    const context = await scopes.openContext(browser, reviewer);
    const page = await context.newPage();
    const admin = await GatewayAdmin.open();
    expect(new URL(E2E.gatewayUrl).hostname).toBe("localhost");
    const req = await request.newContext({ maxRedirects: 0 });
    const marker = `${serverId}-echo`;
    const echoArgs = { text: marker };
    const heldLookup = { tool: "lookup_order", reason: "new" as const, arguments: { order: serverId } };
    const list = async (status = "pending") =>
      (
        await reviewer.api.get<Envelope<{ revisions: Revision[] }>>(REVISIONS, {
          status,
        })
      ).result.revisions.filter((row) => row.server_id === serverId);
    const pins = async () =>
      (await reviewer.api.get<Envelope<Config>>(CONFIG)).result.mcp?.pinning
        ?.pins ?? {};
    const saveServer = (url: string) =>
      reviewer.api.post(UPDATE_SERVER, {
        server_id: serverId,
        config: {
          transport: "http",
          url,
          auth: { type: "bearer", token: MCP_MOCK.bearer },
        },
      });
    try {
      await saveServer(MCP_MOCK.url);
      await reviewer.api.post(UPDATE_CONFIG, { mcp: { enabled: true,
        virtual_servers: { [serverId]: { title: serverId, members: [serverId], naming: "prefixed" } },
      } });
      const key = await reviewer.api.post<Envelope<{ key: string }>>(
        "/agentcc/api-keys/", { name: serverId },
      );
      const agent = new RevisionAgent(req, context.request, `/mcp/v/${serverId}`, key.result.key, serverId);
      await agent.offered(["delete_file", "echo", "get_weather"]);
      await expect
        .poll(async () => {
          await list();
          return Object.keys(await pins())
            .filter((key) => key.startsWith(`${serverId}/`))
            .sort();
        }, POLL.GATEWAY_SYNC)
        .toEqual(
          ["delete_file", "echo", "get_weather"].map(
            (tool) => `${serverId}/${tool}`,
          ),
        );
      const baseline = await pins();
      await expect
        .poll(
          async () =>
            (await admin.orgConfig(reviewer.organizationId))?.mcp?.pinning?.pins?.[
              pinKey
            ],
          POLL.GATEWAY_SYNC,
        )
        .toBe(baseline[pinKey]);
      await saveServer(NEXT_URL);
      await expect
        .poll(
          async () => (await list()).map((row) => row.tool).sort(),
          POLL.GATEWAY_SYNC,
        )
        .toEqual(["echo", "lookup_order"]);
      const pending = await list();
      const echo = pending.find((row) => row.tool === "echo")!;
      const lookup = pending.find((row) => row.tool === "lookup_order")!;

      await test.step("W09 step 5: held tools cannot be listed or called upstream", async () => {
        await agent.offered(["delete_file", "get_weather"]);
        // Positive control: prove the next-release journal observes real traffic.
        await agent.answered("get_weather", { city: serverId }, "Sunny, 21 C");
        await agent.held([
          { tool: "echo", reason: "changed", arguments: echoArgs }, heldLookup,
        ]);
      });

      await test.step("W09 step 6: Admin reviews the exact changes; Member cannot approve", async () => {
        await page.goto(PATH);
        await expect(
          page.getByRole("tab", { name: "Tool revisions", exact: true }),
        ).toBeVisible({ timeout: UI_READY });
        const changed = page.getByRole("article", {
          name: `${serverId} / echo revision`,
          exact: true,
        });
        await expect(changed.getByText(BEFORE, { exact: true })).toBeVisible({
          timeout: UI_READY,
        });
        await expect(changed.getByText(AFTER, { exact: true })).toBeVisible();
        const added = page.getByRole("article", {
          name: `${serverId} / lookup_order revision`,
          exact: true,
        });
        await expect(
          added.getByText("inputSchema", { exact: true }),
        ).toBeVisible();
        const memberContext = await scopes.openContext(browser, scopes.member);
        const memberPage = await memberContext.newPage();
        await memberPage.goto(PATH);
        const memberRow = memberPage.getByRole("article", {
          name: `${serverId} / echo revision`,
          exact: true,
        });
        await expect(
          memberRow.getByRole("button", { name: "Approve", exact: true }),
        ).toBeDisabled({ timeout: UI_READY });
        expect(
          (
            await scopes.send(
              scopes.member,
              "POST",
              `${REVISIONS}${echo.id}/approve/`,
              {},
            )
          ).status,
        ).toBe(403);
        await memberContext.close();
      });

      await test.step("W09 step 6: approve and reject from the reviewed rows", async () => {
        for (const [row, action] of [
          [echo, "approve"],
          [lookup, "reject"],
        ] as const) {
          const label = action === "approve" ? "Approve" : "Reject";
          await page
            .getByRole("article", {
              name: `${serverId} / ${row.tool} revision`,
              exact: true,
            })
            .getByRole("button", { name: label, exact: true })
            .click();
          const decision = page.waitForResponse(
            (res) =>
              new URL(res.url()).pathname ===
                `${REVISIONS}${row.id}/${action}/` &&
              res.request().method() === "POST",
          );
          await page
            .getByRole("dialog")
            .getByRole("button", { name: label, exact: true })
            .click();
          const response = await decision;
          expect(response.status()).toBe(200);
          expect(response.request().postDataJSON()).toEqual({});
          expect((await response.json()).result.status).toBe(
            action === "approve" ? "approved" : "rejected",
          );
          await expect(page.getByRole("dialog")).toHaveCount(0);
          await expect(
            page.getByRole("button", { name: "Refresh", exact: true }),
          ).toBeEnabled();
          const descriptions = await agent.offered(["delete_file", "echo", "get_weather"]);
          expect(descriptions[`${serverId}__echo`]).toBe(AFTER);
          if (action === "approve")
            await agent.answered("echo", echoArgs, `echo ok: ${JSON.stringify(echoArgs)}`);
          // After BOTH approval and rejection, the new tool stays held.
          await agent.held([heldLookup]);
        }
        await expect(
          page.getByText("No pending tool revisions.", { exact: true }),
        ).toBeVisible();
        await expect
          .poll(
            async () =>
              (await admin.orgConfig(reviewer.organizationId))?.mcp?.pinning
                ?.pins?.[pinKey],
            POLL.GATEWAY_SYNC,
          )
          .toBe(echo.revision);
        expect((await pins())[`${serverId}/lookup_order`]).toBeUndefined();
        expect((await list("rejected")).map((row) => row.tool)).toEqual([
          "lookup_order",
        ]);
      });

      await test.step("unpin and re-pin the accepted definition without touching sibling pins", async () => {
        await page
          .getByRole("article", { name: `${pinKey} pin`, exact: true })
          .getByRole("button", { name: "Unpin", exact: true })
          .click();
        await page
          .getByRole("dialog")
          .getByRole("button", { name: "Unpin", exact: true })
          .click();
        await expect(
          page.getByRole("article", { name: `${pinKey} pin`, exact: true }),
        ).toHaveCount(0);
        await expect
          .poll(
            async () =>
              (await admin.orgConfig(reviewer.organizationId))?.mcp?.pinning
                ?.pins?.[pinKey] ?? null,
            POLL.GATEWAY_SYNC,
          )
          .toBeNull();
        await agent.offered(["delete_file", "get_weather"]);
        await agent.held([
          { tool: "echo", reason: "new", arguments: echoArgs }, heldLookup,
        ]);
        const accepted = page.getByRole("article", {
          name: `${serverId} / echo accepted definition`,
          exact: true,
        });
        await accepted
          .getByText("Accepted definition", { exact: true })
          .click();
        await expect(
          accepted.getByText(echo.revision, { exact: false }),
        ).toBeVisible();
        await accepted
          .getByRole("button", { name: "Pin", exact: true })
          .click();
        await page
          .getByRole("dialog")
          .getByRole("button", { name: "Pin", exact: true })
          .click();
        await expect
          .poll(
            async () =>
              (await admin.orgConfig(reviewer.organizationId))?.mcp?.pinning
                ?.pins?.[pinKey],
            POLL.GATEWAY_SYNC,
          )
          .toBe(echo.revision);
        const descriptions = await agent.offered(["delete_file", "echo", "get_weather"]);
        expect(descriptions[`${serverId}__echo`]).toBe(AFTER);
        await agent.answered("echo", echoArgs, `echo ok: ${JSON.stringify(echoArgs)}`);
        await agent.held([heldLookup]);
        const finalPins = await pins();
        for (const tool of ["delete_file", "get_weather"])
          expect(finalPins[`${serverId}/${tool}`]).toBe(
            baseline[`${serverId}/${tool}`],
          );
      });

      const otherContext = await scopes.openContext(browser, scopes.ownerB);
      const otherPage = await otherContext.newPage();
      await otherPage.goto(PATH);
      await expect(
        otherPage.getByText("No pending tool revisions.", { exact: true }),
      ).toBeVisible({ timeout: UI_READY });
      await expect(otherPage.getByText(serverId, { exact: false })).toHaveCount(
        0,
      );
      const otherRows =
        await scopes.ownerB.api.get<Envelope<{ revisions: Revision[] }>>(
          REVISIONS,
        );
      expect(
        otherRows.result.revisions.filter((row) => row.server_id === serverId),
      ).toEqual([]);
      await testInfo.attach("w09-revision-ui", {
        contentType: "application/json",
        body: JSON.stringify({
          serverId,
          echoRevisionId: echo.id,
          rejectedRevisionId: lookup.id,
          browser: browser.browserType().name(),
          productAccepted: false,
        }),
      });
    } finally {
      await req.dispose();
      await admin.dispose();
    }
  },
);
