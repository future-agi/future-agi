import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OmegaError } from "src/sections/omega/shared/omegaStates";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";

import { resetAccessTokensForTests } from "../lib/accessTokens";
import fixture from "./fixtures/serve-fixture.json";
import {
  ID,
  detailView,
  fakeApi,
  renderDeployments,
  revisionView,
} from "./helpers";
import { session, workspace } from "./session-mock";

const auth = vi.hoisted(() => ({ role: undefined }));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => auth,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));

const { default: DeploymentNewPage } = await import("../DeploymentNewPage");

function folderFiles() {
  return Object.entries(fixture.files).map(([path, text]) => {
    const file = new File([text], path.split("/").pop());
    Object.defineProperty(file, "webkitRelativePath", {
      value: `serve-fixture/${path}`,
    });
    return file;
  });
}

async function pickFolderAndContinue() {
  fireEvent.change(await screen.findByTestId("folder-input"), {
    target: { files: folderFiles() },
  });
  expect(await screen.findByTestId("bundle-summary")).toHaveTextContent(
    "serve-fixture",
  );
  await userEvent.click(screen.getByTestId("wizard-continue"));
}

beforeEach(() => {
  auth.role = undefined;
  Object.assign(session, { state: "ready", code: null, message: null });
  resetAccessTokensForTests();
});
afterEach(() => vi.unstubAllEnvs());

