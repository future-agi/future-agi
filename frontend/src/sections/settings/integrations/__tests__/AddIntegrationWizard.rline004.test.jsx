import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithRouter, screen } from "src/utils/test-utils";
import AddIntegrationWizard from "../AddIntegrationWizard";
import { IntegrationsScopeContext } from "../scope";

const h = vi.hoisted(() => ({ create: vi.fn() }));
const CONNECTION_ID = "c0a80101-0000-4000-8000-000000000004";
const WORKSPACE_ID = "c0a80101-0000-4000-8000-0000000000b0";

// Keep the real wizard, success callback, router, scope hook and sync step.
// Credential validation is outside this navigation regression.
vi.mock("../AddIntegrationWizard/StepPlatformSelect", () => ({
  default: () => null,
}));
vi.mock("../AddIntegrationWizard/StepCredentials", async () => {
  const { default: PropTypes } = await import("prop-types");
  function ValidatedCredentials({ onNext }) {
    return <button onClick={onNext}>Continue to sync settings</button>;
  }
  ValidatedCredentials.propTypes = { onNext: PropTypes.func.isRequired };
  return { default: ValidatedCredentials };
});
vi.mock("../AddIntegrationWizard/StepProjectMapping", () => ({
  default: () => null,
}));
vi.mock("src/api/integrations", () => ({
  useCreateConnection: () => ({
    mutate: h.create,
    isPending: false,
    error: null,
  }),
}));

beforeEach(() => {
  h.create.mockReset();
  h.create.mockImplementation((_payload, { onSuccess }) => {
    onSuccess({ data: { result: { id: CONNECTION_ID } } });
  });
});

describe("RLINE-004 View Integration navigation", () => {
  it.each([
    [
      "preserves the initiating workspace for a scoped wizard",
      WORKSPACE_ID,
      `/dashboard/settings/workspace/${WORKSPACE_ID}/integrations`,
    ],
    [
      "keeps the global detail route for an unscoped wizard",
      undefined,
      "/dashboard/settings/integrations",
    ],
  ])("%s", (_name, workspaceId, listPath) => {
    const onClose = vi.fn();
    const wizard = (
      <AddIntegrationWizard
        open
        platforms={[]}
        initialPlatform="datadog"
        onClose={onClose}
        onSelectSlack={vi.fn()}
      />
    );
    renderWithRouter(
      workspaceId ? (
        <IntegrationsScopeContext.Provider
          value={{ workspaceId, canManage: true }}
        >
          {wizard}
        </IntegrationsScopeContext.Provider>
      ) : (
        wizard
      ),
      { route: listPath },
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Continue to sync settings" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Connect Integration" }),
    );
    expect(h.create).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Integration Connected!")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe(listPath);

    fireEvent.click(screen.getByRole("button", { name: "View Integration" }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(window.location.pathname).toBe(`${listPath}/${CONNECTION_ID}`);
  });
});
