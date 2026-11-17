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

import { SENTRY_REPLAY_BLOCK_SELECTORS } from "src/sections/omega/embed/telemetry/sdk-guards";
import { SS_KEY_ORG_ID, SS_KEY_WORKSPACE_ID } from "src/utils/sessionKeys";

import fixture from "../../deployments/__tests__/fixtures/serve-fixture.json";
import {
  ID,
  detailView,
  fakeApi as fakeDeployments,
  renderDeployments,
  revisionView,
} from "../../deployments/__tests__/helpers";
import { session, workspace } from "../../deployments/__tests__/session-mock";
import { forgetKey, holdKey } from "../keyStore";

// What fix round 1 of P11 closed, on the real pages: a member is shown no
// write EE refuses them; a keyed snippet's Idempotency-Key follows the request
// itself, so a re-read deployment never sends an old key with a new rollback;
// a revealed key sits only inside elements no session recorder captures; and
// Copy works, or says it did not, without the async Clipboard API.

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

const { default: DeploymentDetailPage } = await import(
  "../../deployments/DeploymentDetailPage"
);
const { default: DeploymentNewPage } = await import(
  "../../deployments/DeploymentNewPage"
);
const { default: DeploymentsListPage } = await import(
  "../../deployments/DeploymentsListPage"
);

const ORG = "22222222-2222-4222-8222-222222222222";
const WS = "33333333-3333-4333-8333-333333333333";
const USER = "44444444-4444-4444-8444-444444444444";
const KEY = {
  name: "Code snippets 2026-10-22 13:30:00",
  apiKey: "a7a7a7a7a7a7a7a7a7a7a7a7a7a7qrst",
  secretKey: "b8b8b8b8b8b8b8b8b8b8b8b8b8b8uvwx",
};
const READS = [
  "Read this deployment",
  "Check its health",
  "Build log of revision 2 (streamed)",
];

const originalExecCommand = document.execCommand;
beforeEach(() => {
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  auth.role = "Owner";
  auth.user = { id: USER };
  Object.assign(session, { state: "ready", code: null, message: null });
  sessionStorage.setItem(SS_KEY_ORG_ID, ORG);
  sessionStorage.setItem(SS_KEY_WORKSPACE_ID, WS);
  forgetKey();
});
afterEach(() => {
  vi.unstubAllEnvs();
  sessionStorage.clear();
  forgetKey();
  document.execCommand = originalExecCommand;
});

const snippet = () => screen.getByTestId("code-snippet").textContent;

async function openCode() {
  fireEvent.click(await screen.findByTestId("code-open"));
  return screen.findByTestId("code-drawer");
}

async function callChoices() {
  fireEvent.mouseDown(
    within(screen.getByTestId("code-panel")).getByRole("combobox"),
  );
  const labels = (await screen.findAllByRole("option")).map(
    (option) => option.textContent,
  );
  fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
  return labels;
}

function renderDetail(api, tab = "revisions") {
  return renderDeployments(<DeploymentDetailPage />, {
    route: `/dashboard/deployments/${ID}/${tab}`,
    api,
  });
}

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

describe("someone EE refuses every deployment write (not a workspace owner or admin)", () => {
  // EE 2be31e97: deploy, rollback, access-token and delete call
  // context.manage(...), MANAGING_ROLES = {owner, admin}, else 403
  // role_forbidden. Future AGI makes only an organization Owner or Admin one
  // of those; an organization Member who administers a workspace is still a
  // member (accounts/omega_session.py delegation_role).
  it.each(["Member", "workspace_admin", undefined])(
    "role %s: the deployment's Code drawer lists reads only, on the tab that leads with a rollback",
    async (role) => {
      auth.role = role;
      renderDetail(fakeDeployments({ get: vi.fn(async () => detailView()) }));
      await openCode();
      expect(screen.getByTestId("code-snippet").dataset.requestId).toBe(
        "deployments.get",
      );
      expect(await callChoices()).toEqual(READS);
      expect(snippet()).not.toMatch(/-X (POST|DELETE)/);
    },
  );

  it("role Member: the wizard's Show as code shows no deploy request, and says who can deploy", async () => {
    auth.role = "Member";
    renderDeployments(<DeploymentNewPage />, {
      api: fakeDeployments({ list: vi.fn(async () => []) }),
      route: "/dashboard/deployments/new",
    });
    fireEvent.click(await screen.findByTestId("show-as-code"));
    fireEvent.change(await screen.findByTestId("folder-input"), {
      target: { files: folderFiles() },
    });
    await screen.findByTestId("bundle-summary");
    // A project and valid settings: an owner would see the deploy request now.
    expect(screen.getByTestId("show-as-code-blocked").textContent).toMatch(
      /^Only workspace owners and admins can deploy/,
    );
    expect(screen.queryByTestId("code-snippet")).toBeNull();
  });

  it("role Admin: every write the page offers is listed (EE admits an admin)", async () => {
    auth.role = "Admin";
    renderDetail(fakeDeployments({ get: vi.fn(async () => detailView()) }));
    await openCode();
    expect(screen.getByTestId("code-snippet").dataset.requestId).toBe(
      "deployments.rollback",
    );
    expect(await callChoices()).toEqual([
      "Roll back to revision 1",
      ...READS,
      "Mint a new access token (the old one stops working)",
      "Delete this deployment",
    ]);
  });
});

