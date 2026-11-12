import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";

import { renderMcp, DEMO_MCP } from "./testing/renderMcp";
import {
  AddCredentialDialog,
  RotateCredentialDialog,
} from "./credentials/CredentialDialogs";
import VirtualServerDrawer from "./virtual/VirtualServerDrawer";

// Vault writes (create, rotate) and virtual-server
// patches are held while the MCP status read is loading or has failed. The
// submit stays disabled, and on a failed read the page's status-error state
// (with Retry) shows beside it.
const { hold } = vi.hoisted(() => ({
  hold: { current: { held: false, failed: false, retry: () => {} } },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("./hooks/useMCPReleaseFeatures", () => ({
  useMCPReleaseFeatures: () => ({
    catalog: true,
    credentialVault: true,
    virtualServers: true,
    catalogConnect: true,
  }),
  useMCPReleaseGuard: () => () => {},
  useMCPWriteHold: () => hold.current,
}));
vi.mock("./hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({ data: { enabled: true, features: {} } }),
}));

const STATUS_ERROR = "MCP status could not be loaded";
const LOADING = { held: true, failed: false, retry: () => {} };

function addDialog() {
  renderMcp(
    <AddCredentialDialog
      open
      onClose={() => {}}
      serverIds={["crm-tools"]}
      taken={new Set()}
      initialServer="crm-tools"
    />,
  );
  fireEvent.change(screen.getByLabelText(/^Token/), {
    target: { value: "fixture-only" },
  });
  return screen.getByRole("button", { name: "Store credential" });
}

function rotateDialog() {
  renderMcp(
    <RotateCredentialDialog
      credential={{ id: "fixture-id", server_id: "crm-tools", kind: "bearer" }}
      onClose={() => {}}
    />,
  );
  fireEvent.change(screen.getByLabelText(/New bearer token/), {
    target: { value: "fixture-only" },
  });
  return screen.getByRole("button", { name: "Rotate" });
}

function drawer() {
  renderMcp(
    <VirtualServerDrawer
      open
      onClose={() => {}}
      slug="support-desk"
      entry={DEMO_MCP.virtual_servers["support-desk"]}
      gatewayId="gw-1"
      baseUrl="http://localhost:28490/v1"
      serverIds={["support-tools", "crm-tools"]}
      tools={[]}
      keys={[]}
      takenSlugs={new Set()}
    />,
  );
  return screen.getByRole("button", { name: "Save" });
}

beforeEach(() => {
  hold.current = { held: false, failed: false, retry: () => {} };
});

describe.each([
  ["Store credential", addDialog],
  ["Rotate", rotateDialog],
  ["Save (virtual server)", drawer],
])("%s while the MCP status has not answered", (_label, open) => {
  it("is enabled once the status has answered", () => {
    expect(open()).toBeEnabled();
    expect(screen.queryByText(STATUS_ERROR)).toBeNull();
  });

  it("is disabled while the status is loading, with no error shown", () => {
    hold.current = LOADING;
    expect(open()).toBeDisabled();
    expect(screen.queryByText(STATUS_ERROR)).toBeNull();
  });

  it("is disabled after the status read failed, beside the status error and its Retry", () => {
    const retry = vi.fn();
    hold.current = { held: true, failed: true, retry };
    expect(open()).toBeDisabled();
    expect(screen.getByText(STATUS_ERROR)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledTimes(1);
  });
});

it("holds a virtual server's Delete while the status is loading", () => {
  hold.current = LOADING;
  drawer();
  expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
});
