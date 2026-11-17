import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import OrgConfigEditor from "./OrgConfigEditor";

// The Routing and Model Database tabs explain how managed models are routed
// and priced. They are told whether this deployment offers managed models
// (GatewayContext, from GET /agentcc/managed-models/), so a deployment that
// offers none reads no managed sentence.

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), get: vi.fn() },
  endpoints: {
    gateway: {
      orgConfig: { create: "/agentcc/org-configs/" },
      config: (id) => `/agentcc/gateways/${id}/config/`,
      updateConfig: (id) => `/agentcc/gateways/${id}/update-config/`,
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const context = vi.hoisted(() => ({ managedOffered: false }));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "gw-1",
    managedOffered: context.managedOffered,
  }),
}));

// Each tab reports the managed state it was given.
const Reports = ({ name, managedOffered }) => (
  <div>{`${name} managedOffered=${String(managedOffered)}`}</div>
);
Reports.propTypes = { name: PropTypes.string, managedOffered: PropTypes.bool };
vi.mock("./RoutingConfigTab", () => ({
  default: (props) => <Reports name="Routing" {...props} />,
}));
vi.mock("./ModelDatabaseConfigTab", () => ({
  default: (props) => <Reports name="Model database" {...props} />,
}));

const renderEditor = (defaultTab) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <OrgConfigEditor
        open
        onClose={vi.fn()}
        initialConfig={{ id: "cfg-1", version: 1 }}
        defaultTab={defaultTab}
      />
    </QueryClientProvider>,
  );

describe("OrgConfigEditor: managed models", () => {
  beforeEach(() => {
    context.managedOffered = false;
  });

  it("tells Routing and Model Database the deployment offers no managed models", () => {
    renderEditor("routing");
    expect(
      screen.getByText("Routing managedOffered=false"),
    ).toBeInTheDocument();
  });

  it("tells Model Database too", () => {
    renderEditor("model_database");
    expect(
      screen.getByText("Model database managedOffered=false"),
    ).toBeInTheDocument();
  });

  it("tells them when the deployment offers managed models", () => {
    context.managedOffered = true;
    renderEditor("routing");
    expect(screen.getByText("Routing managedOffered=true")).toBeInTheDocument();
  });
});
