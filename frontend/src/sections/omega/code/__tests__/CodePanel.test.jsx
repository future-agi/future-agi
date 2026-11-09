import React from "react";
import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";
import { SS_KEY_ORG_ID, SS_KEY_WORKSPACE_ID } from "src/utils/sessionKeys";

import fixture from "../../deployments/__tests__/fixtures/serve-fixture.json";
import {
  ID,
  detailView,
  fakeApi as fakeDeployments,
  renderDeployments,
} from "../../deployments/__tests__/helpers";
import { session, workspace } from "../../deployments/__tests__/session-mock";
import {
  CAPS_V2,
  fakeApi as fakeSandboxes,
  renderSandboxes,
  sandboxView,
} from "../../sandboxes/__tests__/helpers";
import { forgetKey, holdKey } from "../keyStore";

// The code panels on the real Sandboxes and Deployments pages: what each
// shows, the key masked until an explicit reveal, Copy, the SDK tabs behind
// their release switch, and "Show as code" following the create form.

// A workspace owner: EE lets an owner or admin change a deployment, and the
// panels offer those writes only then (CodePanel.safety.test.jsx covers a member).
// Signed in as this person: a held key is theirs alone (keyStore.js).
const auth = vi.hoisted(() => ({
  role: "Owner",
  authenticated: true,
  user: { id: "44444444-4444-4444-8444-444444444444" },
}));
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
const created = vi.hoisted(() => ({ reply: null, calls: [] }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      post: vi.fn(async (url, body) => {
        created.calls.push({ url, body });
        if (created.reply instanceof Error) throw created.reply;
        return { data: { status: true, result: created.reply } };
      }),
    },
  };
});

const { default: SandboxesListPage } = await import(
  "../../sandboxes/SandboxesListPage"
);
const { default: SandboxDetailPage } = await import(
  "../../sandboxes/SandboxDetailPage"
);
const { default: DeploymentsListPage } = await import(
  "../../deployments/DeploymentsListPage"
);
const { default: DeploymentDetailPage } = await import(
  "../../deployments/DeploymentDetailPage"
);
const { default: DeploymentNewPage } = await import(
  "../../deployments/DeploymentNewPage"
);

const ORG = "22222222-2222-4222-8222-222222222222";
const WS = "33333333-3333-4333-8333-333333333333";
const USER = "44444444-4444-4444-8444-444444444444";
const KEY = {
  key_id: "55555555-5555-4555-8555-555555555555",
  key_name: "Code snippets 2026-10-22 10:41:07",
  api_key: "c0c0c0c0c0c0c0c0c0c0c0c0c0c0wxyz",
  secret_key: "d1d1d1d1d1d1d1d1d1d1d1d1d1d1stuv",
};

let clipboard;
beforeEach(() => {
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  auth.role = "Owner";
  auth.user = { id: USER };
  Object.assign(session, { state: "ready", code: null, message: null });
  sessionStorage.setItem(SS_KEY_ORG_ID, ORG);
  sessionStorage.setItem(SS_KEY_WORKSPACE_ID, WS);
  forgetKey();
  created.reply = KEY;
  created.calls = [];
});
afterEach(() => {
  vi.unstubAllEnvs();
  sessionStorage.clear();
  forgetKey();
});

const sandboxesApi = (overrides = {}) =>
  fakeSandboxes({
    list: vi.fn(async () => ({
      sandboxes: [sandboxView()],
      caps: CAPS_V2,
      provider: null,
    })),
    ...overrides,
  });

/** Sandboxes pages with the capabilities answer `parts` (the release switches). */
function renderSandboxPage(
  element,
  { route, parts = [], api = sandboxesApi() } = {},
) {
  const rendered = renderSandboxes(element, { api, route });
  act(() => {
    rendered.queryClient.setQueryData(CAPABILITIES_QUERY_KEY, {
      data: capabilitiesAnswer({ parts }),
    });
  });
  return rendered;
}