describe("one Idempotency-Key per distinct request while the drawer is open", () => {
  const keyOf = () =>
    snippet().match(/Idempotency-Key: (fa-dep-rollback-[^']+)'/)[1];
  // The page's own deployment reads (deploymentKeys), not the shell's.
  const DEPLOYMENT_QUERIES = ["omega-deployments"];

  it("sends a new key when a re-read turns the rollback into another one, and the same key for the same request", async () => {
    // Revision 2 live: roll back to 1. Then revision 1 live (the rollback ran
    // from a terminal; the page polls or refetches on focus): roll back to 2.
    const rolledBack = detailView({
      liveRevision: 1,
      revisions: [revisionView(2), revisionView(1, { status: "live" })],
    });
    const get = vi
      .fn()
      .mockResolvedValueOnce(detailView())
      .mockResolvedValueOnce(rolledBack)
      .mockResolvedValueOnce(rolledBack)
      .mockResolvedValue(detailView());
    const { queryClient } = renderDetail(fakeDeployments({ get }));
    await openCode();
    expect(snippet()).toContain(`/${ID}/revisions/1/rollback'`);
    expect(snippet()).toContain(`--data-binary '{"expectedRevision":2}'`);
    const first = keyOf();

    const reread = async (path) => {
      await act(() =>
        queryClient.refetchQueries({ queryKey: DEPLOYMENT_QUERIES }),
      );
      await waitFor(() => expect(snippet()).toContain(path));
    };
    await reread(`/${ID}/revisions/2/rollback'`);
    expect(snippet()).toContain(`--data-binary '{"expectedRevision":1}'`);
    const second = keyOf();
    expect(second).not.toBe(first);

    // Re-read, unchanged: still the same request, so still its key.
    await act(() =>
      queryClient.refetchQueries({ queryKey: DEPLOYMENT_QUERIES }),
    );
    await waitFor(() => expect(get).toHaveBeenCalledTimes(3));
    expect(keyOf()).toBe(second);

    // Back to the first request: its own key again (EE replays it).
    await reread(`/${ID}/revisions/1/rollback'`);
    expect(keyOf()).toBe(first);
  });
});

/** Every text node or attribute in the document that holds `value`. */
function holders(value) {
  const found = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.nodeValue.includes(value)) found.push(node.parentElement);
  }
  for (const element of document.body.querySelectorAll("*")) {
    for (const attribute of element.attributes) {
      if (attribute.value.includes(value)) found.push(element);
    }
  }
  return found;
}

describe("a revealed key is never session-recorded", () => {
  it("sits only inside elements PostHog, rrweb and Sentry replay skip", async () => {
    holdKey({ ...KEY, organizationId: ORG, userId: USER });
    renderDeployments(<DeploymentsListPage />);
    await openCode();
    expect(
      screen.getByTestId("code-key-strip").closest(".ph-no-capture.rr-block"),
    ).not.toBeNull();
    fireEvent.click(screen.getByTestId("code-reveal-key"));
    expect(snippet()).toContain(`X-Secret-Key: ${KEY.secretKey}`);

    const blocked = SENTRY_REPLAY_BLOCK_SELECTORS.join(",");
    for (const value of [KEY.secretKey, KEY.apiKey]) {
      const where = holders(value);
      expect(where.length, value).toBeGreaterThan(0);
      for (const element of where) {
        expect(element.closest(".ph-no-capture.rr-block")).not.toBeNull();
        expect(element.closest(blocked)).not.toBeNull();
      }
    }
  });
});

