import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import { serverRows } from "./catalog";
import ServerCatalog from "./ServerCatalog";

const LONG_ID = "acme-crm-tools-eu-west-production-primary";

describe("ServerCatalog long names", () => {
  it("shows a cut server id in full on hover", () => {
    const rows = serverRows(
      { servers: { [LONG_ID]: { url: "https://mcp.example.com/mcp" } } },
      { servers: [{ server_id: LONG_ID, healthy: true, tool_count: 2 }] },
    );
    renderMcp(<ServerCatalog rows={rows} onOpen={vi.fn()} />);
    expect(screen.getByText(LONG_ID)).toHaveAttribute("title", LONG_ID);
  });
});
