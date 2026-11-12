import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import {
  AddCredentialDialog,
  RotateCredentialDialog,
} from "./CredentialDialogs";

const org = vi.hoisted(() => ({ level: 8 }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));

beforeEach(() => {
  org.level = 8;
});

describe("credential dialogs enforce the gateway role before submission", () => {
  it.each([1, 3, null])("blocks direct add submission for role %s", (level) => {
    org.level = level;
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
    expect(
      screen.getByRole("button", { name: "Store credential" }),
    ).toBeDisabled();
    expect(screen.getByText("Owner or Admin only")).toBeInTheDocument();
  });

  it.each([1, 3, null])(
    "blocks direct rotate submission for role %s",
    (level) => {
      org.level = level;
      renderMcp(
        <RotateCredentialDialog
          credential={{
            id: "fixture-id",
            server_id: "crm-tools",
            kind: "bearer",
          }}
          onClose={() => {}}
        />,
      );
      fireEvent.change(screen.getByLabelText(/New bearer token/), {
        target: { value: "fixture-only" },
      });
      expect(screen.getByRole("button", { name: "Rotate" })).toBeDisabled();
      expect(screen.getByText("Owner or Admin only")).toBeInTheDocument();
    },
  );

  it.each([8, 15])("keeps a valid add form enabled for role %s", (level) => {
    org.level = level;
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
    expect(
      screen.getByRole("button", { name: "Store credential" }),
    ).toBeEnabled();
  });
});

it.each(["oauth", "oauth_client", "header"])(
  "does not offer a static-secret rotation form for %s",
  (kind) => {
    renderMcp(
      <RotateCredentialDialog
        credential={{ id: "fixture-id", server_id: "crm-tools", kind }}
        onClose={() => {}}
      />,
    );
    expect(screen.queryByLabelText(/New API key/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Rotate" })).toBeNull();
  },
);

// W07 enabled-scenario fixture; the release-switch specs exercise OFF.
vi.mock("../hooks/useMCPReleaseFeatures", () => ({
  useMCPReleaseFeatures: () => ({
    catalog: true,
    credentialVault: true,
    virtualServers: true,
    catalogConnect: true,
  }),
  useMCPReleaseGuard: () => () => {},
  // The status has answered: writes are not held.
  useMCPWriteHold: () => ({ held: false, failed: false, retry: () => {} }),
}));
