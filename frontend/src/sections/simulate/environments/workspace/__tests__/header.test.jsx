import { useRef, useState } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import WorkspaceHeader from "../WorkspaceHeader";
import LivePill from "../LivePill";
import EnvVersionPin from "../EnvVersionPin";
import SystemBanners from "../SystemBanners";

const ENV = {
  id: "env-1",
  name: "Refund Copilot",
  tagline: "Handles refund conversations",
  surface: "voice",
  buildStatus: "ready",
};

// WorkspaceHeader now uses a react-query mutation (§2 delete), so wrap in a client.
const withRouter = (ui) => (
  <QueryClientProvider client={new QueryClient()}>
    <MemoryRouter>{ui}</MemoryRouter>
  </QueryClientProvider>
);

describe("LivePill", () => {
  it("shows Live for a ready environment", () => {
    render(<LivePill env={{ buildStatus: "ready" }} />);
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.queryByText("Building")).toBeNull();
  });

  it("shows Building while the environment is still deriving", () => {
    render(<LivePill env={{ buildStatus: "building" }} />);
    expect(screen.getByText("Building")).toBeInTheDocument();
    expect(screen.queryByText("Live")).toBeNull();
  });

  it("honours an explicit building override", () => {
    render(<LivePill env={{ buildStatus: "ready" }} building />);
    expect(screen.getByText("Building")).toBeInTheDocument();
  });

  it("shows a static Failed for a terminal-failed build (not Building/Live)", () => {
    render(<LivePill env={{ buildStatus: "failed" }} />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.queryByText("Live")).toBeNull();
    expect(screen.queryByText("Building")).toBeNull();
  });
});

describe("EnvVersionPin", () => {
  it("is read-only for a seeded template — no chevron, clicking opens nothing", async () => {
    const user = userEvent.setup();
    const patch = vi.fn();
    render(
      <EnvVersionPin env={ENV} envState={{ scenarios: [1, 2, 3] }} patch={patch} readOnly />
    );

    expect(screen.queryByTestId("env-version-chevron")).toBeNull();
    expect(screen.queryByRole("button", { name: /env v3/i })).toBeNull();

    await user.click(screen.getByText(/env v3/i));
    expect(screen.queryByText("Environment versions")).toBeNull();
    expect(patch).not.toHaveBeenCalled();
  });

  it("opens a menu of every version and switches on pick", async () => {
    const user = userEvent.setup();
    const patch = vi.fn();
    render(<EnvVersionPin env={ENV} envState={{ scenarios: [1, 2, 3] }} patch={patch} />);

    await user.click(screen.getByRole("button", { name: /env v3/i }));

    expect(screen.getByText("Environment versions")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^v3/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^v2/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^v1/ }));
    expect(patch).toHaveBeenCalledWith({ activeEnvVersion: "v1" });
  });
});

describe("off-latest flow", () => {
  it("shows the switch-back banner once an older version is pinned", async () => {
    const user = userEvent.setup();

    function Harness() {
      const [envState, setEnvState] = useState({ scenarios: [1, 2, 3] });
      const patchRef = useRef(null);
      if (!patchRef.current) {
        patchRef.current = vi.fn((p) => setEnvState((s) => ({ ...s, ...p })));
      }
      const patch = patchRef.current;
      Harness.patch = patch;
      return (
        <>
          <EnvVersionPin env={ENV} envState={envState} patch={patch} />
          <SystemBanners env={ENV} envState={envState} patch={patch} />
        </>
      );
    }

    render(<Harness />);
    expect(screen.queryByText(/Switch to v3/)).toBeNull();

    await user.click(screen.getByRole("button", { name: /env v3/i }));
    await user.click(screen.getByRole("button", { name: /^v1/ }));

    expect(Harness.patch).toHaveBeenCalledWith({ activeEnvVersion: "v1" });
    expect(await screen.findByText(/Switch to v3/)).toBeInTheDocument();
    expect(screen.getByText(/Editing off v1/)).toBeInTheDocument();
  });
});

