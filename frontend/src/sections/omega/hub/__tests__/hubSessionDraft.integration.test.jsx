// Composed RED/GREEN only: real SessionGate + real useOmegaSession, with a
// deferred in-memory probe. Never contact the assertion service or gateway.
import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fakeApi,
  renderDeployments,
} from "../../deployments/__tests__/helpers";
import fixture from "../../deployments/__tests__/fixtures/serve-fixture.json";
import DeploymentNewPage from "../../deployments/DeploymentNewPage";
import { loadHubDeploymentSource } from "../api/deploymentSource";

const control = vi.hoisted(() => ({
  workspace: { currentWorkspaceId: "ws-a" },
  role: "Owner",
  probe: vi.fn(),
  session: null,
}));
vi.mock("src/contexts/WorkspaceContext", async () => {
  const { createContext, useContext } = await import("react");
  control.WorkspaceContext = createContext(null);
  return { useWorkspace: () => useContext(control.WorkspaceContext) };
});
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => ({ role: control.role }),
}));
vi.mock("src/sections/omega/shared", async (original) => {
  const actual = await original();
  return {
    ...actual,
    useOmegaSession: () => {
      const session = actual.useOmegaSession({ probe: control.probe });
      control.session = session;
      return session;
    },
  };
});
vi.mock("../api/deploymentSource", () => ({
  loadHubDeploymentSource: vi.fn(),
}));

