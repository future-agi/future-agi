// RED at 7e05e449: DeploymentNewPage ignores ?hub and starts with bundle=null.
// GREEN requires INTEGRATION-HUNKS.patch to be applied; deliberately
// not a claim that a standalone/unmounted gate ships.
import React from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  renderDeployments,
  fakeApi,
} from "../../deployments/__tests__/helpers";
import { session, workspace } from "../../deployments/__tests__/session-mock";
import fixture from "../../deployments/__tests__/fixtures/serve-fixture.json";
import DeploymentNewPage from "../../deployments/DeploymentNewPage";
import { loadHubDeploymentSource } from "../api/deploymentSource";

const auth = vi.hoisted(() => ({ role: "Owner" }));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => auth,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (original) => ({
  ...(await original()),
  useOmegaSession: () => session,
}));
vi.mock("../api/deploymentSource", () => ({
  loadHubDeploymentSource: vi.fn(),
}));
const pin = "future-agi/first-agent@1.0.0";
const bundle = {
  source: "hub",
  archive: Uint8Array.from(atob(fixture.cli.archiveBase64), (c) =>
    c.charCodeAt(0),
  ),
  sha256: fixture.cli.sha256,
  contentSha256: fixture.cli.contentSha256,
  size: fixture.cli.size,
  files: Object.keys(fixture.files).map((path) => ({
    path,
    size: fixture.files[path].length,
  })),
  excluded: [],
  manifest: { name: "serve-fixture", entries: [{ id: "greet" }] },
  provenance: {
    ref: pin,
    version: "1.0.0",
    digest: `sha256:${"a".repeat(64)}`,
    bundleSha256: "b".repeat(64),
  },
};
beforeEach(() => {
  vi.clearAllMocks();
  auth.role = "Owner";
  Object.assign(session, { state: "ready", code: null, message: null });
  loadHubDeploymentSource.mockResolvedValue(bundle);
});
const mount = (query = `hub=${encodeURIComponent(pin)}`) => {
  const api = fakeApi({ list: vi.fn(async () => []) });
  return renderDeployments(<DeploymentNewPage />, {
    api,
    route: `/dashboard/deployments/new?${query}`,
  });
};

describe("integrated Hub -> existing deployment wizard", () => {
  it("prefills Source, retains Settings/Review and creates only on explicit Deploy with the wizard key", async () => {
    const { api } = mount();
    await screen.findByTestId("bundle-summary");
    expect(screen.getByTestId("source-hub")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByTestId("hub-source-provenance")).toHaveTextContent(pin);
    expect(api.create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("wizard-continue"));
    expect(screen.getByTestId("deployment-name")).toHaveValue("serve-fixture");
    fireEvent.click(screen.getByTestId("wizard-continue"));
    expect(screen.getByTestId("deploy-review")).toHaveTextContent(
      "serve-fixture",
    );
    expect(api.create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("deploy-submit"));
    await waitFor(() => expect(api.create).toHaveBeenCalledOnce());
    expect(api.create.mock.calls[0][0].bundle).toBe(bundle);
    expect(api.create.mock.calls[0][1].idempotencyKey).toMatch(
      /^fa-dep-deploy-/,
    );
  });
  it("does not fetch before SessionGate admits the page", () => {
    session.state = "checking";
    const { api } = mount();
    expect(loadHubDeploymentSource).not.toHaveBeenCalled();
    expect(api.create).not.toHaveBeenCalled();
  });
  it("does not create for a member after prefill", async () => {
    auth.role = "Member";
    const { api } = mount();
    await screen.findByTestId("bundle-summary");
    fireEvent.click(screen.getByTestId("wizard-continue"));
    fireEvent.click(screen.getByTestId("wizard-continue"));
    expect(screen.getByTestId("deploy-submit")).toBeDisabled();
    expect(api.create).not.toHaveBeenCalled();
  });
  it.each([`hub=${pin}&deployment=d1`, `hub=${pin}&hub=${pin}`, "hub="])(
    "refuses ambiguous query %s without choosing either source",
    async (query) => {
      const { api } = mount(query);
      await screen.findByTestId("hub-source-error");
      expect(loadHubDeploymentSource).not.toHaveBeenCalled();
      expect(api.create).not.toHaveBeenCalled();
      expect(screen.queryByTestId("bundle-summary")).toBeNull();
    },
  );
});