describe("SystemBanners", () => {
  it("renders the building banner from the env build status", () => {
    render(
      <SystemBanners
        env={{ ...ENV, buildStatus: "building", buildProgress: { done: 5, total: 7 } }}
        envState={{ scenarios: [1, 2, 3] }}
        patch={vi.fn()}
      />
    );
    expect(screen.getByText("Environment is still being built")).toBeInTheDocument();
    expect(screen.getByText(/5 of 7 steps done/)).toBeInTheDocument();
  });

  it("renders nothing on a latest, ready environment", () => {
    const { container } = render(
      <SystemBanners env={ENV} envState={{ scenarios: [1, 2, 3] }} patch={vi.fn()} />
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("WorkspaceHeader", () => {
  const baseProps = {
    env: ENV,
    envState: { scenarios: [1, 2, 3], evals: [{ id: "e1" }] },
    patch: vi.fn(),
    canRun: true,
    runBlockedReason: "",
    onFork: vi.fn(),
  };

  it("hides the overflow menu when locked", () => {
    render(withRouter(<WorkspaceHeader {...baseProps} locked />));
    expect(screen.queryByRole("button", { name: "More actions" })).toBeNull();
  });

  it("does not render the mock env-version pin (hidden until the contract has a real version)", () => {
    render(withRouter(<WorkspaceHeader {...baseProps} />));
    expect(screen.queryByText(/env v3/i)).toBeNull();
  });

  it("shows the rename pencil for a backend-backed env and opens the dialog", async () => {
    const user = userEvent.setup();
    render(withRouter(<WorkspaceHeader {...baseProps} backed />));

    const pencil = screen.getByRole("button", { name: "Rename environment" });
    expect(pencil).toBeInTheDocument();
    await user.click(pencil);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("hides the rename pencil for a non-backed env (no §8 row to PATCH)", () => {
    render(withRouter(<WorkspaceHeader {...baseProps} />));
    expect(screen.queryByRole("button", { name: "Rename environment" })).toBeNull();
  });

  it("hides the rename pencil when locked", () => {
    render(withRouter(<WorkspaceHeader {...baseProps} backed locked />));
    expect(screen.queryByRole("button", { name: "Rename environment" })).toBeNull();
  });

  it("offers Delete in the overflow for a backend-backed env and opens the confirm", async () => {
    const user = userEvent.setup();
    render(withRouter(<WorkspaceHeader {...baseProps} backed />));

    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(screen.getByRole("menuitem", { name: /Delete environment/ }));
    expect(screen.getByText("Delete environment?")).toBeInTheDocument();
  });

  it("omits Delete for a non-backed env (nothing to remove on the server)", async () => {
    const user = userEvent.setup();
    render(withRouter(<WorkspaceHeader {...baseProps} />));

    await user.click(screen.getByRole("button", { name: "More actions" }));
    expect(screen.queryByRole("menuitem", { name: /Delete environment/ })).toBeNull();
    expect(screen.getByRole("menuitem", { name: /Fork environment/ })).toBeInTheDocument();
  });

  it("forks from the overflow menu when unlocked", async () => {
    const user = userEvent.setup();
    const onFork = vi.fn();
    render(withRouter(<WorkspaceHeader {...baseProps} onFork={onFork} locked={false} />));

    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(screen.getByRole("menuitem", { name: /Fork environment/ }));
    expect(onFork).toHaveBeenCalledTimes(1);
  });

  it("opens the run-config dialog and starts a run-all (no ids) on confirm", async () => {
    const user = userEvent.setup();
    const onStartRun = vi.fn();
    render(withRouter(<WorkspaceHeader {...baseProps} canRun onStartRun={onStartRun} />));

    // The header Run opens the config dialog rather than navigating directly.
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    const dialog = screen.getByRole("dialog");
    // Confirm inside the dialog → run every scenario (ids undefined) × default k.
    await user.click(within(dialog).getByRole("button", { name: "Run simulation" }));
    expect(onStartRun).toHaveBeenCalledWith(undefined, 1);
  });

  it("hides the header Run + Repeats while a scenario selection is active", () => {
    render(withRouter(<WorkspaceHeader {...baseProps} canRun selectionActive />));
    expect(screen.queryByRole("button", { name: "Run simulation" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Repeats:/ })).toBeNull();
  });

  it("disables Run simulation with a reason tooltip when the env cannot run", async () => {
    const user = userEvent.setup();
    const reason = "Add scenarios on the Scenarios tab";
    render(
      withRouter(
        <WorkspaceHeader {...baseProps} canRun={false} runBlockedReason={reason} />
      )
    );

    const btn = screen.getByRole("button", { name: "Run simulation" });
    expect(btn).toBeDisabled();
    await user.hover(btn.parentElement);
    expect(await screen.findByText(reason)).toBeInTheDocument();
  });
});