describe("Copy without the async Clipboard API (plain http on a LAN address)", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
  });

  it("copies through a hidden textarea inside the drawer, kept out of recordings, and says Copied", async () => {
    const seen = [];
    document.execCommand = vi.fn((command) => {
      const area = document.activeElement;
      seen.push({
        command,
        value: area?.value,
        selected: area?.value?.slice(area.selectionStart, area.selectionEnd),
        recorded: !area?.closest(".ph-no-capture.rr-block"),
        inDrawer: Boolean(area?.closest("[data-testid=code-drawer]")),
      });
      return true;
    });
    renderDeployments(<DeploymentsListPage />);
    await openCode();
    const copy = screen.getByTestId("code-copy");
    copy.focus();
    fireEvent.click(copy);
    await waitFor(() => expect(copy.textContent).toBe("Copied"));
    expect(seen).toEqual([
      {
        command: "copy",
        value: snippet(),
        selected: snippet(),
        recorded: false,
        inDrawer: true,
      },
    ]);
    expect(document.querySelector("textarea[aria-hidden=true]")).toBeNull();
    expect(document.activeElement).toBe(copy);
    expect(screen.queryByTestId("code-copy-failed")).toBeNull();
  });

  it("falls back the same way when the Clipboard API refuses", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn(async () => Promise.reject(new Error("no"))) },
    });
    document.execCommand = vi.fn(() => true);
    renderDeployments(<DeploymentsListPage />);
    await openCode();
    fireEvent.click(screen.getByTestId("code-copy"));
    await waitFor(() =>
      expect(screen.getByTestId("code-copy").textContent).toBe("Copied"),
    );
    expect(navigator.clipboard.writeText).toHaveBeenCalledTimes(1);
    expect(document.execCommand).toHaveBeenCalledWith("copy");
  });

  it.each([
    ["copies nothing", () => false],
    ["has no copy command", undefined],
  ])("says so, and keeps Copy, when the browser %s", async (_, execCommand) => {
    document.execCommand = execCommand;
    renderDeployments(<DeploymentsListPage />);
    await openCode();
    fireEvent.click(screen.getByTestId("code-copy"));
    const failed = await screen.findByTestId("code-copy-failed");
    expect(failed.getAttribute("role")).toBe("alert");
    expect(failed.textContent).toMatch(/did not let the page copy/);
    // No key held: the command on screen reads the shell, and runs as selected.
    expect(failed.textContent).toMatch(/Select the command above/);
    expect(failed.textContent).not.toMatch(/masked/);
    expect(screen.getByTestId("code-copy").textContent).toBe("Copy");
    // A later copy that works clears it.
    document.execCommand = vi.fn(() => true);
    fireEvent.click(screen.getByTestId("code-copy"));
    await waitFor(() =>
      expect(screen.queryByTestId("code-copy-failed")).toBeNull(),
    );
    expect(screen.getByTestId("code-copy").textContent).toBe("Copied");
  });

  it("tells someone whose key is masked on screen to reveal it first: the masked command would not run (fix round 2)", async () => {
    document.execCommand = () => false;
    holdKey({ ...KEY, organizationId: ORG, userId: USER });
    renderDeployments(<DeploymentsListPage />);
    await openCode();
    expect(snippet()).toContain(
      "X-Secret-Key: \u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022'",
    );
    fireEvent.click(screen.getByTestId("code-copy"));
    const failed = await screen.findByTestId("code-copy-failed");
    expect(failed.getAttribute("role")).toBe("alert");
    expect(failed.textContent).toMatch(/shows your key masked/);
    expect(failed.textContent).toMatch(
      /Reveal the key, then select the command/,
    );
    // Revealed, the command on screen is the one that runs.
    fireEvent.click(screen.getByTestId("code-reveal-key"));
    expect(snippet()).toContain(`X-Secret-Key: ${KEY.secretKey}`);
    expect(screen.getByTestId("code-copy-failed").textContent).toMatch(
      /Select the command above/,
    );
    expect(screen.getByTestId("code-copy-failed").textContent).not.toMatch(
      /masked/,
    );
  });

  it("still writes through the Clipboard API where there is one", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    document.execCommand = vi.fn(() => true);
    renderDeployments(<DeploymentsListPage />);
    fireEvent.click(await screen.findByTestId("code-open"));
    await user.click(await screen.findByTestId("code-copy"));
    expect(writeText).toHaveBeenCalledWith(snippet());
    expect(document.execCommand).not.toHaveBeenCalled();
  });
});