describe("New deployment wizard", () => {
  it.each([
    ["a new deployment", "/dashboard/deployments/new"],
    ["a new revision", `/dashboard/deployments/new?deployment=${ID}`],
  ])(
    "does not create %s for a member after selecting a folder",
    async (_, route) => {
      auth.role = "Member";
      const api = fakeApi();
      renderDeployments(<DeploymentNewPage />, { api, route });
      await pickFolderAndContinue();
      await userEvent.click(screen.getByTestId("wizard-continue"));
      const submit = screen.getByTestId("deploy-submit");
      expect(submit).toBeDisabled();
      fireEvent.click(submit);
      expect(api.create).not.toHaveBeenCalled();
    },
  );

  it.each(["archive", "cli"])(
    "keeps the selected bundle visible after switching to %s (FA-DEP-UX-03)",
    async (mode) => {
      vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
      renderDeployments(<DeploymentNewPage />, {
        route: "/dashboard/deployments/new",
        parts: [OMEGA_PART.DEPLOY_CLI],
      });
      fireEvent.change(await screen.findByTestId("folder-input"), {
        target: { files: folderFiles() },
      });
      await screen.findByTestId("bundle-summary");
      await userEvent.click(screen.getByTestId(`source-${mode}`));
      expect(screen.getByTestId("bundle-summary")).toHaveTextContent(
        "serve-fixture",
      );
      expect(screen.getByTestId("wizard-continue")).toBeEnabled();
    },
  );

  it.each([
    ["building", "Deploying greeter · revision 2"],
    ["live", "greeter is running on this host"],
    ["failed", "Revision 2 did not go live"],
  ])(
    "announces the %s tracker summary as a status (FA-DEP-UX-10)",
    async (status, summary) => {
      auth.role = "Owner";
      const detail = detailView();
      const api = fakeApi({
        list: vi.fn(async () => []),
        get: vi.fn(async () => ({
          ...detail,
          status,
          operation: { ...detail.operation, status },
        })),
      });
      renderDeployments(<DeploymentNewPage />, {
        api,
        route: "/dashboard/deployments/new",
      });
      await pickFolderAndContinue();
      await userEvent.click(screen.getByTestId("wizard-continue"));
      await userEvent.click(screen.getByTestId("deploy-submit"));
      const tracker = await screen.findByTestId("deploy-tracker");
      expect(await within(tracker).findByText(summary)).toHaveAttribute(
        "role",
        "status",
      );
    },
  );

  it("bundles the folder, sends omega-deploy/1 with a key, and follows the revision to Live", async () => {
    auth.role = "Owner";
    let status = "building";
    const accepted = detailView({
      status: "queued",
      revision: 1,
      liveRevision: null,
      url: null,
      studioUrl: null,
      revisions: [],
      history: [],
      operation: null,
      accessToken: "s".repeat(43),
    });
    const api = fakeApi({
      list: vi.fn(async () => []),
      create: vi.fn(async () => accepted),
      get: vi.fn(async () =>
        status === "live"
          ? detailView({
              revision: 1,
              liveRevision: 1,
              operation: {
                seq: 1,
                action: "deploy",
                revision: 1,
                by: "a",
                startedAt: "",
                status: "live",
                message: "Revision 1 is live.",
              },
            })
          : detailView({
              status,
              revision: 1,
              liveRevision: null,
              url: null,
              studioUrl: null,
              operation: {
                seq: 1,
                action: "deploy",
                revision: 1,
                by: "a",
                startedAt: "",
                status,
                message: "Building revision 1.",
              },
            }),
      ),
      credentials: vi.fn(async () => [
        { name: "SERVE_FIXTURE_SECRET", status: "active" },
      ]),
    });
    renderDeployments(<DeploymentNewPage />, {
      api,
      route: "/dashboard/deployments/new",
    });
    await pickFolderAndContinue();

    expect(screen.getByTestId("deployment-name")).toHaveValue("serve-fixture");
    // The deployment's page masks variable values, and the hint says so.
    expect(
      screen.getByText(/its page shows their names only/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/shown on its page/)).toBeNull();
    await userEvent.click(screen.getByTestId("add-env"));
    await userEvent.type(
      screen.getByLabelText("Variable 1 name"),
      "SERVE_FIXTURE_GREETING",
    );
    await userEvent.type(screen.getByLabelText("Variable 1 value"), "hello-v1");
    await userEvent.click(
      await screen.findByLabelText("Secret SERVE_FIXTURE_SECRET"),
    );
    await userEvent.click(screen.getByTestId("wizard-continue"));
    expect(screen.getByTestId("deploy-review")).toHaveTextContent(
      "SERVE_FIXTURE_GREETING",
    );

    await userEvent.click(screen.getByTestId("deploy-submit"));
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1));
    const [input, { idempotencyKey }] = api.create.mock.calls[0];
    expect(input.name).toBe("serve-fixture");
    expect(input.bundle.contentSha256).toBe(fixture.cli.contentSha256);
    expect(input.env).toEqual({ SERVE_FIXTURE_GREETING: "hello-v1" });
    expect(input.secretRefs).toEqual(["SERVE_FIXTURE_SECRET"]);
    expect(idempotencyKey).toMatch(/^fa-dep-deploy-/);

    expect(await screen.findByTestId("deploy-progress")).toHaveAttribute(
      "data-status",
      "building",
    );
    expect(screen.getByTestId("progress-queued")).toHaveAttribute(
      "data-state",
      "done",
    );
    expect(screen.getByTestId("progress-building")).toHaveAttribute(
      "data-state",
      "current",
    );
    expect(api.logs).toHaveBeenCalledWith(
      ID,
      expect.objectContaining({ source: "build", revision: 1 }),
    );

    status = "live";
    await waitFor(
      () =>
        expect(screen.getByTestId("deploy-tracker")).toHaveAttribute(
          "data-status",
          "live",
        ),
      { timeout: 4000 },
    );
    expect(screen.getByTestId("deploy-url")).toHaveTextContent(
      "http://127.0.0.1:55408",
    );
    expect(screen.getByTestId("access-token-notice")).toBeInTheDocument();
    // Only this host can reach it (DEMO-FIX-2 item 1): said so, in the heading
    // and on the last stage. Here, right under the access token it just
    // minted, the result links the deployment's own Studio page, bare
    // (OWNER-RULINGS 28, OD-21): that page's key gate is one paste away, so it
    // is no dead end. The detail page, where the person may never have seen
    // the token, links nothing on that host (DEMO-POLISH-3). Open in Studio
    // waits for a deployed server that opens in Studio (DEPLOYMENT_STUDIO).
    expect(screen.getByTestId("deploy-tracker")).toHaveTextContent(
      "greeter is running on this host",
    );
    expect(screen.getByTestId("progress-live")).toHaveTextContent(
      "Running on this host",
    );
    expect(screen.getByTestId("progress-live")).toHaveAttribute(
      "data-state",
      "done",
    );
    expect(screen.queryByTestId("open-in-studio")).toBeNull();
    expect(screen.getByTestId("open-studio-page")).toHaveAttribute(
      "href",
      "http://127.0.0.1:55408/",
    );
    const gate = screen.getByTestId("studio-page-gate");
    expect(gate).toHaveAttribute("data-token-held", "true");
    expect(gate).toHaveTextContent("copy it from Access token above");
    for (const el of document.querySelectorAll("[href]")) {
      expect(el.getAttribute("href")).not.toContain("s".repeat(43));
    }
  });

  it("re-sends a deploy after a lost reply with the same Idempotency-Key, and a changed one with a new key", async () => {
    auth.role = "Owner";
    const create = vi
      .fn()
      .mockRejectedValueOnce(
        new OmegaError({
          state: "unavailable",
          status: 502,
          code: "upstream_unavailable",
          message: "The Omega gateway could not be reached.",
        }),
      )
      .mockRejectedValueOnce(
        new OmegaError({
          state: "error",
          status: 400,
          code: "secret_unavailable",
          message: "No active credential named X.",
        }),
      );
    const api = fakeApi({ list: vi.fn(async () => []), create });
    renderDeployments(<DeploymentNewPage />, {
      api,
      route: "/dashboard/deployments/new",
    });
    await pickFolderAndContinue();
    await userEvent.click(screen.getByTestId("wizard-continue"));
    await userEvent.click(screen.getByTestId("deploy-submit"));
    // The transient failure is re-sent once on its own, with the same key; EE's refusal is shown.
    expect(await screen.findByTestId("deploy-error")).toHaveTextContent(
      "No active credential named X.",
    );
    expect(create).toHaveBeenCalledTimes(2);
    expect(create.mock.calls[1][1].idempotencyKey).toBe(
      create.mock.calls[0][1].idempotencyKey,
    );

    await userEvent.click(screen.getByText("Back"));
    await userEvent.clear(screen.getByTestId("deployment-name"));
    await userEvent.type(screen.getByTestId("deployment-name"), "renamed");
    await userEvent.click(screen.getByTestId("wizard-continue"));
    create.mockResolvedValueOnce(detailView());
    await userEvent.click(screen.getByTestId("deploy-submit"));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(3));
    expect(create.mock.calls[2][0].name).toBe("renamed");
    expect(create.mock.calls[2][1].idempotencyKey).not.toBe(
      create.mock.calls[0][1].idempotencyKey,
    );
  });

  it("refuses a folder that is not an Omega project", async () => {
    renderDeployments(<DeploymentNewPage />, {
      route: "/dashboard/deployments/new",
    });
    const file = new File(["x"], "index.ts");
    Object.defineProperty(file, "webkitRelativePath", {
      value: "app/index.ts",
    });
    fireEvent.change(await screen.findByTestId("folder-input"), {
      target: { files: [file] },
    });
    expect(await screen.findByTestId("source-problem")).toHaveTextContent(
      "no omega.studio.json",
    );
    expect(screen.getByTestId("wizard-continue")).toBeDisabled();
  });

  it("takes a LangGraph.js project folder (langgraph.json) as a project, named after its folder", async () => {
    renderDeployments(<DeploymentNewPage />, {
      route: "/dashboard/deployments/new",
    });
    const files = {
      "langgraph.json": '{"graphs":{"agent":"./src/agent/graph.ts:graph"}}',
      "src/agent/graph.ts": "export const graph = {};\n",
    };
    const picked = Object.entries(files).map(([path, text]) => {
      const file = new File([text], path.split("/").pop());
      Object.defineProperty(file, "webkitRelativePath", {
        value: `new-langgraphjs-project/${path}`,
      });
      return file;
    });
    fireEvent.change(await screen.findByTestId("folder-input"), {
      target: { files: picked },
    });
    const summary = await screen.findByTestId("bundle-summary");
    expect(summary).toHaveTextContent("new-langgraphjs-project");
    expect(summary).toHaveTextContent("1 entry: agent");
    expect(screen.queryByTestId("source-problem")).toBeNull();
    await userEvent.click(screen.getByTestId("wizard-continue"));
    expect(screen.getByTestId("deployment-name")).toHaveValue(
      "new-langgraphjs-project",
    );
    await userEvent.click(screen.getByTestId("wizard-continue"));
    expect(await screen.findByTestId("deploy-review")).toHaveTextContent(
      "new-langgraphjs-project · 2 files",
    );
  });

  it("offers no CLI source while no customer can install `omega deploy`", async () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    renderDeployments(<DeploymentNewPage />, {
      route: "/dashboard/deployments/new",
    });
    expect(await screen.findByTestId("source-folder")).toBeInTheDocument();
    expect(screen.getByTestId("source-archive")).toBeInTheDocument();
    expect(screen.queryByTestId("source-cli")).toBeNull();
    expect(screen.queryByTestId("cli-source")).toBeNull();
    vi.unstubAllEnvs();
  });

  it("names no unreleased CLI on the Bundle file and Project folder sources", async () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    renderDeployments(<DeploymentNewPage />, {
      route: "/dashboard/deployments/new",
    });
    await userEvent.click(await screen.findByTestId("source-archive"));
    expect(
      screen.getByText(
        "A .tar.gz with omega.studio.json at its top level, holding only files and folders. It is sent as it is.",
      ),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/omega deploy|\bCLI\b/);

    await userEvent.click(screen.getByTestId("source-folder"));
    const secret = new File(["KEY=1"], ".env");
    Object.defineProperty(secret, "webkitRelativePath", {
      value: "serve-fixture/.env",
    });
    fireEvent.change(await screen.findByTestId("folder-input"), {
      target: { files: [...folderFiles(), secret] },
    });
    expect(await screen.findByTestId("bundle-excluded")).toHaveTextContent(
      "Left out of the bundle: .env (may hold secrets)",
    );
    expect(document.body.textContent).not.toMatch(/omega deploy|\bCLI\b/);
    vi.unstubAllEnvs();
  });

  it("offers the CLI's bundle command once the CLI ships `omega deploy`", async () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    renderDeployments(<DeploymentNewPage />, {
      route: "/dashboard/deployments/new",
      parts: [OMEGA_PART.DEPLOY_CLI],
    });
    await userEvent.click(await screen.findByTestId("source-cli"));
    expect(screen.getByTestId("cli-bundle-command")).toHaveTextContent(
      "omega deploy bundle --project . --out omega-project.tar.gz",
    );
    vi.unstubAllEnvs();
  });

  it("leaves out Secrets when the workspace vault has nothing to pick", async () => {
    const api = fakeApi({ credentials: vi.fn(async () => []) });
    renderDeployments(<DeploymentNewPage />, {
      api,
      route: "/dashboard/deployments/new",
    });
    await pickFolderAndContinue();
    expect(await screen.findByTestId("deployment-name")).toBeInTheDocument();
    await waitFor(() => expect(api.credentials).toHaveBeenCalled());
    expect(screen.queryByText("Secrets")).toBeNull();
    expect(screen.queryByTestId("secrets-empty")).toBeNull();
    expect(screen.queryByTestId("secret-picker")).toBeNull();
  });

  it("deploys the next revision of an existing deployment with its settings and a locked name", async () => {
    const api = fakeApi();
    renderDeployments(<DeploymentNewPage />, {
      api,
      route: `/dashboard/deployments/new?deployment=${ID}`,
    });
    await pickFolderAndContinue();
    expect(screen.getByTestId("deployment-name")).toHaveValue("greeter");
    expect(screen.getByTestId("deployment-name")).toBeDisabled();
    const value = screen.getByLabelText("Variable 1 value");
    expect(value).toHaveValue("hello-v2");
    expect(value).toHaveAttribute("type", "password");
    await userEvent.click(
      screen.getByRole("button", { name: "Show variable 1 value" }),
    );
    expect(value).toHaveAttribute("type", "text");
    expect(
      screen.getByText(/Deploys revision 3 of greeter/),
    ).toBeInTheDocument();
  });

  it("holds a redeploy that carries a secret name the vault will not inject until it is unticked", async () => {
    const detail = detailView({
      revisions: [
        revisionView(2, { status: "live", secretRefs: ["OMEGA_API_KEY"] }),
        revisionView(1),
      ],
    });
    const api = fakeApi({
      get: vi.fn(async () => detail),
      credentials: vi.fn(async () => [
        {
          credentialId: "cred_omega",
          name: "OMEGA_API_KEY",
          kind: "api_key",
          status: "active",
          version: 1,
          bindingCount: 1,
        },
      ]),
    });
    renderDeployments(<DeploymentNewPage />, {
      api,
      route: `/dashboard/deployments/new?deployment=${ID}`,
    });
    await pickFolderAndContinue();
    expect(await screen.findByTestId("secrets-blocked")).toHaveTextContent(
      "Untick OMEGA_API_KEY to continue",
    );
    expect(screen.getByTestId("wizard-continue")).toBeDisabled();
    await userEvent.click(screen.getByLabelText("Secret OMEGA_API_KEY"));
    expect(screen.getByTestId("wizard-continue")).toBeEnabled();
    expect(api.create).not.toHaveBeenCalled();
  });
});