const READY = { state: "ready", code: null, message: null };
const pin = "future-agi/first-agent@1.0.0";
const hubBundle = {
  source: "hub",
  archive: Uint8Array.from(atob(fixture.cli.archiveBase64), (c) =>
    c.charCodeAt(0),
  ),
  sha256: fixture.cli.sha256,
  contentSha256: fixture.cli.contentSha256,
  size: fixture.cli.size,
  files: Object.entries(fixture.files).map(([path, text]) => ({
    path,
    size: text.length,
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
const pending = [];
const pendingLoads = [];
const deferred = (queue) => new Promise((resolve) => queue.push(resolve));
const flushProbe = async (result = READY) => {
  expect(pending.length).toBe(1);
  await act(async () => pending.shift()(result));
};
const flushLoad = async () => {
  expect(pendingLoads.length).toBe(1);
  await act(async () => pendingLoads.shift()(hubBundle));
};

beforeEach(() => {
  vi.clearAllMocks();
  control.workspace.currentWorkspaceId = "ws-a";
  control.role = "Owner";
  control.session = null;
  pending.length = 0;
  pendingLoads.length = 0;
  control.probe.mockImplementation(() => deferred(pending));
  loadHubDeploymentSource.mockImplementation(() => deferred(pendingLoads));
});
afterEach(cleanup);

function WorkspacePage() {
  const [workspace, setWorkspace] = React.useState(control.workspace);
  control.changeWorkspace = setWorkspace;
  return (
    <control.WorkspaceContext.Provider value={workspace}>
      <DeploymentNewPage />
    </control.WorkspaceContext.Provider>
  );
}

function mount() {
  const api = fakeApi({ list: vi.fn(async () => []) });
  const view = renderDeployments(<WorkspacePage />, {
    api,
    route: `/dashboard/deployments/new?hub=${encodeURIComponent(pin)}`,
  });
  return {
    ...view,
    switchWorkspace(id) {
      act(() => control.changeWorkspace({ currentWorkspaceId: id }));
    },
  };
}

function expectChecking(api, loads = 1) {
  expect(control.session.state).toBe("checking");
  expect(screen.getByTestId("deployment-loading")).toBeInTheDocument();
  expect(screen.queryByTestId("wizard-continue")).toBeNull();
  expect(screen.queryByTestId("deploy-submit")).toBeNull();
  expect(screen.queryByTestId("hub-source-workspace-replace")).toBeNull();
  expect(loadHubDeploymentSource).toHaveBeenCalledTimes(loads);
  expect(api.create).not.toHaveBeenCalled();
}

async function editManualDraft() {
  const view = mount();
  expectChecking(view.api, 0);
  await flushProbe();
  await flushLoad();
  await screen.findByTestId("bundle-summary");
  fireEvent.click(screen.getByTestId("source-folder"));
  const files = Object.entries(fixture.files).map(([path, original]) => {
    // A distinct valid manual bundle, not the originally downloaded Hub pin.
    const text =
      path === "omega.studio.json"
        ? JSON.stringify({ ...JSON.parse(original), name: "manual-draft" })
        : original;
    const file = new File([text], path.split("/").pop());
    Object.defineProperty(file, "webkitRelativePath", {
      value: `manual-draft/${path}`,
    });
    return file;
  });
  fireEvent.change(screen.getByTestId("folder-input"), { target: { files } });
  await waitFor(() =>
    expect(screen.getByTestId("bundle-summary")).toHaveTextContent(
      "manual-draft",
    ),
  );
  fireEvent.click(screen.getByTestId("wizard-continue"));
  fireEvent.change(screen.getByTestId("deployment-name"), {
    target: { value: "edited-manual" },
  });
  fireEvent.click(screen.getByTestId("add-env"));
  fireEvent.change(screen.getByLabelText("Variable 1 name"), {
    target: { value: "DRAFT_MARKER" },
  });
  fireEvent.change(screen.getByLabelText("Variable 1 value"), {
    target: { value: "retained-local-value" },
  });
  return view;
}

function expectSettingsRetained() {
  expect(screen.getByTestId("wizard-step-settings")).toBeInTheDocument();
  expect(screen.getByTestId("deployment-name")).toHaveValue("edited-manual");
  expect(screen.getByLabelText("Variable 1 name")).toHaveValue("DRAFT_MARKER");
  expect(screen.getByLabelText("Variable 1 value")).toHaveValue(
    "retained-local-value",
  );
}

function expectPaused(api) {
  expect(screen.getByTestId("hub-source-workspace-changed")).toBeVisible();
  expect(screen.queryByTestId("wizard-step-settings")).toBeNull();
  expect(loadHubDeploymentSource).toHaveBeenCalledTimes(1);
  expect(api.create).not.toHaveBeenCalled();
}

describe("composed session-bound Hub draft", () => {
  it("preserves the workspace-bound manual draft across a real session recheck", async () => {
    const view = await editManualDraft();
    view.switchWorkspace("ws-b");
    expectChecking(view.api);
    await flushProbe();
    expectPaused(view.api);
    view.switchWorkspace("ws-a");
    expectChecking(view.api);
    await flushProbe();
    expectSettingsRetained();
    expect(screen.queryByTestId("hub-source-workspace-changed")).toBeNull();
    expect(loadHubDeploymentSource).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByTestId("bundle-summary")).toHaveTextContent(
      "manual-draft",
    );
    expect(screen.getByTestId("source-folder")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(screen.getByTestId("wizard-continue"));
    fireEvent.click(screen.getByTestId("wizard-continue"));
    expect(view.api.create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("deploy-submit"));
    await waitFor(() => expect(view.api.create).toHaveBeenCalledOnce());
    const request = view.api.create.mock.calls[0][0];
    expect(request.bundle.source).toBe("folder");
    expect(request.bundle.manifest.name).toBe("manual-draft");
    expect(request.name).toBe("edited-manual");
    expect(request.env).toEqual({ DRAFT_MARKER: "retained-local-value" });
  });

  it("requires explicit discard before downloading the source in the new workspace", async () => {
    const view = await editManualDraft();
    view.switchWorkspace("ws-b");
    expectChecking(view.api);
    await flushProbe();
    expectPaused(view.api);
    fireEvent.click(screen.getByTestId("hub-source-workspace-replace"));
    await waitFor(() =>
      expect(loadHubDeploymentSource).toHaveBeenCalledTimes(2),
    );
    await flushLoad();
    expect(screen.getByTestId("bundle-summary")).toHaveTextContent(
      "serve-fixture",
    );
    expect(screen.queryByTestId("hub-source-workspace-changed")).toBeNull();
    fireEvent.click(screen.getByTestId("wizard-continue"));
    expect(screen.getByTestId("deployment-name")).toHaveValue("serve-fixture");
    expect(screen.queryByLabelText("Variable 1 name")).toBeNull();
    expect(view.api.create).not.toHaveBeenCalled();
    // The explicit replacement binds the new draft to B, not its old owner A.
    view.switchWorkspace("ws-a");
    expectChecking(view.api, 2);
    await flushProbe();
    expect(screen.getByTestId("hub-source-workspace-changed")).toBeVisible();
    expect(loadHubDeploymentSource).toHaveBeenCalledTimes(2);
  });

  it("keeps bundle settings step and request key through an unchanged-workspace re-probe", async () => {
    const view = await editManualDraft();
    fireEvent.click(screen.getByTestId("wizard-continue"));
    const before = screen.getByTestId("deploy-review").textContent;
    fireEvent.click(screen.getByTestId("show-as-code"));
    const key = screen
      .getByTestId("show-as-code-panel")
      .textContent.match(/fa-dep-deploy-[\w-]+/)?.[0];
    expect(key).toMatch(/^fa-dep-deploy-/);
    act(() => {
      void control.session.recheck();
    });
    expectChecking(view.api);
    await flushProbe();
    expect(screen.getByTestId("wizard-step-deploy")).toBeInTheDocument();
    expect(screen.getByTestId("deploy-review").textContent).toBe(before);
    fireEvent.click(screen.getByTestId("show-as-code"));
    expect(screen.getByTestId("show-as-code-panel")).toHaveTextContent(key);
    expect(loadHubDeploymentSource).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expectSettingsRetained();
    expect(view.api.create).not.toHaveBeenCalled();
  });

  it("does not prefill from a late Hub result while the real session is checking", async () => {
    const view = mount();
    await flushProbe();
    expect(loadHubDeploymentSource).toHaveBeenCalledTimes(1);
    const signal = loadHubDeploymentSource.mock.calls[0][1].signal;
    act(() => {
      void control.session.recheck();
    });
    expectChecking(view.api);
    expect(signal.aborted).toBe(true);
    await flushLoad();
    expectChecking(view.api);
    expect(screen.queryByTestId("bundle-summary")).toBeNull();
    await flushProbe();
    await waitFor(() =>
      expect(loadHubDeploymentSource).toHaveBeenCalledTimes(2),
    );
    await flushLoad();
    expect(screen.getByTestId("bundle-summary")).toBeInTheDocument();
    expect(view.api.create).not.toHaveBeenCalled();
  });
});
