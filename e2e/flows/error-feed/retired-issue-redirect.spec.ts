import { test, expect } from "../../lib/fixtures";
import { flowAnnotation } from "../../lib/flow-meta";

// The isolated E2E stack has no Enterprise license. Stub only the entitlement
// and redirect responses here; database resolution and tenant scoping are
// exercised by tracer/tests/test_grouping_sampled_merges.py.
test(
  "FEED-E2E-001: an old issue URL opens the surviving issue",
  {
    tag: ["@flow"],
    annotation: flowAnnotation({
      id: "FEED-E2E-001",
      area: "error-feed",
      userGoal: "A saved link to a retired issue opens its surviving issue",
      steps: [
        "open the retired issue URL",
        "follow its redirect to the active issue URL",
      ],
      browserChecks: [
        "the browser requests the redirect for the retired ID and receives a stubbed response",
        "the browser never requests the retired issue detail",
      ],
    }),
  },
  async ({ page }) => {
    const oldId = "e2e-retired-issue";
    const activeId = "e2e-active-issue";
    const redirectRequests: string[] = [];
    const oldDetailRequests: string[] = [];

    await page.route("**/api/capabilities/", (route) =>
      route.fulfill({
        json: { features: { error_feed: { allowed: true } } },
      }),
    );
    await page.route("**/tracer/feed/issues/*/redirect/**", (route) => {
      const id = new URL(route.request().url()).pathname.split("/").at(-3);
      redirectRequests.push(id ?? "");
      return route.fulfill({
        json: {
          result: {
            requested_cluster_id: id,
            resolved_cluster_id: id === oldId ? activeId : id,
          },
        },
      });
    });
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.endsWith(`/feed/issues/${oldId}/`)) {
        oldDetailRequests.push(request.url());
      }
    });

    await page.goto(`/dashboard/error-feed/${oldId}`);
    await expect(page).toHaveURL(
      new RegExp(`/dashboard/error-feed/${activeId}$`),
    );
    expect(redirectRequests).toContain(oldId);
    expect(oldDetailRequests).toEqual([]);
  },
);
