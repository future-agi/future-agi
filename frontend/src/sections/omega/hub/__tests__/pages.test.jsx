import React from "react";
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import rules from "../__fixtures__/rules.json";
import review from "../__fixtures__/review-submission.json";
import meMaintainer from "../__fixtures__/me-maintainer.json";
import meUser from "../__fixtures__/me-user.json";
import HubAuditPage from "../HubAuditPage";
import HubBlocklistPage from "../HubBlocklistPage";
import HubPicksPage from "../HubPicksPage";
import HubPublishPage from "../HubPublishPage";
import HubPublishersReviewPage from "../HubPublishersReviewPage";
import HubReportsPage from "../HubReportsPage";
import HubReviewPage from "../HubReviewPage";
import HubReviewQueuePage from "../HubReviewQueuePage";
import DecisionForm from "../components/DecisionForm";
import { calls, respond } from "./mockAxios";
import { randomRules, renderAt, rulesTexts } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

describe("Rule text comes only from GET /rules (SPEC §6.8)", () => {
  it("the Publish page shows the rules file's texts", async () => {
    const random = randomRules(rules);
    respond([
      ["get", "/omega-hub/v1/rules", random],
      ["get", "/omega-hub/v1/me", { ...meUser, publishers: [{ handle: "c-test", displayName: "C Test", tier: "community" }] }],
    ]);
    renderAt(<HubPublishPage />);
    await screen.findByTestId("hub-rules");
    for (const text of rulesTexts(random)) {
      expect(screen.getAllByText(text, { exact: false }).length).toBeGreaterThan(0);
    }
    for (const text of rulesTexts(rules)) expect(document.body).not.toHaveTextContent(text);
  });

  it("the review form shows the rules file's checklist", async () => {
    const random = randomRules(rules);
    respond([["get", "/omega-hub/v1/rules", random]]);
    renderAt(<DecisionForm submission={review} />);
    await screen.findAllByTestId("hub-checklist-item");
    for (const item of random.checklist) expect(screen.getByText(item.text)).toBeInTheDocument();
    for (const item of rules.checklist) expect(document.body).not.toHaveTextContent(item.text);
  });
});

describe("Review routes are for maintainers only (SPEC §7.1)", () => {
  const pages = [
    ["queue", <HubReviewQueuePage key="q" />],
    ["review", <HubReviewPage key="r" />],
    ["reports", <HubReportsPage key="rp" />],
    ["picks", <HubPicksPage key="p" />],
    ["publishers", <HubPublishersReviewPage key="pr" />],
    ["blocklist", <HubBlocklistPage key="b" />],
    ["audit", <HubAuditPage key="a" />],
  ];

  for (const [name, element] of pages) {
    it(`${name}: renders the 403 state and asks for no review data when isMaintainer is false`, async () => {
      respond([["get", "/omega-hub/v1/me", meUser]]);
      renderAt(element, { path: "/dashboard/hub/review/:id?", at: `/dashboard/hub/review/${review.id}` });
      expect(await screen.findByTestId("hub-forbidden")).toHaveTextContent("No permission");
      expect(calls.filter((c) => c.path.includes("/review/"))).toEqual([]);
    });
  }

  it("shows the queue to a maintainer", async () => {
    respond([
      ["get", "/omega-hub/v1/me", meMaintainer],
      ["get", "/omega-hub/v1/review/queue", { count: 1, results: [{ id: review.id, ref: "c-test/csv-answers", version: "1.0.0", kind: "agent", tier: "community", channel: "web", status: "in_review", isUpdate: false, flags: 3, createdAt: new Date().toISOString(), reviewer: null, reviewStartedAt: null }] }],
    ]);
    renderAt(<HubReviewQueuePage />);
    const row = await screen.findByTestId("hub-queue-row");
    expect(row).toHaveTextContent("c-test/csv-answers@1.0.0");
    expect(row).toHaveTextContent("3 flags");
    await waitFor(() => expect(screen.queryByTestId("hub-forbidden")).toBeNull());
  });
});
