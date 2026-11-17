import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import CacheStatusView from "./CacheStatusView";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "../testing/adminOnly";

const overview = vi.hoisted(() => ({ data: undefined, isError: false }));
const org = vi.hoisted(() => ({ level: 8 }));

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));

vi.mock("../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => overview,
}));

vi.mock("./hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: null }),
  useCreateOrgConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));

// The editor stands in as the tab it was asked to open on.
vi.mock("../../gateway/settings/OrgConfigEditor", () => ({
  default: (props) =>
    props.open ? <div>editor open at {String(props.defaultTab)}</div> : null,
}));

const CONFIG = { cache: { enabled: true, defaultTtl: "5m" } };

describe("CacheStatusView", () => {
  beforeEach(() => {
    org.level = 8;
    overview.data = {
      total_requests: 608,
      cache_hit_rate: 42.1,
      cache_hits: 256,
      total_cost: 1.2,
    };
    overview.isError = false;
  });

  it("labels its settings and figures in sentence case, as the other gateway pages", () => {
    render(<CacheStatusView config={CONFIG} gatewayId="default" />);
    for (const label of [
      "Cache configuration",
      "Enabled",
      "Default TTL",
      "Cache performance (last 7 days)",
      "Cache hit rate",
      "Total requests",
      "Cache hits",
      "Est. savings",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(
      screen.getByRole("button", { name: "Configure cache" }),
    ).toBeInTheDocument();
  });

  // The editor's Cache tab is named by its id, so the tab this opens does
  // not depend on which editor tabs come before it.
  it("opens the org config editor on its Cache tab", () => {
    render(<CacheStatusView config={CONFIG} gatewayId="d" />);
    fireEvent.click(screen.getByRole("button", { name: "Configure cache" }));
    expect(screen.getByText("editor open at cache")).toBeInTheDocument();
  });

  it("offers no cache mutations or figures when the gateway explicitly disables caching", () => {
    render(
      <CacheStatusView config={{ cache: { enabled: false } }} gatewayId="d" />,
    );
    expect(
      screen.getByText("Response caching is off on this gateway."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Enable cache" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Configure cache" }),
    ).toBeNull();
    expect(screen.queryByText("Cache hit rate")).toBeNull();
  });

  it("says the week's figures could not be loaded, and claims nothing, when the read fails", () => {
    // A failed read used to read "No response was served from cache in the
    // last 7 days" over Total requests 0.
    overview.data = undefined;
    overview.isError = true;
    render(<CacheStatusView config={CONFIG} gatewayId="default" />);
    expect(
      screen.getByText(
        "The cache figures for the last 7 days could not be loaded.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No response was served/)).toBeNull();
    const figure = (label) => screen.getByText(label).parentElement.textContent;
    expect(figure("Total requests")).toBe("Total requests\u2014");
    expect(figure("Cache hit rate")).toBe("Cache hit rate\u2014");
    expect(figure("Cache hits")).toBe("Cache hits\u2014");
    expect(figure("Est. savings")).toBe("Est. savings\u2014");
  });

  // Turning the cache on writes the org config: Owner or Admin only. The
  // editor still opens for reading (it gates its own Save & Activate).
  it.each(NOT_ADMIN_LEVELS)(
    "lets %s read the cache settings but not turn the cache on",
    (_who, level) => {
      org.level = level;
      render(
        <CacheStatusView
          config={{ cache: {}, org_sections_enforced: ["cache"] }}
          gatewayId="default"
        />,
      );
      expectAdminOnly(screen.getByRole("button", { name: "Enable cache" }));
      expect(
        screen.getByRole("button", { name: "Configure cache" }),
      ).toBeEnabled();
      expect(
        screen.getByText(
          "Only an organization owner or admin can change the cache.",
        ),
      ).toBeInTheDocument();
    },
  );

  it.each(ADMIN_LEVELS)("lets %s turn the cache on", (_who, level) => {
    org.level = level;
    render(
      <CacheStatusView
        config={{ cache: {}, org_sections_enforced: ["cache"] }}
        gatewayId="d"
      />,
    );
    expectEditable(screen.getByRole("button", { name: "Enable cache" }));
    expect(
      screen.queryByText(
        "Only an organization owner or admin can change the cache.",
      ),
    ).toBeNull();
  });

  // A week with no requests has no hit rate; it read "0.0%".
  it("reads — for the hit rate of a week with no requests", () => {
    overview.data = {
      total_requests: { value: 0, trend: null },
      cache_hit_rate: { value: 0, trend: null },
      total_cost: { value: "0", trend: null },
    };
    render(<CacheStatusView config={CONFIG} gatewayId="default" />);
    const figure = (label) => screen.getByText(label).parentElement.textContent;
    expect(figure("Total requests")).toBe("Total requests0");
    expect(figure("Cache hit rate")).toBe("Cache hit rate\u2014");
    expect(figure("Cache hits")).toBe("Cache hits0");
  });

  // The savings estimate is cache hits times the average
  // cost of a request, and an unpriced request is stored at cost 0. With no
  // price for any request it read "$0.00"; with some, it understates.
  it("reads the savings estimate as unknown or a floor when requests had no price", () => {
    const figure = (label) => screen.getByText(label).parentElement;
    overview.data = {
      total_requests: { value: 608, trend: null },
      cache_hit_rate: { value: 42.1, trend: null },
      cache_hits: 256,
      total_cost: { value: "0", trend: null, unpriced_requests: 352 },
    };
    const { unmount } = render(
      <CacheStatusView config={CONFIG} gatewayId="default" />,
    );
    expect(figure("Est. savings").textContent).toBe("Est. savings\u2014");
    // unpriced_requests counts every request of the week, so the note does.
    expect(
      within(figure("Est. savings")).getByTitle(
        "No price found for 352 of 608 requests",
      ),
    ).toBeInTheDocument();
    unmount();

    overview.data = {
      ...overview.data,
      total_cost: { value: "1.2", trend: null, unpriced_requests: 2 },
    };
    render(<CacheStatusView config={CONFIG} gatewayId="default" />);
    // MONEY31 r2: exact numerator/count, including the priced lower bound.
    expect(figure("Est. savings").textContent).toBe(
      "Est. savings\u2265\u00a0$307.20 / 352",
    );
    expect(
      within(figure("Est. savings")).getByTitle(
        "2 of 608 requests had no price found, so this estimate counts only the priced ones.",
      ),
    ).toBeInTheDocument();
  });

  // The estimate's two-decimal format rounded a priced
  // floor under a cent to "≥ $0.00", and a small savings to "$0.00", which
  // reads as free. A non-zero amount reads exactly when it rounds to zero.
  it("never reads a non-zero savings estimate as $0.00", () => {
    const figure = (label) => screen.getByText(label).parentElement;
    overview.data = {
      total_requests: 10,
      cache_hits: 1,
      total_cost: { value: "0.036", trend: null, unpriced_requests: 1 },
    };
    const { unmount } = render(
      <CacheStatusView config={CONFIG} gatewayId="default" />,
    );
    // MONEY31 r2: priced floor and fully priced estimate share the exact ratio.
    expect(figure("Est. savings").textContent).toBe(
      "Est. savings\u2265\u00a0$0.036 / 9",
    );
    unmount();

    overview.data = { ...overview.data, total_cost: "0.036" };
    render(<CacheStatusView config={CONFIG} gatewayId="default" />);
    expect(figure("Est. savings").textContent).toBe("Est. savings$0.036 / 9");
  });

  it("shows the week's cache figures from the overview read", () => {
    render(<CacheStatusView config={CONFIG} gatewayId="default" />);
    expect(screen.getByText("42.1%")).toBeInTheDocument();
    expect(screen.getByText("608")).toBeInTheDocument();
    expect(screen.getByText("256")).toBeInTheDocument();
    expect(
      screen.getByText(
        "The gateway served 256 responses from cache in the last 7 days.",
      ),
    ).toBeInTheDocument();
  });
});
