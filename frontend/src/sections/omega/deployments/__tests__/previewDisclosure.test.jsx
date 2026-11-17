import React from "react";
import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ID, fakeApi, renderDeployments } from "./helpers";
import { session, workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));

const { default: DeploymentsListPage } = await import("../DeploymentsListPage");
const { default: DeploymentNewPage } = await import("../DeploymentNewPage");

// Independent copy of the operator disclosure: changing implementation copy
// must not silently change what these acceptance tests require.
const DISCLOSURE =
  "Self-hosted Deployments is a preview. Run it only on a dedicated deploy-engine host you own, not shared with other workloads or tenants. Until a separate control daemon ships, the server and its deploy engine are root-equivalent on that host. Each deployment's outbound network is isolated in its own network namespace, which is on by default.";

/** @param {Pick<typeof screen, "getByText">} [scope] */
function expectDisclosure(scope = screen) {
  expect(scope.getByText(DISCLOSURE, { exact: true })).toBeVisible();
  expect(scope.getByText("Preview", { exact: true })).toBeVisible();
}

beforeEach(() => {
  Object.assign(session, { state: "ready", code: null, message: null });
});

describe("Self-hosted Deployments preview disclosure", () => {
  it("discloses host isolation and root equivalence on the populated landing", async () => {
    renderDeployments(<DeploymentsListPage />);
    await screen.findAllByTestId("deployment-row");
    expectDisclosure();
  });

  it("discloses the same terms before the first deployment in the empty landing", async () => {
    renderDeployments(<DeploymentsListPage />, {
      api: fakeApi({ list: vi.fn(async () => []) }),
    });
    await screen.findByTestId("deployments-empty");
    expectDisclosure();
    expect(screen.getByTestId("new-deployment")).toBeEnabled();
  });

  it("retains the disclosure when the landing cannot open a session", async () => {
    Object.assign(session, {
      state: "refused",
      code: "viewer",
      message: "Viewers cannot use Omega.",
    });
    const api = fakeApi();
    renderDeployments(<DeploymentsListPage />, { api });
    await screen.findByText("Viewers cannot use Omega.");
    expectDisclosure();
    expect(api.list).not.toHaveBeenCalled();
  });

  it.each([
    "/dashboard/deployments/new",
    `/dashboard/deployments/new?deployment=${ID}`,
  ])(
    "discloses the exact terms on the Source step at %s, before upload or creation",
    async (route) => {
      const api = fakeApi();
      renderDeployments(<DeploymentNewPage />, { api, route });
      const source = await screen.findByTestId("wizard-step-source");
      expectDisclosure(within(source));
      expect(screen.getByTestId("wizard-continue")).toBeDisabled();
      expect(api.create).not.toHaveBeenCalled();
    },
  );
});
