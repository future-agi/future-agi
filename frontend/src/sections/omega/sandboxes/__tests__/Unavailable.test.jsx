import React from "react";
import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createOmegaClient } from "src/sections/omega/shared";
import { OmegaError } from "src/sections/omega/shared/omegaStates";

import { createSandboxesApi } from "../api/sandboxesApi";
import { errorCopy } from "../components/SandboxErrorState";
import FilesTab from "../detail/FilesTab";
import {
  CAPS,
  CAPS_V2,
  PROVIDER,
  fakeApi,
  renderSandboxes,
  sandboxView,
} from "./helpers";
import { session, workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));

const { default: SandboxesListPage } = await import("../SandboxesListPage");
const { default: SandboxSettingsPage } = await import(
  "../settings/SandboxSettingsPage"
);

beforeEach(() => {
  Object.assign(session, {
    state: "ready",
    code: null,
    title: null,
    message: null,
    retryAfter: null,
  });
});

const SCHEMA = "omega-workspace-sandboxes/v1";
const OFF_TITLE = "Sandboxes are not available on this deployment.";
const OFF_NEEDS =
  "An administrator enables a sandbox host for this deployment.";
const EE_DEFAULT_REASON = "Sandboxes are not enabled on this deployment.";

/**
 * EE's caps on an install with no sandbox backend (cut 5): nothing on offer,
 * network off only, the built-in running limit, and a create refusal coded
 * `sandboxes_unavailable` with the deployment's reason.
 */
function offCaps(reason = EE_DEFAULT_REASON) {
  return {
    ...CAPS,
    defaultImage: null,
    images: [],
    sizes: [],
    defaultSize: null,
    templates: [],
    defaultTemplate: null,
    customLimits: false,
    limits: {
      default: { cpus: 0.1, memoryMb: 64, pidsLimit: 16 },
      min: { cpus: 0.1, memoryMb: 64, pidsLimit: 16 },
      max: { cpus: 0.1, memoryMb: 64, pidsLimit: 16 },
    },
    networkModes: ["none"],
    defaultNetwork: "none",
    networkUnavailableReason: null,
    networkUnavailableCode: null,
    maxActiveSandboxes: 10,
    settings: { canEdit: false },
    createRefusal: {
      field: "deployment",
      code: "sandboxes_unavailable",
      reason,
    },
  };
}

/** `GET /workspace/sandboxes` there: 200, no sandboxes, no provider. */
const offList = (reason) => ({
  schemaVersion: SCHEMA,
  sandboxes: [],
  caps: offCaps(reason),
});

/** `GET /workspace/sandboxes/settings` there: 200, nothing anyone can save. */
const offSettings = (reason) => ({
  schemaVersion: SCHEMA,
  settings: {},
  operator: {
    templates: [],
    defaultTemplate: null,
    images: [],
    sizes: [],
    defaultSize: null,
    allowNetwork: false,
    maxActivePerWorkspace: 10,
    idleTtlSeconds: 1800,
    customLimits: false,
  },
  canEdit: false,
  caps: offCaps(reason),
});

/** Every other route there: 409 with the same body the old 503 carried. */
const OFF_409_BODY = {
  schemaVersion: SCHEMA,
  error: { code: "sandboxes_unavailable", message: EE_DEFAULT_REASON },
};

const BASE = "/omega/api";
const mintAssertion = vi.fn(async () => ({
  token: "omega_delegate_test.a.b",
  expiresAt: Date.now() + 45_000,
}));
const json = (status, body) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

/** The real client and sandboxes API over a fetch that answers like EE. */
function realApi(answer) {
  const fetchImpl = vi.fn(async (url) => answer(String(url)));
  const { omegaJson, omegaFetch } = createOmegaClient({
    basePath: BASE,
    fetchImpl,
    mintAssertion,
  });
  return { api: createSandboxesApi({ omegaJson, omegaFetch }), fetchImpl };
}

/** Nothing on the page reads as an error, a warning or something to create. */
function expectCalm() {
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByTestId("sandbox-error-state")).toBeNull();
  expect(screen.queryByTestId("sandboxes-create-refusal")).toBeNull();
  expect(screen.queryByTestId("sandbox-settings-refusal")).toBeNull();
  expect(screen.queryByTestId("new-sandbox")).toBeNull();
  expect(screen.queryByTestId("create-sandbox-drawer")).toBeNull();
}

