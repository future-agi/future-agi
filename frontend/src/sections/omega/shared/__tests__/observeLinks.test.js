import { describe, expect, it } from "vitest";

import { hydrateStoredFilterList } from "src/api/contracts/filter-contract";

import {
  buildAddEvalsLink,
  buildObserveErrorLink,
  buildObserveIssueLink,
  buildObserveLink,
  buildObserveRunLink,
  deploymentObserveFilters,
  observeLinkForTarget,
  parseObserveOrigin,
} from "../observeLinks";

const location = {
  origin: "https://app.futureagi.test",
  pathname: "/dashboard/deployments/dep-123",
  search: "",
  hash: "",
};

const decodeFilters = (href, parameter = "observeLinkFilter") => {
  const url = new URL(href, location.origin);
  return JSON.parse(url.searchParams.get(parameter));
};

const filterValuesByColumn = (href) =>
  decodeFilters(href).map((row) => [
    row.column_id,
    row.filter_config?.filter_value,
  ]);

describe("deployment Observe links", () => {
  it("builds a link when the caller leaves location out (the page's own location is the default)", () => {
    // A parameter named `window` once shadowed the global in `location`'s default and threw here.
    const options = {
      projectId: "project-1",
      deploymentId: "dep-0123456789abcdef",
      window: "7d",
    };
    for (const build of [
      buildObserveLink,
      buildObserveErrorLink,
      buildObserveRunLink,
    ]) {
      const href = build(options);
      expect(href).toMatch(/^\/dashboard\/observe\/project-1\/llm-tracing\?/);
      expect(
        new URL(href, window.location.origin).searchParams.get(
          "primaryTraceDateFilter",
        ),
      ).toBeTruthy();
    }
    expect(
      observeLinkForTarget(
        { projectId: "project-1" },
        { deploymentId: "dep-0123456789abcdef" },
      ),
    ).toMatch(/^\/dashboard\/observe\/project-1\/llm-tracing\?/);
    const back = new URL(
      buildObserveLink({
        ...options,
        returnTo: "/dashboard/deployments/dep-0123456789abcdef",
      }),
      window.location.origin,
    );
    expect(back.searchParams.get("observeReturnTo")).toBe(
      "/dashboard/deployments/dep-0123456789abcdef",
    );
  });

  it("builds canonical, chip-visible deployment and revision filters", () => {
    const filters = deploymentObserveFilters({
      deploymentId: "dep-123",
      revision: "5",
    });
    expect(filters).toEqual([
      expect.objectContaining({
        column_id: "omega.deployment.id",
        display_name: "Deployment ID",
        property_id: "custom_attribute:omega.deployment.id",
        filter_config: expect.objectContaining({
          filter_type: "text",
          filter_op: "equals",
          filter_value: "dep-123",
          col_type: "SPAN_ATTRIBUTE",
        }),
      }),
      expect.objectContaining({
        column_id: "omega.deployment.revision",
        display_name: "Revision",
        filter_config: expect.objectContaining({ filter_value: "5" }),
      }),
    ]);
  });

  it("round-trips a link through URL encoding and preserves removable rows", () => {
    const href = buildObserveLink({
      projectId: "observe-project",
      deploymentId: "dep-123",
      deploymentName: "support-agent",
      revision: "5",
      returnTo: "https://app.futureagi.test/dashboard/deployments/dep-123",
      location,
      primaryTraceDateFilter: {
        dateFilter: ["2026-10-18 00:00:00", "2026-10-19 00:00:00"],
        dateOption: "Custom",
      },
    });
    expect(href).toContain("/dashboard/observe/observe-project/llm-tracing?");
    const url = new URL(href, location.origin);
    expect(url.searchParams.get("tab")).toBe("traces");
    expect(url.searchParams.get("selectedTab")).toBe("trace");
    expect(url.searchParams.get("observeFrom")).toBe(
      "support-agent · revision 5 in Deployments",
    );
    expect(url.searchParams.get("observeReturnTo")).toBe(
      "/dashboard/deployments/dep-123",
    );
    expect(JSON.parse(url.searchParams.get("primaryTraceDateFilter"))).toEqual({
      dateFilter: ["2026-10-18 00:00:00", "2026-10-19 00:00:00"],
      dateOption: "Custom",
    });
    expect(decodeFilters(href)).toHaveLength(2);
    expect(decodeFilters(href)[0].filter_config.filter_value).toBe("dep-123");
  });

  it("uses the supplied window to keep a 24-hour strip aligned with the list", () => {
    const href = buildObserveLink({
      projectId: "project-1",
      deploymentId: "dep-1",
      revision: 2,
      window: "24h",
      location,
    });
    const dateFilter = JSON.parse(
      new URL(href, location.origin).searchParams.get("primaryTraceDateFilter"),
    );
    expect(dateFilter.dateOption).toBe("Custom");
    expect(dateFilter.dateFilter).toHaveLength(2);
  });

  it("merges caller filters instead of dropping deployment scope", () => {
    const href = buildObserveErrorLink({
      projectId: "project-1",
      deploymentId: "dep-1",
      revision: 2,
      filters: [
        {
          column_id: "model",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "gemini",
          },
        },
      ],
      location,
    });
    expect(filterValuesByColumn(href)).toEqual([
      ["omega.deployment.id", "dep-1"],
      ["omega.deployment.revision", "2"],
      ["model", "gemini"],
      ["status", "ERROR"],
    ]);
  });

  it("round-trips builder rows through the real hydration contract", () => {
    const href = buildObserveLink({
      projectId: "project-1",
      deploymentId: "dep-123",
      revision: 5,
      location,
    });
    const hydrated = hydrateStoredFilterList(
      decodeFilters(href),
      () => "row-id",
    );
    expect(hydrated.map((row) => row.filter_config.filter_value)).toEqual([
      "dep-123",
      "5",
    ]);
    expect(hydrated.every((row) => row.id === "row-id")).toBe(true);
  });

  it("builds a one-run link with the deployment scope and run chip", () => {
    const href = buildObserveRunLink({
      projectId: "project-1",
      deploymentId: "dep-1",
      revision: 3,
      runId: "run-42",
      location,
    });
    expect(new URL(href, location.origin).pathname).toBe(
      "/dashboard/observe/project-1/llm-tracing",
    );
    expect(filterValuesByColumn(href)).toContainEqual([
      "omega.run.id",
      "run-42",
    ]);
  });

  it("uses a trace-detail route when a trace id is known", () => {
    const href = buildObserveLink({
      projectId: "project-1",
      deploymentId: "dep-1",
      revision: 3,
      traceId: "trace-42",
      returnTo: "/dashboard/deployments/dep-1",
      location,
    });
    expect(new URL(href, location.origin).pathname).toBe(
      "/dashboard/observe/project-1/trace/trace-42",
    );
    expect(
      new URL(href, location.origin).searchParams.get("observeReturnTo"),
    ).toBe("/dashboard/deployments/dep-1");
  });

  it("builds Add evals and Error Feed links from the shared seam", () => {
    const addEvals = buildAddEvalsLink({
      projectId: "project-1",
      deploymentId: "dep-1",
      revision: 4,
      returnTo: "/dashboard/deployments/dep-1",
      location,
    });
    expect(new URL(addEvals, location.origin).pathname).toBe(
      "/dashboard/tasks/create",
    );
    expect(
      JSON.parse(
        new URL(addEvals, location.origin).searchParams.get("filters"),
      ),
    ).toHaveLength(2);
    expect(
      new URL(addEvals, location.origin).searchParams.get("returnTo"),
    ).toBe("/dashboard/deployments/dep-1");
    expect(buildObserveIssueLink("issue-7")).toBe(
      "/dashboard/error-feed/issue-7",
    );
  });

  it("accepts target responses with snake_case or camelCase ids", () => {
    expect(
      observeLinkForTarget(
        {
          project_id: "project-1",
          deployment_id: "dep-1",
          project_name: "deploy/support-agent",
        },
        { location, revision: 2 },
      ),
    ).toContain("/dashboard/observe/project-1/llm-tracing?");
  });

  it("drops an unsafe origin pair instead of creating an outbound return link", () => {
    const href = buildObserveLink({
      projectId: "project-1",
      deploymentId: "dep-1",
      returnTo: "https://evil.example/steal",
      origin: "Deployments",
      location,
    });
    const url = new URL(href, location.origin);
    expect(url.searchParams.has("observeFrom")).toBe(false);
    expect(url.searchParams.has("observeReturnTo")).toBe(false);

    expect(
      parseObserveOrigin({
        search:
          "?observeFrom=Deployments&observeReturnTo=https%3A%2F%2Fevil.example%2F",
        location,
      }),
    ).toBeNull();
  });

  it("rejects protocol-relative and backslash return paths", () => {
    for (const returnTo of [
      "//evil.example/steal",
      "\\\\evil.example\\steal",
      "https://user:pass@app.futureagi.test/secret",
    ]) {
      const href = buildObserveLink({
        projectId: "project-1",
        deploymentId: "dep-1",
        returnTo,
        origin: "Deployments",
        location,
      });
      const url = new URL(href, location.origin);
      expect(url.searchParams.has("observeFrom")).toBe(false);
      expect(url.searchParams.has("observeReturnTo")).toBe(false);
    }
  });

  it("requires both origin parameters and bounds the label", () => {
    expect(
      parseObserveOrigin({ search: "?observeFrom=Deployments", location }),
    ).toBeNull();
    expect(
      parseObserveOrigin({
        search:
          "?observeFrom=Deployments&observeReturnTo=%2Fdashboard%2Fdeployments",
        location,
      }),
    ).toEqual({ origin: "Deployments", returnTo: "/dashboard/deployments" });
    expect(
      parseObserveOrigin({
        search: `?observeFrom=${"x".repeat(121)}&observeReturnTo=%2Fdashboard%2Fdeployments`,
        location,
      }),
    ).toBeNull();
  });
});
