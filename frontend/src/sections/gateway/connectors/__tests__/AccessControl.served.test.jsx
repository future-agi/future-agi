import React from "react";
import { expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderMcp } from "../../mcp/testing/renderMcp";
import AccessControl from "../components/AccessControl";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(async (url) => ({
      data: {
        result:
          url === "/keys/"
            ? []
            : [
                {
                  name: "fixture__current",
                  upstream_name: "current",
                  server_id: "fixture",
                  identity: {
                    platform: "other",
                    toolkit_slug: "",
                    toolkit_name: "",
                  },
                },
              ],
      },
    })),
  },
  endpoints: {
    gateway: {
      mcpServedTools: () => "/served/",
      apiKeys: "/keys/",
      config: () => "/config/",
    },
  },
}));
vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../../mcp/testing/agGridMock");
  return { AgGridReact: MockAgGrid };
});
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

// Imports only components present at the baseline, so the red proves the
// missing live behavior rather than a missing module/build failure.
it("reads live tools independently of historical catalogue declarations", async () => {
  renderMcp(
    <AccessControl
      gatewayId="default"
      canManage
      appName="Fixture"
      provider={{}}
      server={{
        server_id: "fixture",
        virtual_server: { slug: "eng" },
        tools: [{ name: "historical", gateway_posture: "allow" }],
      }}
    />,
  );
  expect(await screen.findByText("fixture__current")).toBeInTheDocument();
  expect(screen.getByText("Served tools (1)")).toBeInTheDocument();
  expect(
    screen.getByText("Historical / declared posture — not live"),
  ).toBeInTheDocument();
  expect(screen.getByText("historical")).toBeInTheDocument();
});
