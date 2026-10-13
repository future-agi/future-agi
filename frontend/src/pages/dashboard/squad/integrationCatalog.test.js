import { describe, expect, it } from "vitest";
import {
  coverageRows,
  filterIntegrations,
  integrationFamily,
  integrationState,
} from "./integrationCatalog";

describe("integration catalog projection", () => {
  const rows = [
    {
      id: "github",
      name: "GitHub",
      provider: "GitHub",
      auth: "oauth",
      status: "installed",
    },
    {
      id: "gmail",
      name: "Gmail",
      provider: "Google",
      auth: "oauth",
      status: "available",
    },
    {
      id: "custom-mcp-orders",
      name: "Orders MCP",
      provider: "MCP",
      status: "available",
    },
  ];

  it("derives connection state and family only from server fields", () => {
    expect(integrationState(rows[0])).toBe("connected");
    expect(integrationState(rows[1])).toBe("available");
    expect(integrationState({ id: "missing", status: "unsupported" })).toBe(
      "unavailable",
    );
    expect(integrationFamily(rows[1])).toBe("Google");
    expect(integrationFamily(rows[2])).toBe("Other");
  });

  it("filters by search, family, connected, and unavailable without inventing rows", () => {
    expect(
      filterIntegrations(rows, { query: "git" }).map((row) => row.id),
    ).toEqual(["github"]);
    expect(
      filterIntegrations(rows, { filter: "connected" }).map((row) => row.id),
    ).toEqual(["github"]);
    expect(
      filterIntegrations(rows, { filter: "Google" }).map((row) => row.id),
    ).toEqual(["gmail"]);
    expect(filterIntegrations(rows, { filter: "unavailable" })).toEqual([]);
  });

  it("keeps the complete provider coverage matrix and marks absent providers unavailable", () => {
    const matrix = coverageRows(rows);
    expect(matrix).toHaveLength(17);
    expect(matrix.find((row) => row.id === "github")).toMatchObject({
      state: "connected",
    });
    expect(matrix.find((row) => row.id === "gmail")).toMatchObject({
      state: "available",
    });
    expect(matrix.find((row) => row.id === "salesforce")).toMatchObject({
      state: "unavailable",
      serverRows: [],
    });
    expect(matrix.find((row) => row.id === "custom-mcp")).toMatchObject({
      state: "available",
    });
  });
});
