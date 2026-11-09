import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";

import { resetAccessTokensForTests } from "../lib/accessTokens";
import { readBytes } from "../lib/bundle";
import { SAMPLE_FILE_NAME, sampleBundle } from "../lib/sampleProject";
import { ID, detailView, fakeApi, renderDeployments } from "./helpers";
import { session, workspace } from "./session-mock";

// P22 hide switches on the Deployments pages, each in BOTH states:
// DEPLOY_NO_DOWNTIME (P19) words a revision switch, DEPLOY_LANGGRAPH_JS (P23)
// words what can be deployed. Off is what ships until the owning lane is
// accepted; on is what its acceptance proves. Plus the W0 floor of parity gap
// 8: a sample project that deploys as it is.

// Successful deploy scenarios run as an owner; the real role hook still decides.
const auth = vi.hoisted(() => ({ role: undefined }));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => auth,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
// The sample's two actions, failing on demand (the failure path below); with
// no failure set they are the real ones.
const sampleFailures = vi.hoisted(() => ({ download: null, use: null }));
vi.mock("../lib/sampleProject", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    downloadSample: (...args) =>
      sampleFailures.download
        ? Promise.reject(sampleFailures.download)
        : actual.downloadSample(...args),
    sampleBundle: (...args) =>
      sampleFailures.use
        ? Promise.reject(sampleFailures.use)
        : actual.sampleBundle(...args),
  };
});
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));

const { default: DeploymentNewPage } = await import("../DeploymentNewPage");
const { default: DeploymentsListPage } = await import("../DeploymentsListPage");
const { default: DeploymentDetailPage } = await import(
  "../DeploymentDetailPage"
);

const OFF_SWITCHOVER = /without service for about 3 seconds/;
const ON_SWITCHOVER = /keeps serving until .* answers its health check/;

beforeEach(() => {
  auth.role = undefined;
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  Object.assign(session, { state: "ready", code: null, message: null });
  Object.assign(sampleFailures, { download: null, use: null });
  resetAccessTokensForTests();
});
afterEach(() => vi.unstubAllEnvs());

async function openRollback(parts) {
  renderDeployments(<DeploymentDetailPage />, {
    route: `/dashboard/deployments/${ID}/revisions`,
    parts,
  });
  const rows = await screen.findAllByTestId("revision-row");
  await userEvent.click(within(rows[1]).getByTestId("rollback"));
  return screen.findByTestId("switchover-note");
}

describe("Roll back dialog (DEPLOY_NO_DOWNTIME)", () => {
  it("off: the agent is without service for about 3 seconds while the revision starts", async () => {
    const note = await openRollback([]);
    expect(note).toHaveTextContent(
      "The agent is without service for about 3 seconds while revision 1 starts and answers its health check.",
    );
    expect(screen.queryByText(/a few seconds/)).toBeNull();
  });

  it("on: the serving revision keeps serving until the next answers its health check", async () => {
    const note = await openRollback([OMEGA_PART.DEPLOY_NO_DOWNTIME]);
    expect(note).toHaveTextContent(
      "The revision serving now keeps serving until revision 1 answers its health check, and stays live if it never does.",
    );
    expect(note).not.toHaveTextContent(OFF_SWITCHOVER);
  });
});

describe("Settings: a settings change deploys a new revision (DEPLOY_NO_DOWNTIME)", () => {
  it.each([
    [[], OFF_SWITCHOVER],
    [[OMEGA_PART.DEPLOY_NO_DOWNTIME], ON_SWITCHOVER],
  ])("parts %j", async (parts, words) => {
    renderDeployments(<DeploymentDetailPage />, {
      route: `/dashboard/deployments/${ID}/settings`,
      parts,
    });
    const card = await screen.findByTestId("settings-env");
    expect(within(card).getByTestId("switchover-note")).toHaveTextContent(
      words,
    );
  });
});

