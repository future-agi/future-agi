import React from "react";
import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_STATE, OmegaError } from "src/sections/omega/shared/omegaStates";

import {
  CAPS,
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
// Which Future AGI this is: hosted ("cloud") or a self-hosted install.
const capabilities = vi.hoisted(() => ({ data: null }));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useCapabilities: () => ({
    data: capabilities.data,
    isLoading: false,
    isError: false,
  }),
}));

const { default: SandboxDetailPage } = await import("../SandboxDetailPage");
const ID = "sbx_0123456789abcdef01234567";
const at = (tab) => `/dashboard/sandboxes/${ID}${tab ? `/${tab}` : ""}`;

beforeEach(() => {
  Object.assign(session, { state: "ready", code: null, message: null });
  capabilities.data = { display_mode: "oss", features: {} };
});

/** An exec that the test drives: `events` feeds the stream, `end` closes it. */
function controlledExec() {
  const handle = { events: null, end: null };
  const exec = vi.fn((_id, _input, options) => {
    handle.events = options.onEvent;
    return {
      close: vi.fn(),
      done: new Promise((resolve) => {
        handle.end = resolve;
      }),
    };
  });
  return { exec, handle };
}

/** A files route whose top level holds these folders. */
const folders = (...names) =>
  vi.fn(async (_id, path) => ({
    path,
    entries:
      path === ""
        ? names.map((name) => ({
            name,
            size: 4096,
            mode: "755",
            directory: true,
          }))
        : [],
  }));

async function openRun(api) {
  const view = renderSandboxes(<SandboxDetailPage />, {
    api,
    route: at("run"),
  });
  await screen.findByTestId("run-command");
  // The list (and its caps) has loaded once the summary names the time limit.
  await waitFor(() =>
    expect(screen.getByTestId("run-options-summary")).toHaveTextContent(
      "a stop will be requested after 1 min",
    ),
  );
  return view;
}

const type = (testId, value, index = 0) =>
  fireEvent.change(screen.getAllByTestId(testId)[index], {
    target: { value },
  });

describe("Run tab command time limit", () => {
  it("says how long a command can run here, from the caps", async () => {
    await openRun(fakeApi());
    const limit = screen.getByTestId("run-command-limit");
    expect(limit.textContent).toBe(
      "Commands can run for up to 15 minutes here.",
    );
  });

  it("follows the caps' maximum, not a fixed number", async () => {
    const api = fakeApi({
      list: vi.fn(async () => ({
        sandboxes: [sandboxView()],
        caps: { ...CAPS, exec: { ...CAPS.exec, maxTimeoutSeconds: 3600 } },
        provider: PROVIDER,
      })),
    });
    await openRun(api);
    expect(screen.getByTestId("run-command-limit").textContent).toBe(
      "Commands can run for up to 1 hour here.",
    );
  });

  it("adds one calm line on hosted Future AGI about self-hosted installs", async () => {
    capabilities.data = { display_mode: "cloud", features: {} };
    await openRun(fakeApi());
    expect(screen.getByTestId("run-command-limit").textContent).toBe(
      "Commands can run for up to 15 minutes here. Self-hosted installs can allow longer commands.",
    );
  });
});