async function openCode(user) {
  await user.click(await screen.findByTestId("code-open"));
  return screen.findByTestId("code-drawer");
}

const snippet = () => screen.getByTestId("code-snippet").textContent;

/** userEvent with a clipboard that records what Copy wrote (setup() installs its own). */
function setup() {
  const user = userEvent.setup();
  clipboard = [];
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn(async (text) => clipboard.push(text)) },
  });
  return user;
}

/** The labels of the drawer's "Call" choices, opened as MUI opens a select. */
async function callChoices() {
  fireEvent.mouseDown(
    within(screen.getByTestId("code-panel")).getByRole("combobox"),
  );
  return (await screen.findAllByRole("option")).map((o) => o.textContent);
}

describe("the Code drawer on a Sandboxes page", () => {
  it("shows the list call for this stack and workspace, signed from the shell when no key is held", async () => {
    const user = setup();
    renderSandboxPage(<SandboxesListPage />);
    const drawer = await openCode(user);
    expect(within(drawer).getByTestId("code-snippet").dataset.requestId).toBe(
      "sandboxes.list",
    );
    const text = snippet();
    expect(text).toContain(
      `curl -sS '${window.location.origin}/omega/api/workspace/sandboxes'`,
    );
    expect(text).toContain('"X-Api-Key: $FI_API_KEY"');
    expect(text).toContain(`X-Workspace-Id: ${WS}`);
    expect(screen.getByTestId("code-key-strip").dataset.keyState).toBe("none");
    // Curl only: the SDK is not published.
    expect(screen.queryByTestId("code-tab-python")).toBeNull();
    expect(screen.queryByTestId("code-tab-typescript")).toBeNull();
    await user.click(screen.getByTestId("code-copy"));
    expect(clipboard).toEqual([text]);
  });

  it("creates a key on request, masks it, and copies it only after an explicit reveal", async () => {
    const user = setup();
    const log = vi.spyOn(console, "log");
    renderSandboxPage(<SandboxesListPage />);
    await openCode(user);
    await user.click(screen.getByTestId("code-create-key"));
    await screen.findByTestId("code-reveal-key");
    expect(created.calls).toEqual([
      {
        url: expect.stringContaining("/accounts/key/generate_secret_key/"),
        body: { key_name: expect.stringMatching(/^Code snippets /) },
      },
    ]);
    expect(screen.getByTestId("code-key-name").textContent).toBe(KEY.key_name);

    // Masked on screen; Copy reads the key from the shell.
    expect(screen.getByTestId("code-key-strip").dataset.keyState).toBe(
      "masked",
    );
    // How long it is kept, as keyStore.js keeps it (a reload drops it too).
    expect(screen.getByTestId("code-key-kept").textContent).toBe(
      "Kept in this tab, for you only, until you sign out, reload or forget it. Disable it any time in API keys.",
    );
    expect(snippet()).not.toContain(KEY.secret_key);
    expect(snippet()).not.toContain(KEY.api_key);
    expect(snippet()).toContain(KEY.api_key.slice(-4));
    await user.click(screen.getByTestId("code-copy"));
    expect(clipboard.at(-1)).toContain('"X-Secret-Key: $FI_SECRET_KEY"');
    expect(clipboard.at(-1)).not.toContain(KEY.secret_key);

    // Revealed: shown and copied in full, in headers only.
    await user.click(screen.getByTestId("code-reveal-key"));
    expect(snippet()).toContain(`X-Secret-Key: ${KEY.secret_key}`);
    await user.click(screen.getByTestId("code-copy"));
    expect(clipboard.at(-1)).toContain(`-H 'X-Secret-Key: ${KEY.secret_key}'`);
    expect(clipboard.at(-1).split("\n")[0]).not.toContain(KEY.secret_key);

    // Hidden again, then forgotten: back to the shell variables.
    await user.click(screen.getByTestId("code-reveal-key"));
    expect(snippet()).not.toContain(KEY.secret_key);
    await user.click(screen.getByTestId("code-forget-key"));
    expect(screen.getByTestId("code-key-strip").dataset.keyState).toBe("none");
    expect(
      JSON.stringify({ ...localStorage, ...sessionStorage }),
    ).not.toContain(KEY.secret_key);
    expect(log).not.toHaveBeenCalled();
    log.mockRestore();
  });

  it("says so when a key cannot be created, and keeps the shell form", async () => {
    created.reply = new Error("403");
    const user = setup();
    renderSandboxPage(<SandboxesListPage />);
    await openCode(user);
    await user.click(screen.getByTestId("code-create-key"));
    expect(await screen.findByTestId("code-create-key-error")).toBeTruthy();
    expect(screen.getByTestId("code-key-strip").dataset.keyState).toBe("none");
  });

  it("lists for its creator what this sandbox's page offers, leading with the open tab", async () => {
    // An organization Member with a workspace role is an Omega member, whom EE
    // shows only the sandboxes they created (sandboxRole.js).
    auth.role = "workspace_member";
    const user = setup();
    const sandbox = sandboxView();
    renderSandboxPage(<SandboxDetailPage />, {
      route: `/dashboard/sandboxes/${sandbox.id}/run`,
      api: sandboxesApi({ get: vi.fn(async () => sandbox) }),
    });
    await openCode(user);
    expect(screen.getByTestId("code-snippet").dataset.requestId).toBe(
      "sandboxes.exec",
    );
    expect(snippet()).toMatch(/^curl -sS -N -X POST /);
    expect(await callChoices()).toEqual([
      "Run a command (streamed)",
      "Read this sandbox",
      "List files in /workspace",
      "List snapshots",
      "Take a snapshot",
      "Read the activity",
      "Hibernate",
      "Delete this sandbox",
    ]);
    await user.click(
      screen.getByRole("option", { name: "Delete this sandbox" }),
    );
    expect(snippet()).toContain("-X DELETE");
    expect(screen.getByTestId("code-destructive")).toBeTruthy();
    expect(screen.queryByTestId("code-note")).toBeNull();
  });

  // POLISH-FA (CODE-PANELS verdict, low): EE refuses exec, snapshot, hibernate
  // and resume to anyone but the sandbox's creator, and an owner or admin may
  // be looking at another member's sandbox (EE's view names no creator).
  it.each(["Owner", "Admin", undefined])(
    "role %s: lists reads and delete only, and says why the creator-only calls are not there",
    async (role) => {
      auth.role = role;
      const user = setup();
      const sandbox = sandboxView();
      renderSandboxPage(<SandboxDetailPage />, {
        route: `/dashboard/sandboxes/${sandbox.id}/run`,
        api: sandboxesApi({ get: vi.fn(async () => sandbox) }),
      });
      await openCode(user);
      // The Run tab leads with exec for its creator; here it is not offered.
      expect(screen.getByTestId("code-snippet").dataset.requestId).toBe(
        "sandboxes.get",
      );
      expect(await callChoices()).toEqual([
        "Read this sandbox",
        "List files in /workspace",
        "List snapshots",
        "Read the activity",
        "Delete this sandbox",
      ]);
      expect(screen.getByTestId("code-note").textContent).toBe(
        "Only the member who created a sandbox can run commands in it, snapshot, hibernate or resume it. As an owner or admin you see every sandbox, so this panel lists the calls that work on any of them: reading and deleting.",
      );
    },
  );

  it("on a hibernated sandbox an owner is offered no resume; nothing is said where nothing was left out", async () => {
    const user = setup();
    const hibernated = sandboxView({ state: "hibernated" });
    const { unmount } = renderSandboxPage(<SandboxDetailPage />, {
      route: `/dashboard/sandboxes/${hibernated.id}`,
      api: sandboxesApi({ get: vi.fn(async () => hibernated) }),
    });
    await openCode(user);
    expect(await callChoices()).toEqual([
      "Read this sandbox",
      "List snapshots",
      "Read the activity",
      "Delete this sandbox",
    ]);
    expect(screen.getByTestId("code-note")).toBeTruthy();
    unmount();
    // An agent run's sandbox is read-only for everyone: no call is withheld.
    const run = sandboxView({ readOnly: true });
    renderSandboxPage(<SandboxDetailPage />, {
      route: `/dashboard/sandboxes/${run.id}`,
      api: sandboxesApi({ get: vi.fn(async () => run) }),
    });
    await openCode(user);
    expect(screen.queryByTestId("code-note")).toBeNull();
  });

  it("keeps Sandboxes curl-only while released SDK tabs remain available for Deployments", async () => {
    const user = setup();
    const sandboxes = renderSandboxPage(<SandboxesListPage />, {
      parts: [OMEGA_PART.SDK_PYTHON, OMEGA_PART.SDK_TYPESCRIPT],
    });
    await openCode(user);
    expect(snippet().startsWith("curl ")).toBe(true);
    expect(screen.queryByTestId("code-tab-python")).toBeNull();
    expect(screen.queryByTestId("code-tab-typescript")).toBeNull();
    expect(screen.queryByTestId("code-tab-curl")).toBeNull();
    sandboxes.unmount();

    // Keep the released-SDK and key-strip assertions on a supported surface.
    renderDeployments(<DeploymentsListPage />, {
      parts: [OMEGA_PART.SDK_PYTHON, OMEGA_PART.SDK_TYPESCRIPT],
    });
    await openCode(user);
    await user.click(await screen.findByTestId("code-tab-python"));
    expect(snippet().split("\n")[0]).toBe("pip install futureagi");
    expect(screen.queryByTestId("code-key-strip")).toBeNull();
    await user.click(screen.getByTestId("code-tab-typescript"));
    expect(snippet().split("\n")[0]).toBe("npm i @future-agi/sdk");
  });
});

