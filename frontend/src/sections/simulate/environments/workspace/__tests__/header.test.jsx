import { useRef, useState } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";

import WorkspaceHeader from "../WorkspaceHeader";
import EnvVersionPin from "../EnvVersionPin";
import SystemBanners from "../SystemBanners";

const ENV = {
  id: "env-1",
  name: "Refund Copilot",
  tagline: "Handles refund conversations",
  surface: "voice",
  buildStatus: "ready",
};

const withRouter = (ui) => <MemoryRouter>{ui}</MemoryRouter>;

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

  it("renders no building banner for a terminally failed build", () => {
    const { container } = render(
      <SystemBanners
        env={{ ...ENV, buildStatus: "failed", buildProgress: { done: 3, total: 7 } }}
        envState={{ scenarios: [1, 2, 3] }}
        patch={vi.fn()}
      />
    );
    expect(container).toBeEmptyDOMElement();
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

  it("shows Live only for a ready environment", () => {
    render(withRouter(<WorkspaceHeader {...baseProps} />));
    expect(screen.getByText("Live")).toBeInTheDocument();
  });

  it("shows Building, not Live, while the environment is still deriving", () => {
    render(withRouter(<WorkspaceHeader {...baseProps} env={{ ...ENV, buildStatus: "building" }} />));
    expect(screen.getByText("Building")).toBeInTheDocument();
    expect(screen.queryByText("Live")).toBeNull();
  });

  it("shows Failed, not Live, once the build has terminally failed", () => {
    render(withRouter(<WorkspaceHeader {...baseProps} env={{ ...ENV, buildStatus: "failed" }} />));
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.queryByText("Live")).toBeNull();
  });

  it("hides the overflow menu when locked", () => {
    render(withRouter(<WorkspaceHeader {...baseProps} locked />));
    expect(screen.queryByRole("button", { name: "More actions" })).toBeNull();
  });

  it("forks from the overflow menu when unlocked", async () => {
    const user = userEvent.setup();
    const onFork = vi.fn();
    render(withRouter(<WorkspaceHeader {...baseProps} onFork={onFork} locked={false} />));

    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(screen.getByRole("menuitem", { name: /Fork environment/ }));
    expect(onFork).toHaveBeenCalledTimes(1);
  });

  it("runs the product's run entry when the env can run and has no bridge ids", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/dashboard/simulate/environments/env-1"]}>
        <Routes>
          <Route
            path="/dashboard/simulate/environments/env-1"
            element={<WorkspaceHeader {...baseProps} canRun />}
          />
          <Route path="/dashboard/simulate/test" element={<div>run entry</div>} />
        </Routes>
      </MemoryRouter>
    );

    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(screen.getByText("run entry")).toBeInTheDocument();
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