describe("Sandboxes list on a deployment with no sandboxes set up", () => {
  it("renders the unavailable state as the page's normal answer: no New sandbox, no warning, no error", async () => {
    const api = fakeApi({ list: vi.fn(async () => offList()) });
    renderSandboxes(<SandboxesListPage />, { api });
    const state = await screen.findByTestId("sandboxes-unavailable");
    expect(state).toHaveTextContent(OFF_TITLE);
    expect(state).toHaveTextContent(OFF_NEEDS);
    expect(state).not.toHaveTextContent(/Omega|operator|try again/i);
    expect(within(state).queryByRole("button")).toBeNull();
    expectCalm();
    expect(screen.queryByTestId("sandboxes-empty")).toBeNull();
    expect(screen.queryByText("No sandboxes yet")).toBeNull();
    expect(screen.queryByTestId("sandboxes-active-count")).toBeNull();
    expect(screen.queryByTestId("sandbox-settings-link")).toBeNull();
    expect(screen.getByTestId("omega-tab-sandboxes")).toHaveTextContent(
      "Sandboxes",
    );
    expect(api.list).toHaveBeenCalledTimes(1);
  });

  it.each([
    [
      "names a reason about this deployment",
      "Sandboxes need the artifact store and the accounts plane; this deployment has neither wired.",
      "Sandboxes need the artifact store and the accounts plane; this deployment has neither wired.",
    ],
    [
      "never shows a reason meant for the operator",
      "No container binding is configured. An operator sets OMEGA_SANDBOX_BINDING.",
      OFF_NEEDS,
    ],
  ])("%s", async (_name, reason, shown) => {
    const api = fakeApi({ list: vi.fn(async () => offList(reason)) });
    renderSandboxes(<SandboxesListPage />, { api });
    const state = await screen.findByTestId("sandboxes-unavailable");
    expect(state).toHaveTextContent(OFF_TITLE);
    expect(state).toHaveTextContent(shown);
    expect(state).not.toHaveTextContent(/operator|binding|OMEGA_/i);
    expectCalm();
  });

  it("reads EE's real 200 answer, which carries no provider, through the Omega client", async () => {
    const { api, fetchImpl } = realApi((url) => {
      if (url === `${BASE}/workspace/sandboxes`) return json(200, offList());
      throw new TypeError(`Unexpected request to ${url}`);
    });
    renderSandboxes(<SandboxesListPage />, { api });
    const state = await screen.findByTestId("sandboxes-unavailable");
    expect(state).toHaveTextContent(OFF_TITLE);
    expectCalm();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("decides from the refusal's code, never from the running limit or the sizes", async () => {
    const reason = "This workspace offers only XL, and it cannot run here.";
    const api = fakeApi({
      list: vi.fn(async () => ({
        sandboxes: [],
        caps: {
          ...CAPS_V2,
          sizes: [],
          maxActiveSandboxes: 1,
          createRefusal: { field: "size", reason },
        },
        provider: PROVIDER,
      })),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    const refusal = await screen.findByTestId("sandboxes-create-refusal");
    expect(refusal).toHaveTextContent(reason);
    expect(screen.getByTestId("sandboxes-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("sandboxes-unavailable")).toBeNull();
  });

  it("renders the same state for an older EE that answers 503", async () => {
    const api = fakeApi({
      list: vi.fn(async () => {
        throw new OmegaError({
          state: "unavailable",
          status: 503,
          code: "sandboxes_unavailable",
          message: EE_DEFAULT_REASON,
        });
      }),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    const state = await screen.findByTestId("sandboxes-unavailable");
    expect(state).toHaveTextContent(OFF_TITLE);
    expect(state).toHaveTextContent(OFF_NEEDS);
    expectCalm();
  });

  it("still lists the workspace's sandboxes when sandboxes are set up", async () => {
    const api = fakeApi({
      list: vi.fn(async () => ({
        sandboxes: [sandboxView()],
        caps: CAPS,
        provider: PROVIDER,
      })),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    expect(await screen.findAllByTestId("sandbox-row")).toHaveLength(1);
    expect(screen.queryByTestId("sandboxes-unavailable")).toBeNull();
  });
});

describe("Sandbox settings on a deployment with no sandboxes set up", () => {
  it("shows the same unavailable state, with no form, no role notice and no Save", async () => {
    const api = fakeApi({ settings: vi.fn(async () => offSettings()) });
    renderSandboxes(<SandboxSettingsPage />, { api });
    const state = await screen.findByTestId("sandboxes-unavailable");
    expect(state).toHaveTextContent(OFF_TITLE);
    expect(state).toHaveTextContent(OFF_NEEDS);
    expect(screen.queryByTestId("sandbox-settings")).toBeNull();
    expect(screen.queryByTestId("sandbox-settings-readonly")).toBeNull();
    expect(screen.queryByText(/Only workspace owners and admins/)).toBeNull();
    expect(screen.queryByRole("button", { name: /save/i })).toBeNull();
    expectCalm();
  });

  it("shows the same state for an older EE that answers 503", async () => {
    const api = fakeApi({
      settings: vi.fn(async () => {
        throw new OmegaError({
          state: "unavailable",
          status: 503,
          code: "sandboxes_unavailable",
          message: EE_DEFAULT_REASON,
        });
      }),
    });
    renderSandboxes(<SandboxSettingsPage />, { api });
    const state = await screen.findByTestId("sandboxes-unavailable");
    expect(state).toHaveTextContent(OFF_TITLE);
    expect(screen.queryByTestId("sandbox-settings")).toBeNull();
    expectCalm();
  });

  it("keeps the form when sandboxes are set up", async () => {
    renderSandboxes(<SandboxSettingsPage />);
    expect(await screen.findByTestId("sandbox-settings")).toBeInTheDocument();
    expect(screen.queryByTestId("sandboxes-unavailable")).toBeNull();
  });
});

describe("Files tab when EE answers 409 sandboxes_unavailable", () => {
  it("says sandboxes are not available on this deployment, with nothing to retry", async () => {
    const sandbox = sandboxView();
    const files = `${BASE}/workspace/sandboxes/${sandbox.id}/files`;
    const { api } = realApi((url) => {
      if (url.startsWith(files)) return json(409, OFF_409_BODY);
      throw new TypeError(`Unexpected request to ${url}`);
    });
    renderSandboxes(<FilesTab sandbox={sandbox} />, { api });
    const list = await screen.findByTestId("files-list");
    const state = await within(list).findByTestId("sandbox-error-state");
    expect(state).toHaveTextContent(OFF_TITLE);
    expect(state).toHaveTextContent(OFF_NEEDS);
    expect(state).not.toHaveTextContent(
      /Refresh in a moment|right now|Try again|Omega/,
    );
    expect(within(state).queryByRole("button")).toBeNull();
  });

  it("words the answer by its code, whatever state or status it arrives with", () => {
    for (const [state, status] of [
      ["error", 409],
      ["unavailable", 409],
      ["unavailable", 503],
    ]) {
      const copy = errorCopy({
        state,
        status,
        code: "sandboxes_unavailable",
        message: EE_DEFAULT_REASON,
      });
      expect(copy).toMatchObject({
        title: OFF_TITLE,
        description: OFF_NEEDS,
      });
      expect(copy.retry).toBeFalsy();
      expect(copy.reload).toBeFalsy();
    }
  });
});

describe("Sandboxes list after a failed sign-in", () => {
  it("says to try again in a moment and to contact support, not to reload, when a fresh key was refused too", async () => {
    const { api, fetchImpl } = realApi((url) => {
      if (url === `${BASE}/workspace/sandboxes`)
        return json(401, { error: "assertion_replayed", retryable: true });
      throw new TypeError(`Unexpected request to ${url}`);
    });
    renderSandboxes(<SandboxesListPage />, { api });
    const state = await screen.findByTestId(
      "sandbox-error-state",
      {},
      { timeout: 5000 },
    );
    expect(state).toHaveTextContent("Omega couldn't confirm your sign-in");
    expect(state).toHaveTextContent(
      "Future AGI couldn't confirm your sign-in with Omega. Try again in a moment. If it keeps happening, contact Future AGI support.",
    );
    expect(state).not.toHaveTextContent("Reload the page");
    // Each read already sent a fresh key once before giving up.
    expect(fetchImpl.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});