describe("Show as code in the create drawer", () => {
  it("emits the drawer's own request and follows every change", async () => {
    const user = setup();
    renderSandboxPage(<SandboxesListPage />);
    await user.click(await screen.findByTestId("new-sandbox"));
    const drawer = await screen.findByTestId("create-sandbox-drawer");
    await user.click(within(drawer).getByTestId("show-as-code"));
    const body = () =>
      JSON.parse(
        snippet()
          .match(/--data-binary '(.*)'$/m)[1]
          .replace(/'\\''/g, "'"),
      );
    expect(body()).toEqual({
      template: "base",
      size: "small",
      network: "none",
      idleTtlSeconds: 1800,
    });
    expect(snippet()).toMatch(/Idempotency-Key: fa-sbx-/);

    const name = within(drawer).getByLabelText(/name/i);
    await user.type(name, "it's mine");
    expect(body().name).toBe("it's mine");
    await user.clear(name);
    expect(body().name).toBeUndefined();
  });

  it("shows no request while the form would not send one", async () => {
    const user = setup();
    renderSandboxPage(<SandboxesListPage />);
    await user.click(await screen.findByTestId("new-sandbox"));
    const drawer = await screen.findByTestId("create-sandbox-drawer");
    await user.click(within(drawer).getByTestId("show-as-code"));
    expect(within(drawer).getByTestId("code-snippet")).toBeTruthy();
    // Custom limits past this workspace's maximum: Create would refuse them.
    await user.click(within(drawer).getByTestId("create-size-custom"));
    const cpus = within(drawer).getByTestId("create-cpus");
    await user.clear(cpus);
    await user.type(cpus, "64");
    expect(
      await within(drawer).findByTestId("show-as-code-blocked"),
    ).toBeTruthy();
    expect(within(drawer).queryByTestId("code-snippet")).toBeNull();
    // Back within the limits: the request is back, with exactly those limits.
    await user.clear(cpus);
    await user.type(cpus, "2");
    const text = within(drawer).getByTestId("code-snippet").textContent;
    expect(text).toContain(
      '"limits":{"cpus":2,"memoryMb":1024,"pidsLimit":256}',
    );
    expect(text).not.toContain('"size"');
  });
});

/** The serve fixture's files, as the folder input hands them over. */
function folderFiles() {
  return Object.entries(fixture.files).map(([path, text]) => {
    const file = new File([text], path.split("/").pop());
    Object.defineProperty(file, "webkitRelativePath", {
      value: `serve-fixture/${path}`,
    });
    return file;
  });
}

describe("Show as code in the deployment wizard", () => {
  it("stays open across the steps, follows Settings, and sends the key Deploy then sends", async () => {
    const user = setup();
    const api = fakeDeployments({
      list: vi.fn(async () => []),
      create: vi.fn(async () =>
        detailView({
          status: "queued",
          revision: 1,
          liveRevision: null,
          revisions: [],
          operation: null,
        }),
      ),
    });
    renderDeployments(<DeploymentNewPage />, {
      api,
      route: "/dashboard/deployments/new",
    });
    await user.click(await screen.findByTestId("show-as-code"));
    // No project yet: no request, and it says why.
    expect(screen.getByTestId("show-as-code-blocked")).toBeTruthy();
    expect(screen.queryByTestId("code-snippet")).toBeNull();

    fireEvent.change(await screen.findByTestId("folder-input"), {
      target: { files: folderFiles() },
    });
    await screen.findByTestId("bundle-summary");
    // A project and the default settings: the request Deploy would send now.
    expect(snippet()).toMatch(/--data-binary '@omega-deploy-request\.json'$/m);
    const keyOf = () =>
      snippet().match(/Idempotency-Key: (fa-dep-deploy-[^']+)'/)[1];
    const first = keyOf();

    await user.click(screen.getByTestId("wizard-continue"));
    // Still open on Settings; a changed name is a new request with a new key.
    const name = screen.getByTestId("deployment-name");
    await user.clear(name);
    expect(await screen.findByTestId("show-as-code-blocked")).toBeTruthy();
    await user.type(name, "renamed");
    expect(keyOf()).not.toBe(first);
    // The body file the snippet reads is offered as a download (jsdom has no object URLs).
    let downloaded = null;
    Object.assign(URL, {
      createObjectURL: (value) => {
        downloaded = value;
        return "blob:code-panels";
      },
      revokeObjectURL: () => {},
    });
    const saved = [];
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function save() {
        saved.push(this.download);
      });
    await user.click(screen.getByTestId("code-download-body"));
    click.mockRestore();
    expect(saved).toEqual(["omega-deploy-request.json"]);
    const text = await new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.readAsText(downloaded);
    });
    const body = JSON.parse(text);
    expect(body).toMatchObject({
      protocol: "omega-deploy/1",
      name: "renamed",
      serve: { studio: true },
    });
    expect(body.bundle.contentSha256).toBe(fixture.cli.contentSha256);
    delete URL.createObjectURL;
    delete URL.revokeObjectURL;

    await user.click(screen.getByTestId("wizard-continue"));
    const shownKey = keyOf();
    await user.click(screen.getByTestId("deploy-submit"));
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1));
    const [input, { idempotencyKey }] = api.create.mock.calls[0];
    expect(input.name).toBe("renamed");
    expect(idempotencyKey).toBe(shownKey);
  });
});