describe("New revision of an existing deployment (DEPLOY_NO_DOWNTIME)", () => {
  it.each([
    [[], "The agent is without service for about 3 seconds while revision 3"],
    [
      [OMEGA_PART.DEPLOY_NO_DOWNTIME],
      "The revision serving now keeps serving until revision 3",
    ],
  ])("parts %j", async (parts, words) => {
    renderDeployments(<DeploymentNewPage />, {
      route: `/dashboard/deployments/new?deployment=${ID}`,
      parts,
    });
    await userEvent.click(await screen.findByTestId("sample-use"));
    expect(await screen.findByTestId("bundle-summary")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("wizard-continue"));
    expect(
      await screen.findByText(
        new RegExp(`Deploys revision 3 of greeter. ${words}`),
      ),
    ).toBeInTheDocument();
  });
});

describe("What Deployments can deploy (DEPLOY_LANGGRAPH_JS)", () => {
  it("off: Omega projects (JS/TS), and no LangGraph", async () => {
    renderDeployments(<DeploymentNewPage />, {
      route: "/dashboard/deployments/new",
    });
    expect(
      await screen.findByText(
        "Deploy an Omega project (JS/TS): a folder with omega.studio.json.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/LangGraph/)).toBeNull();
  });

  it("on: Omega or LangGraph.js projects", async () => {
    renderDeployments(<DeploymentNewPage />, {
      route: "/dashboard/deployments/new",
      parts: [OMEGA_PART.DEPLOY_LANGGRAPH_JS],
    });
    expect(
      await screen.findByText(
        "Deploy an Omega or LangGraph.js project (JS/TS): a folder with omega.studio.json or langgraph.json.",
      ),
    ).toBeInTheDocument();
  });

  it.each([
    [
      [],
      "Omega projects (JS/TS) served from this workspace, each at its own URL.",
      "Deploy an Omega project (JS/TS) (a folder with omega.studio.json) and it gets a URL",
    ],
    [
      [OMEGA_PART.DEPLOY_LANGGRAPH_JS],
      "Omega and LangGraph.js projects (JS/TS) served from this workspace, each at its own URL.",
      "Deploy an Omega or LangGraph.js project (JS/TS) (a folder with omega.studio.json or langgraph.json) and it gets a URL",
    ],
  ])(
    "the list's subtitle and empty state, parts %j",
    async (parts, subtitle, empty) => {
      renderDeployments(<DeploymentsListPage />, {
        api: fakeApi({ list: vi.fn(async () => []) }),
        parts,
      });
      expect(await screen.findByTestId("deployments-empty")).toHaveTextContent(
        empty,
      );
      expect(screen.getByText(subtitle)).toBeInTheDocument();
    },
  );
});

describe("The sample project (parity gap 8, W0 floor)", () => {
  it("does not deploy the sample for a member", async () => {
    auth.role = "Member";
    const api = fakeApi();
    renderDeployments(<DeploymentNewPage />, {
      api,
      route: "/dashboard/deployments/new",
    });
    await userEvent.click(await screen.findByTestId("sample-use"));
    await screen.findByTestId("bundle-summary");
    await userEvent.click(screen.getByTestId("wizard-continue"));
    await userEvent.click(screen.getByTestId("wizard-continue"));
    const submit = screen.getByTestId("deploy-submit");
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(api.create).not.toHaveBeenCalled();
  });

  it("is offered under Project folder and Bundle file, never under the CLI source", async () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    renderDeployments(<DeploymentNewPage />, {
      route: "/dashboard/deployments/new",
      parts: [OMEGA_PART.DEPLOY_CLI],
    });
    const sample = await screen.findByTestId("sample-project");
    expect(sample).toHaveTextContent(
      "No project yet? Start from the sample: a support-triage workflow in TypeScript that needs no model key.",
    );
    await userEvent.click(screen.getByTestId("source-archive"));
    expect(screen.getByTestId("sample-project")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("source-cli"));
    expect(screen.queryByTestId("sample-project")).toBeNull();
  });

  it("downloads the bytes it deploys, under its file name", async () => {
    const saved = [];
    const createObjectURL = vi.fn((blob) => {
      saved.push(blob);
      return "blob:sample";
    });
    const revokeObjectURL = vi.fn();
    // jsdom has no object URLs: stand them in for this test, then restore.
    const original = {
      createObjectURL: URL.createObjectURL,
      revokeObjectURL: URL.revokeObjectURL,
    };
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function record() {
        saved.push({ download: this.download, href: this.href });
      });
    renderDeployments(<DeploymentNewPage />, {
      route: "/dashboard/deployments/new",
    });
    await userEvent.click(await screen.findByTestId("sample-download"));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    const [file, anchor] = saved;
    expect(anchor).toEqual({ download: SAMPLE_FILE_NAME, href: "blob:sample" });
    const bytes = await readBytes(file);
    const bundle = await sampleBundle();
    expect(bytes).toEqual(bundle.archive);
    await waitFor(() =>
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:sample"),
    );
    click.mockRestore();
    Object.assign(URL, original);
  });

  it("deploys as it is: Use the sample, keep its name, and the deploy carries its bundle", async () => {
    auth.role = "Owner";
    const accepted = detailView({
      status: "queued",
      liveRevision: null,
      url: null,
      studioUrl: null,
      operation: null,
      accessToken: "s".repeat(43),
    });
    const api = fakeApi({
      list: vi.fn(async () => []),
      create: vi.fn(async () => accepted),
    });
    renderDeployments(<DeploymentNewPage />, {
      api,
      route: "/dashboard/deployments/new",
    });
    await userEvent.click(await screen.findByTestId("sample-use"));
    const summary = await screen.findByTestId("bundle-summary");
    expect(summary).toHaveTextContent("support-triage");
    expect(summary).toHaveTextContent("1 entry: Triage a support message");
    expect(screen.getByTestId("source-archive")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await userEvent.click(screen.getByTestId("wizard-continue"));
    expect(screen.getByTestId("deployment-name")).toHaveValue("support-triage");
    await userEvent.click(screen.getByTestId("wizard-continue"));
    await userEvent.click(screen.getByTestId("deploy-submit"));
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1));
    const [input] = api.create.mock.calls[0];
    const expected = await sampleBundle();
    expect(input.name).toBe("support-triage");
    expect(input.bundle.contentSha256).toBe(expected.contentSha256);
    expect(input.bundle.manifest.name).toBe("support-triage");
  });
});