describe("Run tab options", () => {
  it("sends the time limit it shows, by default EE's own default", async () => {
    const { exec } = controlledExec();
    const api = fakeApi({ exec });
    await openRun(api);
    expect(screen.getByTestId("run-options-summary")).toHaveTextContent(
      "Runs in /workspace · a stop will be requested after 1 min (the defaults)",
    );
    type("run-command", "echo hi");
    fireEvent.click(screen.getByTestId("run-submit"));
    expect(exec).toHaveBeenCalledWith(
      ID,
      { command: "echo hi", timeoutSeconds: 60 },
      expect.any(Object),
    );
  });

  it("runs in a folder with variables and a longer limit, up to EE's 15 minutes", async () => {
    const { exec } = controlledExec();
    const api = fakeApi({ exec, listFiles: folders("app") });
    await openRun(api);
    const toggle = screen.getByTestId("run-options-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).not.toHaveAttribute("aria-controls");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const panel = screen.getByTestId("run-options");
    expect(toggle).toHaveAttribute("aria-controls", panel.id);

    type("run-cwd", "/workspace/app/");
    fireEvent.mouseDown(within(panel).getByRole("combobox"));
    const choices = screen.getAllByRole("option").map((o) => o.textContent);
    expect(choices).toEqual([
      "30 s",
      "1 min (default)",
      "2 min",
      "5 min",
      "10 min",
      "15 min",
    ]);
    fireEvent.click(screen.getByRole("option", { name: "15 min" }));
    fireEvent.click(screen.getByTestId("run-env-add"));
    fireEvent.click(screen.getByTestId("run-env-add"));
    type("run-env-key", "NODE_ENV", 0);
    type("run-env-value", "test", 0);
    type("run-env-key", "GREETING", 1);
    type("run-env-value", "hello world", 1);
    expect(screen.getByTestId("run-options-summary")).toHaveTextContent(
      "Runs in /workspace/app · a stop will be requested after 15 min · 2 variables",
    );

    type("run-command", "npm test");
    fireEvent.click(screen.getByTestId("run-submit"));
    // The folder is looked up first, then the command goes out.
    await waitFor(() =>
      expect(exec).toHaveBeenCalledWith(
        ID,
        {
          command: "npm test",
          cwd: "app",
          env: { NODE_ENV: "test", GREETING: "hello world" },
          timeoutSeconds: 900,
        },
        expect.any(Object),
      ),
    );
    expect(api.listFiles).toHaveBeenCalledWith(ID, "");
    expect(screen.getByTestId("run-header")).toHaveTextContent(
      "/workspace/app $ npm test",
    );
    // A running command's options cannot change under it.
    expect(screen.getByTestId("run-cwd")).toBeDisabled();
  });

  it("refuses a folder outside /workspace and a reserved variable before anything is sent", async () => {
    const { exec } = controlledExec();
    const api = fakeApi({ exec });
    await openRun(api);
    fireEvent.click(screen.getByTestId("run-options-toggle"));
    type("run-cwd", "../etc");
    expect(
      screen.getByText("Use a folder inside /workspace, without . or .."),
    ).toBeInTheDocument();
    type("run-command", "cat passwd");
    expect(screen.getByTestId("run-submit")).toBeDisabled();
    expect(screen.getByTestId("run-options-summary")).toHaveTextContent(
      "Fix the highlighted option to run a command.",
    );
    // Enter in the command box does not slip past the check either.
    fireEvent.keyDown(screen.getByTestId("run-command"), { key: "Enter" });
    expect(exec).not.toHaveBeenCalled();

    type("run-cwd", "");
    fireEvent.click(screen.getByTestId("run-env-add"));
    type("run-env-key", "OMEGA_EXEC_ID");
    expect(
      screen.getByText("OMEGA_ names are set by the sandbox itself"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("run-submit")).toBeDisabled();
    fireEvent.click(screen.getByTestId("run-env-remove"));
    expect(screen.getByTestId("run-submit")).not.toBeDisabled();
    fireEvent.click(screen.getByTestId("run-submit"));
    expect(exec).toHaveBeenCalledWith(
      ID,
      { command: "cat passwd", timeoutSeconds: 60 },
      expect.any(Object),
    );
  });

  it("says a stop was requested after its time limit and where to give it longer", async () => {
    const { exec, handle } = controlledExec();
    const api = fakeApi({ exec });
    await openRun(api);
    type("run-command", "sleep 75");
    fireEvent.click(screen.getByTestId("run-submit"));
    act(() =>
      handle.events({
        event: "started",
        json: { execId: "exec_aaaaaaaaaaaa" },
      }),
    );
    act(() =>
      handle.events({
        event: "exit",
        json: { exitCode: 143, timedOut: true, durationMs: 61_000 },
      }),
    );
    expect(screen.getByTestId("run-status")).toHaveTextContent(
      "Timed out · 61 s",
    );
    expect(screen.getByTestId("run-timeout-note")).toHaveTextContent(
      "Stop requested after 1 min. Choose a longer limit in Options (up to 15 min) and run it again.",
    );
  });

  it("says output past the cap was dropped without naming the provider", async () => {
    const { exec, handle } = controlledExec();
    const api = fakeApi({ exec });
    await openRun(api);
    type("run-command", "yes | head -c 2000000");
    fireEvent.click(screen.getByTestId("run-submit"));
    act(() =>
      handle.events({
        event: "exit",
        json: { exitCode: 0, truncated: true, durationMs: 900 },
      }),
    );
    expect(screen.getByTestId("run-truncated-note")).toHaveTextContent(
      "Output past the 1 MB limit was not kept. Reaching the output limit does not stop the command.",
    );
    expect(document.body.textContent).not.toMatch(/provider/i);
  });

  it("shows EE's reason when it refuses a command before it starts", async () => {
    const api = fakeApi({
      exec: vi.fn(() => ({
        close: vi.fn(),
        done: Promise.resolve({
          ok: false,
          message: '"timeoutSeconds" must be an integer between 1 and 900.',
        }),
      })),
    });
    await openRun(api);
    type("run-command", "ls");
    fireEvent.click(screen.getByTestId("run-submit"));
    // The whole reason sits in an alert under the header, which says only
    // "Failed", so a long reason wraps instead of being cut off.
    const reason = await screen.findByTestId("run-error");
    expect(reason).toHaveAttribute("role", "alert");
    expect(reason).toHaveTextContent(
      '"timeoutSeconds" must be an integer between 1 and 900.',
    );
    const status = screen.getByTestId("run-status");
    expect(status).toHaveTextContent(/^Failed$/);
    expect(status).toHaveAttribute("role", "status");
  });

  it("shows a stream that ended before the command finished as Failed, with the reason in full", async () => {
    const { exec, handle } = controlledExec();
    const api = fakeApi({ exec });
    await openRun(api);
    type("run-command", "sleep 5");
    fireEvent.click(screen.getByTestId("run-submit"));
    expect(screen.getByTestId("run-status")).toHaveTextContent("Starting…");
    expect(screen.queryByTestId("run-error")).toBeNull();
    await act(async () => handle.end({ ended: true }));
    expect(screen.getByTestId("run-status")).toHaveTextContent(/^Failed$/);
    expect(screen.getByTestId("run-error")).toHaveTextContent(
      "The command stream ended before the command finished.",
    );
  });

  it("offers no time limit when EE states none, and sends none", async () => {
    const { exec } = controlledExec();
    const base = fakeApi();
    const listed = await base.list();
    const { exec: _drop, ...caps } = listed.caps;
    const api = fakeApi({
      exec,
      list: vi.fn(async () => ({ ...listed, caps })),
    });
    renderSandboxes(<SandboxDetailPage />, { api, route: at("run") });
    await screen.findByTestId("run-command");
    await waitFor(() => expect(api.list).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("run-options-toggle"));
    expect(screen.queryByTestId("run-timeout")).toBeNull();
    type("run-command", "ls");
    fireEvent.click(screen.getByTestId("run-submit"));
    expect(exec).toHaveBeenCalledWith(
      ID,
      { command: "ls" },
      expect.any(Object),
    );
  });
});

describe("Run tab states", () => {
  it("says a sandbox that is neither running nor hibernated cannot take commands, without promising files", async () => {
    const api = fakeApi({
      get: vi.fn(async () => sandboxView({ state: "recovery_required" })),
    });
    renderSandboxes(<SandboxDetailPage />, { api, route: at("run") });
    expect(
      await screen.findByText("This sandbox isn't running"),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Files are kept/);
    expect(screen.queryByTestId("run-command")).toBeNull();
  });
});

describe("Run tab before the caps load", () => {
  it("keeps the user's folder and still sends EE's default limit once the caps arrive", async () => {
    const { exec } = controlledExec();
    let releaseList;
    const base = fakeApi();
    const listed = await base.list();
    const api = fakeApi({
      exec,
      listFiles: folders("app"),
      list: vi.fn(
        () =>
          new Promise((resolve) => {
            releaseList = () => resolve(listed);
          }),
      ),
    });
    renderSandboxes(<SandboxDetailPage />, { api, route: at("run") });
    await screen.findByTestId("run-command");
    fireEvent.click(screen.getByTestId("run-options-toggle"));
    type("run-cwd", "app");
    expect(screen.queryByTestId("run-timeout")).toBeNull();
    await act(async () => releaseList());
    await waitFor(() =>
      expect(screen.getByTestId("run-options-summary")).toHaveTextContent(
        "Runs in /workspace/app · a stop will be requested after 1 min",
      ),
    );
    type("run-command", "ls");
    fireEvent.click(screen.getByTestId("run-submit"));
    await waitFor(() =>
      expect(exec).toHaveBeenCalledWith(
        ID,
        { command: "ls", cwd: "app", timeoutSeconds: 60 },
        expect.any(Object),
      ),
    );
  });
});

describe("Run tab working folder", () => {
  it("looks for the folder first and says it is missing instead of a runtime error", async () => {
    const { exec } = controlledExec();
    // EE lists a missing path as empty, so the parent's listing decides.
    const top = ["app", "nope"];
    const listFiles = vi.fn(async (_id, path) => ({
      path,
      entries: (path === "" ? top : path === "nope" ? [] : []).map((name) => ({
        name,
        size: 4096,
        mode: "755",
        directory: true,
      })),
    }));
    const api = fakeApi({ exec, listFiles });
    await openRun(api);
    fireEvent.click(screen.getByTestId("run-options-toggle"));
    type("run-cwd", "nope/here");
    type("run-command", "ls");
    fireEvent.click(screen.getByTestId("run-submit"));
    expect(
      await screen.findByText(
        "No folder /workspace/nope/here in this sandbox. Make it first, for example with mkdir -p nope/here",
      ),
    ).toBeInTheDocument();
    expect(listFiles).toHaveBeenCalledWith(ID, "nope");
    expect(exec).not.toHaveBeenCalled();
    // The note never locks Run: the next Run looks again.
    expect(screen.getByTestId("run-submit")).not.toBeDisabled();
    fireEvent.click(screen.getByTestId("run-submit"));
    await waitFor(() => expect(listFiles).toHaveBeenCalledTimes(2));
    expect(exec).not.toHaveBeenCalled();
    // The user makes the folder elsewhere (a command, the Files tab); the same
    // folder then runs, and the note goes away.
    listFiles.mockImplementation(async (_id, path) => ({
      path,
      entries:
        path === "nope"
          ? [{ name: "here", size: 4096, mode: "755", directory: true }]
          : [],
    }));
    fireEvent.click(screen.getByTestId("run-submit"));
    await waitFor(() =>
      expect(exec).toHaveBeenCalledWith(
        ID,
        { command: "ls", cwd: "nope/here", timeoutSeconds: 60 },
        expect.any(Object),
      ),
    );
    expect(screen.queryByText(/No folder \/workspace\/nope\/here/)).toBeNull();
  });

  it("leaves the command to EE when the folder check itself fails", async () => {
    const { exec } = controlledExec();
    const api = fakeApi({
      exec,
      listFiles: vi.fn(async () => {
        throw new OmegaError({ state: OMEGA_STATE.UNAVAILABLE, status: 503 });
      }),
    });
    await openRun(api);
    fireEvent.click(screen.getByTestId("run-options-toggle"));
    type("run-cwd", "app");
    type("run-command", "ls");
    fireEvent.click(screen.getByTestId("run-submit"));
    await waitFor(() =>
      expect(exec).toHaveBeenCalledWith(
        ID,
        { command: "ls", cwd: "app", timeoutSeconds: 60 },
        expect.any(Object),
      ),
    );
  });
});