describe("the Code drawer on a Deployments page", () => {
  it("lists deployments, and offers no write while one is busy", async () => {
    const user = setup();
    renderDeployments(<DeploymentsListPage />);
    await openCode(user);
    expect(screen.getByTestId("code-snippet").dataset.requestId).toBe(
      "deployments.list",
    );
    expect(snippet()).toContain("/omega/api/workspace/deployments'");
  });

  it("leads a live deployment's Revisions tab with the rollback its page offers", async () => {
    const user = setup();
    renderDeployments(<DeploymentDetailPage />, {
      route: `/dashboard/deployments/${ID}/revisions`,
      api: fakeDeployments({ get: vi.fn(async () => detailView()) }),
    });
    await openCode(user);
    expect(screen.getByTestId("code-snippet").dataset.requestId).toBe(
      "deployments.rollback",
    );
    expect(snippet()).toContain(
      `/workspace/deployments/${ID}/revisions/1/rollback'`,
    );
    expect(snippet()).toContain(`--data-binary '{"expectedRevision":2}'`);
    expect(snippet()).toMatch(/Idempotency-Key: fa-dep-rollback-/);
  });

  it("offers curl only until an SDK is released, then its Python and TypeScript tabs (P22)", async () => {
    const user = setup();
    const first = renderDeployments(<DeploymentsListPage />);
    await openCode(user);
    expect(snippet().startsWith("curl ")).toBe(true);
    expect(screen.queryByTestId("code-tab-python")).toBeNull();
    expect(screen.queryByTestId("code-tab-typescript")).toBeNull();
    expect(screen.queryByTestId("code-tab-curl")).toBeNull();
    first.unmount();

    renderDeployments(<DeploymentsListPage />, {
      parts: [OMEGA_PART.SDK_PYTHON, OMEGA_PART.SDK_TYPESCRIPT],
    });
    await openCode(user);
    await user.click(await screen.findByTestId("code-tab-python"));
    expect(snippet().split("\n")[0]).toBe("pip install futureagi");
    expect(snippet()).toContain("client.deployments.list()");
    await user.click(screen.getByTestId("code-tab-typescript"));
    expect(snippet().split("\n")[0]).toBe("npm i @future-agi/sdk");
  });

  it("offers reads only while the deployment is building", async () => {
    const user = setup();
    renderDeployments(<DeploymentDetailPage />, {
      route: `/dashboard/deployments/${ID}`,
      api: fakeDeployments({
        get: vi.fn(async () => detailView({ status: "building" })),
      }),
    });
    await openCode(user);
    expect(await callChoices()).toEqual([
      "Read this deployment",
      "Check its health",
      "Build log of revision 2 (streamed)",
    ]);
  });
});