describe("The sample project when it fails (fix round 1)", () => {
  it("a download that fails says the sample could not be saved, and both buttons work again", async () => {
    sampleFailures.download = new Error("the browser refused the save");
    renderDeployments(<DeploymentNewPage />, {
      route: "/dashboard/deployments/new",
    });
    await userEvent.click(await screen.findByTestId("sample-download"));
    expect(await screen.findByTestId("sample-problem")).toHaveTextContent(
      "The sample could not be saved. Try again.",
    );
    expect(screen.getByTestId("sample-download")).toBeEnabled();
    expect(screen.getByTestId("sample-use")).toBeEnabled();
    expect(screen.getByTestId("sample-download")).toHaveTextContent(
      "Download the sample",
    );
    // The other action still works, and clears the problem.
    await userEvent.click(screen.getByTestId("sample-use"));
    expect(await screen.findByTestId("bundle-summary")).toHaveTextContent(
      "support-triage",
    );
    expect(screen.queryByTestId("sample-problem")).toBeNull();
  });

  it("a sample that cannot be read says so, deploys nothing, and a retry that works clears it", async () => {
    sampleFailures.use = new Error("unreadable archive");
    renderDeployments(<DeploymentNewPage />, {
      route: "/dashboard/deployments/new",
    });
    await userEvent.click(await screen.findByTestId("sample-use"));
    expect(await screen.findByTestId("sample-problem")).toHaveTextContent(
      "The sample could not be read. Try again.",
    );
    expect(screen.queryByTestId("bundle-summary")).toBeNull();
    expect(screen.getByTestId("sample-use")).toBeEnabled();
    expect(screen.getByTestId("sample-use")).toHaveTextContent(
      "Use the sample here",
    );
    sampleFailures.use = null;
    await userEvent.click(screen.getByTestId("sample-use"));
    expect(await screen.findByTestId("bundle-summary")).toBeInTheDocument();
    expect(screen.queryByTestId("sample-problem")).toBeNull();
  });
});
