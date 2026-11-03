import { test as base, expect, type Route } from "@playwright/test";
import { flowAnnotation } from "../../lib/flow-meta";
import {
  SHARED_ISSUE,
  SIDEBAR_FOR_TRACE_B,
  TRACE_A,
  TRACE_A_DETAIL,
  TRACE_B,
} from "./shared-issue.payload";

// Deliberately NOT using lib/fixtures: a shared link is opened by someone who
// is not signed in, so the flow starts from a clean, unauthenticated context.
//
// The isolated E2E stack has no Enterprise licence and no Error Feed seeder,
// so the three share endpoints are served from captured responses (see
// shared-issue.payload.ts). Resolving, access rules and the licence gate are
// covered by futureagi/tracer/tests/test_shared_link_resolve.py.

const UI_READY = 60_000;
// A toast from the global query error handler lands within a moment of a
// failed read; long enough to have seen one if it was coming.
const TOAST_SETTLE = 2_000;

// Pinned from frontend/src/pages/shared/SharedView.jsx (error copy) and
// frontend/src/pages/dashboard/error-feed/ErrorFeedDetailView.jsx (title).
const ISSUE_TITLE = SHARED_ISSUE.issue.row.error.name;

base(
  "ERR-E2E-005: a shared issue opens read-only for someone who is not signed in",
  {
    tag: ["@flow"],
    annotation: flowAnnotation({
      id: "ERR-E2E-005",
      area: "error-feed",
      userGoal:
        "Someone who is not signed in opens a shared Error Feed issue and reads it without being able to change it",
      steps: [
        "open the issue's public share link while signed out",
        "read the issue on Overview",
        "pick another trace in Traces affected",
        "open a trace from the Traces tab",
        "open the Trends and Fix tabs",
        "open a restricted share link while signed out",
      ],
      browserChecks: [
        "the page shows the issue title and View only, with no Resolve, Acknowledge, Ignore, Share, Debug or Create Linear issue action",
        "the browser never calls the signed-in Error Feed endpoints (/tracer/feed/issues/)",
        "picking a trace reads the sidebar through the share token for exactly that trace",
        "opening a trace reads it through the share token and shows its spans",
        "a restricted link shows Sign in required, with no error toast, and stays on the shared URL",
      ],
    }),
  },
  async ({ page }, testInfo) => {
    // navigation (2 × 60 s) + seven UI_READY waits (7 × 60 s) = 540 s.
    base.setTimeout(540_000);

    const minted = `e2e-share-w${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const publicToken = `${minted}-public`;
    const restrictedToken = `${minted}-restricted`;
    await testInfo.attach("share-tokens", {
      body: JSON.stringify({ publicToken, restrictedToken }, null, 2),
      contentType: "application/json",
    });

    // Record every toast as it appears: they hide themselves, so a check
    // after the fact could miss one.
    await page.addInitScript(() => {
      const seen: string[] = [];
      (window as unknown as { __toasts: string[] }).__toasts = seen;
      new MutationObserver(() => {
        document.querySelectorAll(".notistack-MuiContent").forEach((el) => {
          if (!el.hasAttribute("data-e2e-seen")) {
            el.setAttribute("data-e2e-seen", "");
            seen.push(el.textContent ?? "");
          }
        });
      }).observe(document, { childList: true, subtree: true });
    });

    const feedRequests: string[] = [];
    const traceRequests: string[] = [];
    const sidebarRequests: (string | null)[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.includes("/tracer/feed/issues/")) {
        feedRequests.push(request.url());
      }
    });

    const shared = (token: string) => `/tracer/shared/${token}/`;
    await page.route(
      (url) => url.pathname.includes(shared(publicToken)),
      (route: Route) => {
        const url = new URL(route.request().url());
        const rest = url.pathname.split(shared(publicToken))[1];
        if (rest === "") {
          return route.fulfill({
            json: {
              resource_type: "error_feed_issue",
              resource_id: SHARED_ISSUE.issue.row.cluster_id,
              access_type: "public",
              data: SHARED_ISSUE,
            },
          });
        }
        if (rest.startsWith("traces/")) {
          traceRequests.push(rest.split("/")[1]);
          return route.fulfill({
            json: { status: true, result: TRACE_A_DETAIL },
          });
        }
        if (rest.startsWith("sidebar/")) {
          const traceId = url.searchParams.get("trace_id");
          sidebarRequests.push(traceId);
          return route.fulfill({
            json: {
              status: true,
              result:
                traceId === TRACE_B
                  ? SIDEBAR_FOR_TRACE_B
                  : SHARED_ISSUE.sidebar,
            },
          });
        }
        return route.fulfill({ status: 404, json: { status: false } });
      },
    );
    await page.route(
      (url) => url.pathname.includes(shared(restrictedToken)),
      (route: Route) =>
        route.fulfill({
          status: 401,
          // The real 401 body, captured from GET /tracer/shared/<token>/.
          json: {
            status: false,
            type: "authentication_error",
            code: "not_authenticated",
            detail: "Authentication required to access this link",
            message: "Authentication required to access this link",
            error: "Authentication required to access this link",
            result: "Authentication required to access this link",
          },
        }),
    );

    await base.step(
      "the shared issue opens read-only",
      async () => {
        await page.goto(`/shared/${publicToken}`);
        await expect(page.getByText(ISSUE_TITLE).first()).toBeVisible({
          timeout: UI_READY,
        });
        await expect(page.getByText("View only")).toBeVisible({
          timeout: UI_READY,
        });
        await expect(
          page.getByRole("button", {
            name: /resolve|acknowledge|ignore issue|^share$|debug this cluster|create linear/i,
          }),
        ).toHaveCount(0);
      },
      { timeout: UI_READY * 2 },
    );

    await base.step(
      "the sidebar follows the picked trace through the share token",
      async () => {
        // Before the pick, TRACE_B is listed once (Traces affected); after
        // it, the sidebar's AI metadata names it too.
        const traceB = page.getByText(TRACE_B, { exact: true });
        await expect(traceB).toHaveCount(1, { timeout: UI_READY });
        await traceB.click();
        await expect
          .poll(() => sidebarRequests.at(-1), { timeout: UI_READY })
          .toBe(TRACE_B);
        await expect(traceB).toHaveCount(2, { timeout: UI_READY });
      },
      { timeout: UI_READY },
    );

    await base.step(
      "a trace opens read-only through the share token",
      async () => {
        await page.getByRole("tab", { name: /Traces/ }).click();
        await page
          .locator(".ag-row")
          .filter({ hasText: TRACE_A.slice(0, 8) })
          .click();
        await expect(page.getByText(/Trace ID/).first()).toBeVisible({
          timeout: UI_READY,
        });
        expect(traceRequests).toEqual([TRACE_A]);
        await expect(page.getByText("support_agent").first()).toBeVisible({
          timeout: UI_READY,
        });
      },
      { timeout: UI_READY },
    );

    await base.step(
      "Trends and Fix render from the share",
      async () => {
        await page.keyboard.press("Escape");
        await page.getByRole("tab", { name: /Trends/ }).click();
        await expect(page.getByText("Error rate").first()).toBeVisible({
          timeout: UI_READY,
        });
        await page.getByRole("tab", { name: /Fix/ }).click();
        await expect(
          page.getByText("Showing the last saved analysis"),
        ).toBeVisible({ timeout: UI_READY });
      },
      { timeout: UI_READY * 2 },
    );

    await base.step(
      "nothing on the page called the signed-in Error Feed API",
      async () => {
        expect(feedRequests).toEqual([]);
      },
    );

    await base.step(
      "a restricted link asks the signed-out viewer to sign in",
      async () => {
        await page.goto(`/shared/${restrictedToken}`);
        await expect(
          page.getByRole("heading", { name: "Sign in required" }),
        ).toBeVisible({ timeout: UI_READY });
        expect(new URL(page.url()).pathname).toBe(`/shared/${restrictedToken}`);
        // The page's own screen is the whole answer: no error toast. One from
        // the global query error handler lands within a moment of the 401.
        await page.waitForTimeout(TOAST_SETTLE);
        expect(
          await page.evaluate(
            () => (window as unknown as { __toasts: string[] }).__toasts,
          ),
        ).toEqual([]);
      },
      { timeout: UI_READY * 2 },
    );
  },
);