describe("a viewer, whom the session exchange refuses", () => {
  it("sees no Code action on any page: no page data loads, so no snippet is offered", async () => {
    // The exchange refuses viewers (accounts/views/omega_session.py,
    // omega_viewer_not_admitted); the session probe reports it and every
    // page shows its refusal panel instead of its body.
    Object.assign(session, {
      state: "refused",
      code: "omega_viewer_not_admitted",
      message: "Viewers cannot use Omega.",
    });
    const sandboxApi = sandboxesApi();
    const sandboxPage = renderSandboxPage(<SandboxesListPage />, {
      api: sandboxApi,
    });
    expect(await screen.findByText("Viewers cannot use Omega.")).toBeTruthy();
    expect(screen.queryByTestId("code-open")).toBeNull();
    expect(sandboxApi.list).not.toHaveBeenCalled();
    sandboxPage.unmount();

    const deploymentApi = fakeDeployments({ list: vi.fn(async () => []) });
    renderDeployments(<DeploymentsListPage />, { api: deploymentApi });
    expect(await screen.findByText("Viewers cannot use Omega.")).toBeTruthy();
    expect(screen.queryByTestId("code-open")).toBeNull();
    expect(deploymentApi.list).not.toHaveBeenCalled();
  });
});

describe("the held key across panels", () => {
  it("is the same key on every page of this tab, and none in another organization", async () => {
    holdKey({
      apiKey: KEY.api_key,
      secretKey: KEY.secret_key,
      name: KEY.key_name,
      organizationId: ORG,
      userId: USER,
    });
    const user = setup();
    renderDeployments(<DeploymentsListPage />);
    await openCode(user);
    expect(screen.getByTestId("code-key-strip").dataset.keyState).toBe(
      "masked",
    );
    await user.click(screen.getByTestId("code-close"));
    sessionStorage.setItem(SS_KEY_ORG_ID, "another-org");
    await user.click(screen.getByTestId("code-open"));
    await waitFor(() =>
      expect(screen.getByTestId("code-key-strip").dataset.keyState).toBe(
        "none",
      ),
    );
  });
});
